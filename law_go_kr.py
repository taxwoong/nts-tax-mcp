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
import requests

BASE = "http://www.law.go.kr/DRF"
OC = os.environ.get("LAW_API_OC", "")
TIMEOUT = 20

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
    r = requests.get(f"{BASE}/{endpoint}", params=p, timeout=TIMEOUT,
                     headers={"User-Agent": "nts-tax-mcp/1.0"})
    r.raise_for_status()
    text = r.text
    if "인증" in text[:500] and "실패" in text[:500]:
        raise LawGoKrError("law.go.kr 인증 실패 — OC 또는 IP 등록 확인 필요 (open.law.go.kr에서 현재 서버 IP 등록)")
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
            xml = _get("lawSearch.do", target="eflaw", query=law_name, numOfRows=100, page=page)
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
            return {
                "조문": label,
                "조문제목": _strip_cdata(title.group(1)) if title else "",
                "원문": body[:max_chars],
            }
        raise LawGoKrError(f"MST {mst}에서 제{article_no}조를 찾지 못함")

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

    def get_admin_rule(self, serial: str, max_chars: int = 10000):
        """target=admrul 행정규칙 본문."""
        xml = _get("lawService.do", target="admrul", ID=serial)
        name = re.search(r"<행정규칙명>(.*?)</행정규칙명>", xml, re.S)
        body = _strip_tags(_strip_cdata(xml))
        if len(body) < 50:
            return {"오류": "본문 없음", "응답": xml[:200]}
        return {
            "행정규칙명": _strip_cdata(name.group(1)) if name else "",
            "본문": body[:max_chars],
            "본문길이": len(body),
        }

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
    def law_article_as_of(self, law_name: str, as_of_date: str, article_no: str, law_id: str = ""):
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
        art = self.law_article(chosen["MST"], article_no)
        art["적용시행본"] = {k: chosen[k] for k in ("법령명", "시행일자", "공포일자", "공포번호", "제개정구분", "MST")}
        return art
