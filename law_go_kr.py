# -*- coding: utf-8 -*-
"""
law_go_kr.py — 국가법령정보센터 Open API(DRF) 클라이언트
nts-tax-mcp 확장 모듈: 대법원 판례 · 법령(현행+연혁) · 법령해석례

- 인증: 환경변수 LAW_API_OC(law.go.kr 가입 시 발급받는 기관코드) 필수. 등록된 IP에서만 동작.
- 응답: XML을 경량 파싱 (JSON 지원이 target마다 들쭉날쭉해 XML로 통일).
- 연혁 워크플로우(2026-07-21 확립): lawSearch target=eflaw로 시행본 목록
  → 특정 시행본 원문은 lawService target=law&MST=… → 조문은 <조문번호> 위치 기준 순차 슬라이스.
"""
import os
import re
import html
import time
import requests

BASE = "http://www.law.go.kr/DRF"
OC = os.environ.get("LAW_API_OC", "")
TIMEOUT = 20

# 같은 법령의 조문을 연속 조회하면 매번 법 전체 XML(소득세법 61만 자 등)을
# 다시 받게 되므로, 응답을 짧게 캐싱한다. 법령 개정은 실시간이 아니므로 안전.
_CACHE_TTL = 600   # 초
_CACHE_MAX = 16    # 전문 XML이 커서(수십만 자) 항목 수로 메모리 상한
_cache = {}        # key -> (만료시각, 응답텍스트)

_TAG_RE = re.compile(r"<([^/>\s]+)>\s*(.*?)\s*</\1>", re.S)


class LawGoKrError(Exception):
    pass


def _strip_cdata(s: str) -> str:
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s, flags=re.S)
    return html.unescape(s).strip()


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


def _get(endpoint: str, **params) -> str:
    if not OC:
        raise LawGoKrError("LAW_API_OC 환경변수가 설정되지 않았습니다 — law.go.kr에서 발급받은 기관코드(OC)를 설정하세요.")
    p = {"OC": OC, "type": "XML"}
    p.update({k: v for k, v in params.items() if v not in (None, "", 0)})
    key = (endpoint, tuple(sorted(p.items())))
    hit = _cache.get(key)
    now = time.time()
    if hit and hit[0] > now:
        return hit[1]
    r = requests.get(f"{BASE}/{endpoint}", params=p, timeout=TIMEOUT,
                     headers={"User-Agent": "nts-tax-mcp/1.0"})
    r.raise_for_status()
    text = r.text
    if "인증" in text[:500] and "실패" in text[:500]:
        raise LawGoKrError("law.go.kr 인증 실패 — OC 또는 IP 등록 확인 필요 (open.law.go.kr에서 현재 서버 IP 등록)")
    if len(_cache) >= _CACHE_MAX:
        del _cache[min(_cache, key=lambda k: _cache[k][0])]
    _cache[key] = (now + _CACHE_TTL, text)
    return text


def _parse_items(xml: str, item_tag: str):
    """<item_tag>…</item_tag> 블록마다 자식 태그를 dict로."""
    items = []
    for m in re.finditer(rf"<{item_tag}(?:\s[^>]*)?>(.*?)</{item_tag}>", xml, re.S):
        block = m.group(1)
        d = {}
        for tag, val in _TAG_RE.findall(block):
            d[tag] = _strip_cdata(val)
        if d:
            items.append(d)
    return items


class LawGoKrClient:
    # ---------- 판례 (법제처 제공 대법원·하급심) ----------
    def search_cases(self, keyword: str, court: str = "", date_from: str = "",
                     date_to: str = "", display: int = 10, page: int = 1):
        """target=prec 판례 검색. court: '대법원' 또는 '하위법원'(빈값=전체).
        date_from/date_to: YYYYMMDD (선고일자 범위)."""
        params = dict(target="prec", query=keyword, display=display, page=page, search=2)
        if court:
            params["curt"] = court
        if date_from or date_to:
            params["prncYd"] = f"{date_from or '19450815'}~{date_to or '20991231'}"
        xml = _get("lawSearch.do", **params)
        items = _parse_items(xml, "prec")
        total = re.search(r"<totalCnt>(\d+)</totalCnt>", xml)
        return {
            "total": int(total.group(1)) if total else len(items),
            "cases": [{
                "판례일련번호": it.get("판례일련번호", ""),
                "사건명": it.get("사건명", ""),
                "사건번호": it.get("사건번호", ""),
                "법원명": it.get("법원명", ""),
                "선고일자": it.get("선고일자", ""),
                "판결유형": it.get("판결유형", ""),
                "사건종류명": it.get("사건종류명", ""),
            } for it in items],
        }

    def get_case(self, case_serial: str, max_chars: int = 8000):
        """target=prec 판례 본문. case_serial = 판례일련번호."""
        xml = _get("lawService.do", target="prec", ID=case_serial)
        d = {}
        for tag in ("사건명", "사건번호", "법원명", "선고일자", "판시사항", "판결요지", "참조조문", "참조판례", "판례내용"):
            m = re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.S)
            if m:
                d[tag] = _strip_tags(_strip_cdata(m.group(1)))[: max_chars if tag == "판례내용" else 4000]
        if not d:
            raise LawGoKrError(f"판례 본문 없음 (일련번호 {case_serial}) — 응답 앞부분: {xml[:200]}")
        return d

    # ---------- 법령해석례 (법제처) ----------
    def search_interpretations(self, keyword: str, display: int = 10, page: int = 1):
        """target=expc 법령해석례 검색."""
        xml = _get("lawSearch.do", target="expc", query=keyword, display=display, page=page)
        items = _parse_items(xml, "expc")
        return [{
            "해석례일련번호": it.get("법령해석례일련번호", it.get("일련번호", "")),
            "안건명": it.get("안건명", ""),
            "안건번호": it.get("안건번호", ""),
            "회신기관": it.get("회신기관명", it.get("질의기관명", "")),
            "회신일자": it.get("회신일자", ""),
        } for it in items]

    def get_interpretation(self, serial: str, max_chars: int = 8000):
        xml = _get("lawService.do", target="expc", ID=serial)
        d = {}
        for tag in ("안건명", "안건번호", "회신일자", "질의요지", "회답", "이유"):
            m = re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.S)
            if m:
                d[tag] = _strip_tags(_strip_cdata(m.group(1)))[:max_chars]
        return d or {"오류": "본문 없음", "응답": xml[:200]}

    # ---------- 법령: 현행 검색 ----------
    def search_laws(self, law_name: str, display: int = 20):
        """target=law 현행 법령 검색 → 법령ID·MST(법령일련번호)·시행일자."""
        xml = _get("lawSearch.do", target="law", query=law_name, display=display)
        items = _parse_items(xml, "law")
        return [{
            "법령명": it.get("법령명한글", ""),
            "법령ID": it.get("법령ID", ""),
            "MST": it.get("법령일련번호", ""),
            "시행일자": it.get("시행일자", ""),
            "공포일자": it.get("공포일자", ""),
            "공포번호": it.get("공포번호", ""),
            "제개정구분": it.get("제개정구분명", ""),
        } for it in items]

    # ---------- 법령: 연혁(시행본) 목록 ----------
    def law_history(self, law_name: str, law_id: str = "", max_rows: int = 100):
        """target=eflaw 연혁 시행본 전체 목록. law_id를 주면 그 본법만 필터
        (예: 부가가치세법=001571 — 같은 이름 하위법령 혼입 방지)."""
        rows = []
        page = 1
        while len(rows) < max_rows and page <= 5:
            # 페이지 크기 파라미터는 display가 맞음 — numOfRows는 무시되어 20건씩 5왕복하게 됨 (2026-08-18 실측)
            xml = _get("lawSearch.do", target="eflaw", query=law_name, display=100, page=page)
            items = _parse_items(xml, "law")
            if not items:
                break
            for it in items:
                if law_id and it.get("법령ID", "") != law_id:
                    continue
                rows.append({
                    "법령명": it.get("법령명한글", ""),
                    "법령ID": it.get("법령ID", ""),
                    "MST": it.get("법령일련번호", ""),
                    "시행일자": it.get("시행일자", ""),
                    "공포일자": it.get("공포일자", ""),
                    "공포번호": it.get("공포번호", ""),
                    "제개정구분": it.get("제개정구분명", ""),
                })
            page += 1
        rows.sort(key=lambda r: r.get("시행일자", ""))
        return rows

    # ---------- 법령: 특정 시행본의 조문 원문 ----------
    def law_article(self, mst: str, article_no: str, max_chars: int = 6000):
        """lawService target=law&MST=… 원문에서 조번호 슬라이스.
        article_no: '17' 또는 '17의2' (패딩 없음)."""
        xml = _get("lawService.do", target="law", MST=mst)
        # 조문 뒤에 <부칙> 섹션(수십만 자)이 이어지므로 슬라이스 범위에서 잘라낸다 —
        # 안 자르면 마지막 조문 조회 시 부칙 전체가 본문에 딸려 나온다. 부칙은 law_addenda로.
        cut = xml.find("<부칙>")
        if cut != -1:
            xml = xml[:cut]
        base_no, branch_no = article_no, None
        m = re.match(r"^(\d+)의(\d+)$", article_no.strip())
        if m:
            base_no, branch_no = m.group(1), m.group(2)
        # <조문번호> 위치 순차 슬라이스 (단순 <조문>…</조문> 정규식은 실패 — 2026-07-21 확인)
        positions = [(mm.start(), mm.group(1)) for mm in re.finditer(r"<조문번호>(\d+)</조문번호>", xml)]
        for i, (pos, no) in enumerate(positions):
            if no != base_no.strip():
                continue
            end = positions[i + 1][0] if i + 1 < len(positions) else len(xml)
            block = xml[pos:end]
            bm = re.search(r"<조문가지번호>(\d+)</조문가지번호>", block[:300])
            blk_branch = bm.group(1) if bm else None
            if branch_no != blk_branch:
                continue
            body = _strip_tags(_strip_cdata(block))
            label = f"제{base_no}조" + (f"의{branch_no}" if branch_no else "")
            # 절/관/장이 이 조문에서 시작되면 그 표제가 동일 <조문번호>를 단 채
            # 실제 조문보다 먼저 나온다(예: 제104조 앞의 "제6절 …" 노드) — 표제 블록은
            # 조번호 문자열 자체를 포함하지 않으므로 걸러내고 다음 후보를 찾는다.
            if label not in body:
                continue
            title = re.search(r"<조문제목>(.*?)</조문제목>", block, re.S)
            out = {
                "조문": label,
                "조문제목": _strip_cdata(title.group(1)) if title else "",
            }
            # 조문단위마다 개별 시행일자가 달려 있다(같은 법이라도 조문별 시행일이 다를 수 있음)
            em = re.search(r"<조문시행일자>(\d+)</조문시행일자>", block)
            if em:
                out["조문시행일자"] = em.group(1)
            out["원문"] = body[:max_chars]
            if len(body) > max_chars:
                out["잘림"] = (f"전체 {len(body)}자 중 앞 {max_chars}자만 표시됨 — "
                             f"max_chars를 {len(body)} 이상으로 지정해 다시 조회하면 전문을 볼 수 있음")
            return out
        raise LawGoKrError(f"MST {mst}에서 제{article_no}조를 찾지 못함")

    # ---------- 부칙 (시행일·적용례·경과조치) ----------
    @staticmethod
    def _split_addendum(text: str):
        """부칙 본문을 '제N조(…)' 항 단위로 분할. 조 편제 없는 단순 부칙은 통짜 1개.
        구식 '제1조 (시행일)'처럼 조와 괄호 사이 공백이 있는 표기도 같은 앵커로 잡힌다."""
        starts = [m.start() for m in re.finditer(r"(?m)^제\d+조(?:의\d+)?\s*\(", text)]
        if not starts:
            t = text.strip()
            return [t] if t else []
        parts = []
        head = text[:starts[0]].strip()
        if head:
            parts.append(head)  # "부칙 <제21221호,2025.12.23>" 머리말
        for i, s in enumerate(starts):
            e = starts[i + 1] if i + 1 < len(starts) else len(text)
            parts.append(text[s:e].strip())
        return parts

    @staticmethod
    def _effective_summary(body: str, limit: int = 350):
        """부칙에서 시행일 부분만 요약 — 첫 '시행한다' 줄 + '각 호' 단서면 이어지는 호 목록."""
        m = re.search(r"(?m)^\s*(.*?시행한다.*)$", body)
        if not m:
            first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
            return first[:limit]
        lines = [m.group(1).strip()]
        if "각 호" in lines[0]:
            for ln in body[m.end():].splitlines():
                t = ln.strip()
                if not t:
                    continue
                if re.match(r"^\d+\.", t):
                    lines.append(t)
                else:
                    break
        out = " ".join(lines)
        return out[:limit] + ("…" if len(out) > limit else "")

    def law_addenda(self, mst: str = "", law_name: str = "", law_id: str = "",
                    as_of_date: str = "", promul_no: str = "", article_no: str = "",
                    recent: int = 10, max_chars: int = 8000):
        """법령 부칙(附則) 조회 — 개정규정의 시행일·적용례·경과조치.
        lawService target=law 응답의 <부칙단위> 목록을 파싱한다 (2026-08-26 실측:
        소득세법 현행본에 부칙 114건, 각각 공포일자·공포번호·본문 보유).

        - mst 미지정 시 law_name으로 시행본 자동 선택 (as_of_date 주면 그 시점본, 없으면 현행)
        - promul_no: 그 공포번호 개정의 부칙 전문 (공포번호는 law_history_search 목록에 있음)
        - article_no: 전체 부칙에서 그 조문이 언급된 항(적용례·경과조치)만 최신순 발췌
        - 둘 다 없으면: 최신순 recent건 목록 (공포일자·공포번호·시행일 요약)
        """
        if not mst:
            if not law_name:
                raise LawGoKrError("mst 또는 law_name 중 하나는 필요합니다")
            if as_of_date:
                history = self.law_history(law_name, law_id=law_id)
                chosen = None
                for row in history:  # 시행일자 오름차순
                    if row["시행일자"] and row["시행일자"] <= as_of_date:
                        chosen = row
                if not chosen:
                    raise LawGoKrError(f"{as_of_date} 이전 시행본 없음: {law_name}")
            else:
                rows = self.search_laws(law_name)
                if law_id:
                    rows = [r for r in rows if r.get("법령ID") == law_id]
                exact = [r for r in rows if r.get("법령명") == law_name.strip()]
                pick = exact or rows  # "소득세법" 검색에 시행령·시행규칙이 섞여 나와 정확 일치 우선
                if not pick:
                    raise LawGoKrError(f"법령 검색 결과 없음: {law_name}")
                chosen = pick[0]
            mst = chosen["MST"]
        xml = _get("lawService.do", target="law", MST=mst)
        name = re.search(r"<법령명_한글>(.*?)</법령명_한글>", xml, re.S)
        enf = re.search(r"<시행일자>(\d+)</시행일자>", xml)
        units = []
        for m in re.finditer(r"<부칙단위[^>]*>(.*?)</부칙단위>", xml, re.S):
            blk = m.group(1)
            dt = re.search(r"<부칙공포일자>(\d+)</부칙공포일자>", blk)
            no = re.search(r"<부칙공포번호>(\d+)</부칙공포번호>", blk)
            bd = re.search(r"<부칙내용>(.*?)</부칙내용>", blk, re.S)
            body = _strip_cdata(bd.group(1)) if bd else ""
            # 머리말 "부칙 <제21221호,2025.12.23>"의 꺾쇠가 태그 제거에 지워지지 않게 보존
            body = re.sub(r"<(제\d+호[^>]*?)>", r"〈\1〉", body)
            body = _strip_tags(body)
            body = re.sub(r"[ \t]+\n", "\n", body)
            body = re.sub(r"\n{2,}", "\n", body)  # CDATA 조각 사이 빈 줄 압축
            units.append({
                "공포일자": dt.group(1) if dt else "",
                "공포번호": no.group(1).lstrip("0") if no else "",  # "04803" 식 패딩 제거
                "본문": body,
            })
        if not units:
            raise LawGoKrError(f"MST {mst} 응답에 부칙 없음 — 응답 앞부분: {xml[:200]}")
        out = {
            "법령명": _strip_cdata(name.group(1)) if name else "",
            "MST": mst,
            "시행본_시행일자": enf.group(1) if enf else "",
            "부칙총수": len(units),
        }
        # 조문별 시행일이 다른 개정이면 그 내역이 헤더에 요약돼 있다
        # (예: "20260701:제57조의2, … 20270101:제17조제3항, …")
        multi = re.search(r"<조문시행일자문자열>(.*?)</조문시행일자문자열>", xml, re.S)
        multi_txt = _strip_cdata(multi.group(1)) if multi else ""
        if multi_txt:
            out["조문별_상이한_시행일"] = multi_txt

        if promul_no:
            want = promul_no.strip().lstrip("0") or promul_no.strip()
            hits = [u for u in units if u["공포번호"] == want]
            if not hits:
                raise LawGoKrError(
                    f"공포번호 {promul_no}의 부칙이 이 시행본에 없음 (부칙 {len(units)}건 보유) — "
                    f"law_history_search로 공포번호를 확인하세요")
            u = hits[-1]
            body = u["본문"]
            out["부칙"] = {"공포일자": u["공포일자"], "공포번호": u["공포번호"],
                          "본문": body[:max_chars]}
            if len(body) > max_chars:
                out["잘림"] = (f"이 부칙 전체 {len(body)}자 중 앞 {max_chars}자만 표시됨 — "
                             f"max_chars를 {len(body)} 이상으로 지정해 다시 조회")
            return out

        if article_no:
            a = article_no.strip()
            am = re.match(r"^(\d+)(?:의(\d+))?$", a)
            if not am:
                raise LawGoKrError(f"article_no 형식 오류: '{a}' (예: '96', '104의3')")
            label = f"제{am.group(1)}조" + (f"의{am.group(2)}" if am.group(2) else "")
            # '제96조' 검색이 '제96조의2' 언급에 오매치되지 않게 가지조문 아니면 (?!의) 가드
            pat = re.compile(re.escape(label) + ("" if am.group(2) else r"(?!의)"))
            found, used = [], 0
            for u in reversed(units):  # 최신 개정부터
                paras = [p[:1500] for p in self._split_addendum(u["본문"]) if pat.search(p)]
                if not paras:
                    continue
                size = sum(len(p) for p in paras)
                if found and used + size > max_chars:
                    out["잘림"] = (f"{label} 언급 부칙이 더 있으나 max_chars({max_chars}) 초과로 "
                                 f"최신 {len(found)}건까지만 표시 — max_chars를 늘려 다시 조회")
                    break
                used += size
                found.append({"공포일자": u["공포일자"], "공포번호": u["공포번호"],
                              "해당항": paras})
            out["조문"] = label
            out["언급된_부칙_최신순"] = found
            if not found:
                out["안내"] = (f"{label}이(가) 언급된 부칙 조항 없음 — 그 조문 개정에 별도 "
                             f"적용례·경과조치가 없었다는 뜻일 수 있음 (이때 시행일은 각 시행본의 "
                             f"시행일자·부칙 제1조를 따름)")
            return out

        rows = []
        for u in reversed(units):  # 최신순
            rows.append({"공포일자": u["공포일자"], "공포번호": u["공포번호"],
                         "시행일": self._effective_summary(u["본문"])})
            if len(rows) >= max(1, recent):
                break
        out["부칙목록_최신순"] = rows
        if len(units) > len(rows):
            out["안내"] = (f"전체 {len(units)}건 중 최신 {len(rows)}건만 표시 — recent를 늘리거나, "
                         f"특정 개정의 부칙 전문은 promul_no로, 특정 조문의 적용시기는 "
                         f"article_no로 조회")
        return out

    # ---------- 행정규칙 (훈령·예규·고시·기본통칙) ----------
    def search_admin_rules(self, keyword: str, display: int = 10, page: int = 1):
        """target=admrul 행정규칙 검색 — 기본통칙·조사사무처리규정·고시 등."""
        xml = _get("lawSearch.do", target="admrul", query=keyword, display=display, page=page)
        items = _parse_items(xml, "admrul")
        return [{
            "일련번호": it.get("행정규칙일련번호", ""),
            "행정규칙명": it.get("행정규칙명", ""),
            "종류": it.get("행정규칙종류", ""),
            "소관부처": it.get("소관부처명", ""),
            "발령일자": it.get("발령일자", ""),
            "발령번호": it.get("발령번호", ""),
            "시행일자": it.get("시행일자", ""),
        } for it in items]

    def get_admin_rule(self, serial: str, max_chars: int = 10000,
                       article: str = "", start_char: int = 0):
        """target=admrul 행정규칙 본문.
        article: 조번호를 주면 해당 조문만 슬라이스 — "9-5"(외국환거래규정식 제9-5조),
        "23", "23의2" 형식. 외국환거래규정(30만 자) 같은 대형 고시는 전문 반환이
        불가능하므로 조번호 지정이 사실상 필수. start_char: 조번호를 모를 때
        해당 위치부터 max_chars만큼 이어 읽는 오프셋."""
        xml = _get("lawService.do", target="admrul", ID=serial)
        name = re.search(r"<행정규칙명>(.*?)</행정규칙명>", xml, re.S)
        body = _strip_tags(_strip_cdata(xml))
        if len(body) < 50:
            return {"오류": "본문 없음", "응답": xml[:200]}
        out = {
            "행정규칙명": _strip_cdata(name.group(1)) if name else "",
            "본문길이": len(body),
        }
        if article:
            a = article.strip()
            am = re.match(r"^([\d-]+)(의\d+)?$", a)
            base, ui = (am.group(1), am.group(2) or "") if am else (a, "")
            # 조문 표제는 행 첫머리 "제9-5조(제목)" 형태 — 본문 중 인용("제7-31조의 규정 …")과
            # 행 앵커로 구분한다. 표제가 행 중간에 오는 규칙이면 못 찾으므로 start_char로 폴백.
            m = re.search(rf"(?m)^제{re.escape(base)}조{ui}(?=\(|\s|$)", body)
            if not m:
                out["오류"] = (f"제{a}조 표제를 찾지 못함 — 조번호 형식(예: '9-5', '23', '23의2')을 "
                             f"확인하거나 start_char 오프셋으로 조회")
                return out
            nm = re.search(r"(?m)^제[\d-]+조(?:의\d+)?\s*(?=\()", body[m.end():])
            seg = body[m.start():m.end() + nm.start()] if nm else body[m.start():]
            out["조문"] = f"제{a}조"
            out["본문"] = seg[:max_chars]
            if len(seg) > max_chars:
                out["잘림"] = (f"이 조문 전체 {len(seg)}자 중 앞 {max_chars}자만 표시됨 — "
                             f"max_chars를 {len(seg)} 이상으로 지정해 다시 조회")
            return out
        window = body[start_char:start_char + max_chars]
        out["본문"] = window
        if start_char + len(window) < len(body):
            out["잘림"] = (f"전체 {len(body)}자 중 {start_char}~{start_char + len(window)}자 구간만 표시됨 — "
                         f"start_char={start_char + len(window)}로 이어서 조회하거나, "
                         f"article에 조번호(예: '9-5')를 지정하면 해당 조문만 반환")
        return out

    # ---------- 조세조약 등 조약 ----------
    def search_treaties(self, keyword: str, display: int = 10, page: int = 1):
        """target=trty 조약 검색 — 조세조약 원문·발효일 확인용."""
        xml = _get("lawSearch.do", target="trty", query=keyword, display=display, page=page)
        items = _parse_items(xml, "Trty") + _parse_items(xml, "trty")
        return [{
            "조약일련번호": it.get("조약일련번호", ""),
            "조약명": it.get("조약명한글", it.get("조약명", "")),
            "조약구분": it.get("조약구분명", ""),
            "서명일자": it.get("서명일자", ""),
            "발효일자": it.get("발효일자", ""),
        } for it in items]

    def get_treaty(self, serial: str, max_chars: int = 12000):
        """target=trty 조약 본문."""
        xml = _get("lawService.do", target="trty", ID=serial)
        name = re.search(r"<조약명한글>(.*?)</조약명한글>", xml, re.S)
        body = _strip_tags(_strip_cdata(xml))
        if len(body) < 50:
            return {"오류": "본문 없음", "응답": xml[:200]}
        return {
            "조약명": _strip_cdata(name.group(1)) if name else "",
            "본문": body[:max_chars],
            "본문길이": len(body),
        }

    # ---------- 자치법규 (조례·규칙) ----------
    def search_ordinances(self, keyword: str, region: str = "", display: int = 20, page: int = 1):
        """target=ordin 자치법규 검색. region으로 지자체명 필터 (예: '서울', '용산구')."""
        xml = _get("lawSearch.do", target="ordin", query=keyword, display=display, page=page)
        items = _parse_items(xml, "law") + _parse_items(xml, "ordin")
        rows = []
        for it in items:
            org = it.get("지자체기관명", it.get("소관부처명", ""))
            if region and region not in org:
                continue
            rows.append({
                "일련번호": it.get("자치법규일련번호", it.get("법령일련번호", "")),
                "자치법규명": it.get("자치법규명", it.get("법령명한글", "")),
                "지자체": org,
                "공포일자": it.get("공포일자", ""),
                "시행일자": it.get("시행일자", ""),
                "제개정구분": it.get("제개정구분명", ""),
            })
        return rows

    def get_ordinance(self, serial: str, max_chars: int = 10000):
        """target=ordin 자치법규 본문."""
        xml = _get("lawService.do", target="ordin", ID=serial)
        name = re.search(r"<자치법규명>(.*?)</자치법규명>", xml, re.S)
        body = _strip_tags(_strip_cdata(xml))
        if len(body) < 50:
            return {"오류": "본문 없음", "응답": xml[:200]}
        return {
            "자치법규명": _strip_cdata(name.group(1)) if name else "",
            "본문": body[:max_chars],
            "본문길이": len(body),
        }

    # ---------- 편의: 특정 날짜 시행본의 조문 (예규 당시 조문 확인용) ----------
    def law_article_as_of(self, law_name: str, as_of_date: str, article_no: str, law_id: str = "",
                          max_chars: int = 6000):
        """as_of_date(YYYYMMDD) 당시 시행 중이던 시행본을 골라 해당 조문 원문 반환.
        예규·판례가 인용한 '당시 조문' 검증용 — 회신일을 넣으면 그 시점 법을 준다."""
        history = self.law_history(law_name, law_id=law_id)
        if not history:
            raise LawGoKrError(f"연혁 없음: {law_name}")
        chosen = None
        for row in history:  # 시행일자 오름차순
            if row["시행일자"] and row["시행일자"] <= as_of_date:
                chosen = row
        if not chosen:
            raise LawGoKrError(f"{as_of_date} 이전 시행본 없음 (최초 시행 {history[0]['시행일자']})")
        art = self.law_article(chosen["MST"], article_no, max_chars)
        art["적용시행본"] = {k: chosen[k] for k in ("법령명", "시행일자", "공포일자", "공포번호", "제개정구분", "MST")}
        return art
