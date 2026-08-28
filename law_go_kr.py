# -*- coding: utf-8 -*-
"""
law_go_kr.py — 국가법령정보센터 Open API(DRF) 클라이언트
nts-tax-mcp 확장 모듈: 대법원 판례 · 법령(현행+연혁) · 법령해석례

- 인증: 환경변수 LAW_API_OC(law.go.kr 가입 시 발급받는 기관코드) 필수. 등록된 IP에서만 동작.
- 응답: XML을 경량 파싱 (JSON 지원이 target마다 들쭉날쭉해 XML로 통일).
- 연혁 워크플로우(2026-07-21 확립): lawSearch target=eflaw로 시행본 목록
  → 특정 시행본 원문은 lawService target=law&MST=… → 조문은 <조문번호> 위치 기준 순차 슬라이스.
"""
import difflib
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
    """입력 오류 등 일반 오류 (호출자가 INVALID_INPUT으로 분류)"""


class LawGoKrAuthError(LawGoKrError):
    """OC 미설정·IP 미등록 등 인증 실패 — 자료 부존재와 무관"""


class LawGoKrNotFound(LawGoKrError):
    """검색·조회는 성공했으나 해당 자료가 원천에 없음"""


def _strip_cdata(s: str) -> str:
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s, flags=re.S)
    return html.unescape(s).strip()


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


def _get(endpoint: str, **params) -> str:
    if not OC:
        raise LawGoKrAuthError("LAW_API_OC 환경변수가 설정되지 않았습니다 — law.go.kr에서 발급받은 기관코드(OC)를 설정하세요.")
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
        raise LawGoKrAuthError("law.go.kr 인증 실패 — OC 또는 IP 등록 확인 필요 (open.law.go.kr에서 현재 서버 IP 등록)")
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
        out = {
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
        if not out["cases"]:
            out["status"] = "NOT_FOUND"
        return out

    def get_case(self, case_serial: str, max_chars: int = 8000):
        """target=prec 판례 본문. case_serial = 판례일련번호."""
        xml = _get("lawService.do", target="prec", ID=case_serial)
        d = {}
        for tag in ("사건명", "사건번호", "법원명", "선고일자", "판시사항", "판결요지", "참조조문", "참조판례", "판례내용"):
            m = re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.S)
            if m:
                d[tag] = _strip_tags(_strip_cdata(m.group(1)))[: max_chars if tag == "판례내용" else 4000]
        if not d:
            raise LawGoKrNotFound(f"판례 본문 없음 (일련번호 {case_serial}) — 응답 앞부분: {xml[:200]}")
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
        if not d:
            raise LawGoKrNotFound(f"법령해석례 본문 없음 (일련번호 {serial}) — 응답 앞부분: {xml[:200]}")
        return d

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
    def law_history(self, law_name: str, law_id: str = "", max_rows: int = 500):
        """target=eflaw 연혁 시행본 전체 목록. law_id를 주면 그 본법만 필터
        (예: 부가가치세법=001571 — 같은 이름 하위법령 혼입 방지).

        max_rows 기본 500: 소득세법처럼 시행본이 많은 법령은 100행에서 끊으면
        2011년 이전 연혁이 통째로 누락되어 law_article_as_of·law_article_diff가
        과거 시점을 조회하지 못한다 (2026-08-28 실측)."""
        rows = []
        page = 1
        while len(rows) < max_rows and page <= 10:
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
        # 같은 시행일자에 여러 공포본이 오는 일이 흔하다 — 조문별 단계 시행 때문에
        # 옛 공포본이 미래 시행일자 행으로 재등장하기 때문(2026-08-28 실측: 소득세법
        # 시행일자 20250101에 MST 6개). 시행일자만으로 정렬하면 '기준일 이하 마지막 행'을
        # 고르는 쪽(law_article_as_of·law_article_diff)이 그 그룹에서 가장 오래된 공포본을
        # 집어 옛 문안을 반환하므로, 공포일자·공포번호까지 정렬 키에 넣는다.
        rows.sort(key=lambda r: (r.get("시행일자", ""), r.get("공포일자", ""),
                                 (r.get("공포번호", "") or "").zfill(10)))
        return rows

    # ---------- 법령: 특정 시행본의 조문 원문 ----------
    @staticmethod
    def _find_article_block(xml: str, article_no: str):
        """법령 원문 XML에서 조문 블록을 슬라이스. 반환 (조문 라벨, 블록 or None).
        law_article(원문 반환)와 law_article_diff(구조적 비교)가 공유한다."""
        # 조문 뒤에 <부칙> 섹션(수십만 자)이 이어지므로 슬라이스 범위에서 잘라낸다 —
        # 안 자르면 마지막 조문 조회 시 부칙 전체가 본문에 딸려 나온다. 부칙은 law_addenda로.
        cut = xml.find("<부칙>")
        if cut != -1:
            xml = xml[:cut]
        base_no, branch_no = article_no.strip(), None
        m = re.match(r"^(\d+)의(\d+)$", article_no.strip())
        if m:
            base_no, branch_no = m.group(1), m.group(2)
        label = f"제{base_no}조" + (f"의{branch_no}" if branch_no else "")
        # <조문번호> 위치 순차 슬라이스 (단순 <조문>…</조문> 정규식은 실패 — 2026-07-21 확인)
        positions = [(mm.start(), mm.group(1)) for mm in re.finditer(r"<조문번호>(\d+)</조문번호>", xml)]
        for i, (pos, no) in enumerate(positions):
            if no != base_no:
                continue
            end = positions[i + 1][0] if i + 1 < len(positions) else len(xml)
            block = xml[pos:end]
            bm = re.search(r"<조문가지번호>(\d+)</조문가지번호>", block[:300])
            blk_branch = bm.group(1) if bm else None
            if branch_no != blk_branch:
                continue
            # 절/관/장이 이 조문에서 시작되면 그 표제가 동일 <조문번호>를 단 채
            # 실제 조문보다 먼저 나온다(예: 제104조 앞의 "제6절 …" 노드) — 표제 블록은
            # 조번호 문자열 자체를 포함하지 않으므로 걸러내고 다음 후보를 찾는다.
            if label not in _strip_tags(_strip_cdata(block)):
                continue
            return label, block
        return label, None

    def law_article(self, mst: str, article_no: str, max_chars: int = 6000):
        """lawService target=law&MST=… 원문에서 조번호 슬라이스.
        article_no: '17' 또는 '17의2' (패딩 없음)."""
        xml = _get("lawService.do", target="law", MST=mst)
        label, block = self._find_article_block(xml, article_no)
        if block is None:
            raise LawGoKrNotFound(f"MST {mst}에서 제{article_no}조를 찾지 못함")
        body = _strip_tags(_strip_cdata(block))
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

    # ---------- 부칙 (시행일·적용례·경과조치) ----------
    @staticmethod
    def _parse_addenda_units(xml: str):
        """lawService target=law 응답 XML에서 <부칙단위> 목록을 파싱.
        law_addenda와 law_article_diff(개정 부칙 연결)가 공유한다."""
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
        return units

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
        if as_of_date and not re.match(r"^\d{8}$", str(as_of_date)):
            raise LawGoKrError(f"as_of_date 형식 오류: '{as_of_date}' (YYYYMMDD 8자리)")
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
                    raise LawGoKrNotFound(f"{as_of_date} 이전 시행본 없음: {law_name}")
            else:
                rows = self.search_laws(law_name)
                if law_id:
                    rows = [r for r in rows if r.get("법령ID") == law_id]
                exact = [r for r in rows if r.get("법령명") == law_name.strip()]
                pick = exact or rows  # "소득세법" 검색에 시행령·시행규칙이 섞여 나와 정확 일치 우선
                if not pick:
                    raise LawGoKrNotFound(f"법령 검색 결과 없음: {law_name}")
                chosen = pick[0]
            mst = chosen["MST"]
        xml = _get("lawService.do", target="law", MST=mst)
        name = re.search(r"<법령명_한글>(.*?)</법령명_한글>", xml, re.S)
        enf = re.search(r"<시행일자>(\d+)</시행일자>", xml)
        units = self._parse_addenda_units(xml)
        if not units:
            raise LawGoKrNotFound(f"MST {mst} 응답에 부칙 없음 — 응답 앞부분: {xml[:200]}")
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
                raise LawGoKrNotFound(
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
        if not re.match(r"^\d{8}$", str(as_of_date or "")):
            raise LawGoKrError(f"as_of_date 형식 오류: '{as_of_date}' (YYYYMMDD 8자리)")
        history = self.law_history(law_name, law_id=law_id)
        if not history:
            raise LawGoKrNotFound(f"연혁 없음: {law_name}")
        # 시행본 시행일자만 보면 조문별 단계 시행에서 아직 발효 전인 문안을 집는다
        chosen, _lines, art_date = self._pick_effective_version(history, as_of_date, article_no)
        if not chosen:
            raise LawGoKrNotFound(f"{as_of_date} 이전 시행본 없음 (최초 시행 {history[0]['시행일자']})")
        art = self.law_article(chosen["MST"], article_no, max_chars)
        art["적용시행본"] = {k: chosen[k] for k in ("법령명", "시행일자", "공포일자", "공포번호", "제개정구분", "MST")}
        if art_date and art_date > as_of_date:
            art["주의"] = (f"이 시행본에 수록된 문안의 조문시행일자({art_date})가 기준일"
                          f"({as_of_date})보다 미래입니다 — 기준일 당시 시행 중이던 문안을 "
                          f"찾지 못했을 수 있으니 law_article_diff로 개정 시점을 확인하세요")
        return art

    # ---------- 조문 개정 diff (신구조문 대비) ----------
    _CONTENT_TAG_RE = re.compile(r"<(조문내용|항내용|호내용|목내용)>(.*?)</\1>", re.S)
    _BACKTRACK_MAX = 8  # 단계 시행 소급 탐색 상한 (시행본 XML 재요청 왕복 제한)

    def _article_state(self, mst: str, article_no: str):
        """(문안 줄 목록 or None, 조문시행일자) — 조문이 없으면 (None, "")."""
        xml = _get("lawService.do", target="law", MST=mst)
        _label, block = self._find_article_block(xml, article_no)
        if block is None:
            return None, ""
        em = re.search(r"<조문시행일자>(\d+)</조문시행일자>", block)
        return self._article_content_lines(block), (em.group(1) if em else "")

    def _pick_effective_version(self, history, date: str, article_no: str, state_fn=None):
        """기준일에 '실제로 시행 중이던' 조문 문안을 담은 시행본을 고른다.

        시행본 시행일자만 보고 고르면 조문별 단계 시행에서 아직 발효 전인 문안을
        집는다 — 같은 공포본(MST)이 여러 시행일자 행으로 연혁에 나타나고, 그 MST의
        XML은 미래 시행 조문의 문안까지 담고 있기 때문(2026-08-28 실측: 소득세법
        현행본 헤더 <조문시행일자문자열> = "20260701:제57조의2 … 20270101:제17조제3항 …").
        후보의 조문시행일자가 기준일보다 미래면 이전 시행본으로 소급한다.

        반환: (선택된 연혁 행, 문안 줄 목록 or None, 조문시행일자). 후보가 없으면
        (None, None, ""). 조문 자체가 없는 시점이면 문안이 None (= 신설 전)."""
        get_state = state_fn or (lambda mst: self._article_state(mst, article_no))
        cands = [r for r in history if r.get("시행일자") and r["시행일자"] <= date]
        if not cands:
            return None, None, ""
        tried = set()
        for row in reversed(cands[-self._BACKTRACK_MAX:]):
            if row["MST"] in tried:  # 같은 MST의 중복 시행일자 행은 한 번만 조회
                continue
            tried.add(row["MST"])
            lines, art_date = get_state(row["MST"])
            if lines is None:
                return row, None, ""  # 그 시점엔 조문 없음 (신설 전) — 소급 불필요
            if art_date and art_date > date:
                continue  # 아직 발효 전 문안 — 더 과거 시행본으로
            return row, lines, art_date
        row = cands[0]  # 소급 상한 내 전부 미래 문안 (비정상) — 최고(最古) 후보로 폴백
        lines, art_date = get_state(row["MST"])
        return row, lines, art_date

    @classmethod
    def _article_content_lines(cls, block: str):
        """diff 비교용: 조문 블록에서 실제 문안(조문내용·항·호·목)만 줄 단위로 추출.
        <조문키>·<조문시행일자> 같은 메타데이터와 번호 태그 중복(①/① 본문)을 제외해야
        문안이 같은데 메타데이터만 다른 시행본이 '변경'으로 오판되지 않는다."""
        lines = []
        for _tag, val in cls._CONTENT_TAG_RE.findall(block):
            t = _strip_cdata(val)
            # "<개정 2024.12.31>" 류 주석이 태그 제거에 지워지지 않게 보존 — 개정 이력 자체가 유용한 정보
            t = re.sub(r"<((?:개정|신설|삭제)[^>]*?)>", r"〈\1〉", t)
            t = _strip_tags(t)
            for ln in t.splitlines():
                ln = ln.strip()
                if ln:
                    lines.append(ln)
        return lines

    def law_article_diff(self, law_name: str, article_no: str, date_from: str,
                         date_to: str = "", law_id: str = "", max_chars: int = 8000,
                         find_change: bool = True):
        """두 시점의 조문 문안을 비교(신구조문 대비)하고, 현행(B) 문안이 언제부터
        시행됐는지와 그 개정의 부칙(시행일·적용례)까지 연결해 반환한다.

        - date_from(A)·date_to(B, 생략 시 오늘) 각각의 시행본을 자동 선택해 조문을 대비
        - find_change=True면 A~B 사이 시행본을 이진탐색해 B 문안이 최초로 등장한
          시행본을 특정하고, 그 개정 부칙에서 이 조문이 언급된 적용례를 발췌
        """
        if not date_from:
            raise LawGoKrError("date_from은 필수입니다 (YYYYMMDD 8자리)")
        for d, nm in ((date_from, "date_from"), (date_to, "date_to")):
            if d and not re.match(r"^\d{8}$", str(d)):
                raise LawGoKrError(f"{nm} 형식 오류: '{d}' (YYYYMMDD 8자리)")
        if not date_to:
            date_to = time.strftime("%Y%m%d")
        if date_from >= date_to:
            raise LawGoKrError(f"date_from({date_from})은 date_to({date_to})보다 앞선 날짜여야 합니다")

        history = self.law_history(law_name, law_id=law_id)
        if not history:
            raise LawGoKrNotFound(f"연혁 없음: {law_name}")

        state_cache = {}  # MST -> (문안 줄 목록 or None, 조문시행일자)

        def state(mst):
            if mst not in state_cache:
                state_cache[mst] = self._article_state(mst, article_no)
            return state_cache[mst]

        # 시행본 시행일자만이 아니라 그 조문의 조문시행일자까지 보고 고른다
        # (조문별 단계 시행이면 시행본 XML에 아직 발효 전인 문안이 들어 있음)
        va, lines_a, art_date_a = self._pick_effective_version(history, date_from, article_no, state)
        if not va:
            raise LawGoKrNotFound(f"{date_from} 이전 시행본 없음 (최초 시행 {history[0]['시행일자']})")
        vb, lines_b, art_date_b = self._pick_effective_version(history, date_to, article_no, state)
        if not vb:
            raise LawGoKrNotFound(f"{date_to} 이전 시행본 없음: {law_name}")

        def meta(v):
            return {k: v[k] for k in ("법령명", "시행일자", "공포일자", "공포번호", "제개정구분", "MST")}

        label, _ = self._find_article_block("", article_no)  # 라벨 문자열만 계산
        out = {
            "법령명": va["법령명"],
            "조문": label,
            "비교기준": {
                "A": {"기준일": date_from, "시행본": meta(va)},
                "B": {"기준일": date_to, "시행본": meta(vb)},
            },
        }
        if art_date_a:
            out["비교기준"]["A"]["조문시행일자"] = art_date_a
        if art_date_b:
            out["비교기준"]["B"]["조문시행일자"] = art_date_b
        if va["MST"] == vb["MST"]:
            out["변경여부"] = False
            out["안내"] = ("두 기준일에 같은 조문 문안이 시행 중이었음 — "
                          "이 구간에 이 조문의 개정 시행이 없었음")
            return out
        if lines_a is None and lines_b is None:
            raise LawGoKrNotFound(f"{label}: A·B 두 시점 모두 조문이 존재하지 않음 (조번호 확인)")
        if lines_a == lines_b:
            out["변경여부"] = False
            out["안내"] = ("시행본은 다르지만 이 조문의 문안은 동일 — "
                          "이 구간의 개정은 이 조문을 건드리지 않았음")
            return out

        out["변경여부"] = True
        if lines_a is None:
            out["변경유형"] = "신설 (A 시점에는 없던 조문)"
        elif lines_b is None:
            out["변경유형"] = "삭제 (B 시점에는 없는 조문)"
        else:
            out["변경유형"] = "개정"
        diff_text = "\n".join(difflib.unified_diff(
            lines_a or [], lines_b or [],
            fromfile=f"A ({va['시행일자']} 시행본)", tofile=f"B ({vb['시행일자']} 시행본)",
            lineterm="", n=1))
        out["diff"] = diff_text[:max_chars]
        if len(diff_text) > max_chars:
            out["잘림"] = f"diff 전체 {len(diff_text)}자 중 앞 {max_chars}자만 표시 — max_chars를 늘려 재조회"

        if find_change:
            between = [r for r in history
                       if va["시행일자"] < (r["시행일자"] or "") <= vb["시행일자"]]
            if between:
                # B 문안은 어느 시점부터 계속 유지됐다고 보고(중간 되돌림 없음 가정),
                # B 문안과 같은 상태가 시작되는 최초 시행본을 이진탐색 — 왕복 log2(N)회
                lo, hi = 0, len(between) - 1
                while lo < hi:
                    mid = (lo + hi) // 2
                    if state(between[mid]["MST"])[0] == lines_b:
                        hi = mid
                    else:
                        lo = mid + 1
                change = between[lo]
                out["현행_문안_시작"] = meta(change)
                # 조문별 단계 시행이면 같은 공포본(MST)이 여러 시행일자 행으로 나타나므로,
                # 이 조문의 실제 적용 시작일은 시행본 시행일자가 아니라 조문시행일자가 기준
                ch_art_date = state(change["MST"])[1]
                if ch_art_date:
                    out["현행_문안_시작"]["이_조문의_시행일자"] = ch_art_date
                    if ch_art_date != change.get("시행일자"):
                        out["현행_문안_시작"]["주의"] = (
                            "조문별 단계 시행 — 이 조문의 실제 적용 시작은 "
                            f"'이_조문의_시행일자'({ch_art_date}) 기준")
                out["개정_부칙"] = self._addendum_for_change(change, label)
                out["안내"] = ("'현행_문안_시작'은 B 문안이 처음 수록된 시행본입니다. "
                              "A~B 사이에 개정이 여러 번 있었는지 확인하려면 date_to를 "
                              f"{change['시행일자']} 이전으로 좁혀 다시 비교하세요.")
        return out

    def _addendum_for_change(self, change_row: dict, label: str):
        """변경이 일어난 시행본의 부칙에서 시행일 요약 + 해당 조문 언급 항을 발췌."""
        xml = _get("lawService.do", target="law", MST=change_row["MST"])
        units = self._parse_addenda_units(xml)
        want = (change_row.get("공포번호") or "").lstrip("0")
        unit = next((u for u in units if u["공포번호"] == want), None)
        if unit is None:
            return {"안내": (f"공포번호 {change_row.get('공포번호')}의 부칙을 찾지 못함 "
                            f"(타법개정 등) — law_addenda_search로 직접 조회")}
        adx = {
            "공포일자": unit["공포일자"],
            "공포번호": unit["공포번호"],
            "시행일_요약": self._effective_summary(unit["본문"]),
        }
        # '제96조' 검색이 '제96조의2' 언급에 오매치되지 않게 가지조문 아니면 (?!의) 가드
        pat = re.compile(re.escape(label) + ("" if "의" in label else r"(?!의)"))
        paras = [p[:1500] for p in self._split_addendum(unit["본문"]) if pat.search(p)]
        if paras:
            adx["이_조문_언급_항"] = paras[:5]
        else:
            adx["안내"] = ("이 개정 부칙에 이 조문을 명시한 적용례·경과조치 없음 — "
                          "시행일_요약의 일반 시행일을 따름")
        return adx
