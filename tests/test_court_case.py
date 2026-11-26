# -*- coding: utf-8 -*-
"""
test_court_case.py — v5.11.2 오프라인 테스트: 법제처 판례 검색의 법원 구분(하위법원) 버그 수정,
사건번호 정확 조회, 인용 검증의 판결 번호 확인.

law.go.kr에는 접속하지 않는다 (law_go_kr._get을 가짜로 바꿔 끼움).

실행:
    python tests/test_court_case.py
    python -m pytest tests/test_court_case.py -q
"""
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import law_go_kr  # noqa: E402
import server  # noqa: E402
import server_ext  # noqa: E402

ROW = ("<prec id=\"1\"><판례일련번호>{serial}</판례일련번호><사건명><![CDATA[사건]]></사건명>"
       "<사건번호>{no}</사건번호><법원명>{court}</법원명><선고일자>2020.01.01</선고일자></prec>")


def _xml(rows):
    body = "".join(ROW.format(serial=s, no=n, court=c) for s, n, c in rows)
    return f"<PrecSearch><totalCnt>{len(rows)}</totalCnt>{body}</PrecSearch>"


class _FakeGet:
    """by_nb: 사건번호 → 행 목록 / by_query: 검색어 → 행 목록."""

    def __init__(self, by_nb=None, by_query=None, fail=None):
        self.by_nb, self.by_query, self.fail = by_nb or {}, by_query or {}, fail
        self.calls = []

    def __call__(self, endpoint, **params):
        self.calls.append(params)
        if self.fail:
            raise self.fail
        if "nb" in params:
            return _xml(self.by_nb.get(params["nb"], []))
        return _xml(self.by_query.get(params.get("query"), []))

    def __enter__(self):
        self._old = law_go_kr._get
        law_go_kr._get = self
        return self

    def __exit__(self, *exc):
        law_go_kr._get = self._old


def test_court_param_maps_lower_courts_to_org_code():
    p = law_go_kr._court_params
    assert p("하위법원") == {"org": "400202"} and p("하급심") == {"org": "400202"}
    assert p("대법원") == {"org": "400201"}
    assert p("서울고등법원") == {"curt": "서울고등법원"}
    assert p("") == {}
    with _FakeGet(by_query={"주식매수청구권": [("1", "2023나2056133", "서울고등법원")]}) as fake:
        r = law_go_kr.LawGoKrClient().search_cases("주식매수청구권", court="하위법원")
        assert r["cases"][0]["법원명"] == "서울고등법원"
        assert fake.calls[0].get("org") == "400202" and "curt" not in fake.calls[0]


def test_case_no_detection():
    c = law_go_kr.normalize_case_no
    assert c("2017나2027639") == "2017나2027639" and c("2013가합63177") == "2013가합63177"
    assert c("대법원 2019다236385") == "2019다236385" and c("서울고법 2017나2027639") == "2017나2027639"
    assert c("96누1234") == "96누1234"
    assert c("주식매수청구권") == "" and c("조심2020중2305") == "" and c("2013") == ""


def test_exact_case_lookup_and_citing_fallback():
    fake = _FakeGet(by_nb={"2025나10173": [("9", "2025나10173", "광주고등법원제주재판부")]},
                    by_query={"2017나2027639": [("5", "2019다236385", "대법원")]})
    with fake:
        c = law_go_kr.LawGoKrClient()
        hit = c.search_cases("2025나10173")
        assert hit["cases"][0]["사건번호"] == "2025나10173" and hit["사건번호_정확조회"] == "2025나10173"
        miss = c.search_cases("", case_no="2017나2027639")
        assert miss["status"] == "NOT_FOUND" and miss["cases"] == []
        assert miss["이_번호를_언급한_판결"][0]["사건번호"] == "2019다236385"
        assert "존재하지 않는다는 뜻이 아닙니다" in miss["_guidance"]


def test_tool_adds_coverage_note_and_validates():
    with _FakeGet():
        assert server_ext.court_case_search()["status"] == "INVALID_INPUT"
        r = server_ext.court_case_search(keyword="없는주제", court="하위법원")
        assert r["status"] == "NOT_FOUND" and "일부만" in r["_guidance"]


def test_verify_citations_checks_court_case_numbers():
    old_nts, old_olta = server._collect_nts_candidates, server._collect_olta_candidates
    server._collect_nts_candidates = lambda raw: []
    server._collect_olta_candidates = lambda raw: []
    try:
        with _FakeGet(by_nb={"2019다236385": [("5", "2019다236385", "대법원")]},
                      by_query={"2017나2027639": [("5", "2019다236385", "대법원")]}):
            e = server._verify_one_citation("대법원 2019다236385", True)
            assert e["판정"] == "확인" and e["출처"] == server.PREC_SOURCE
            e = server._verify_one_citation("2017나2027639", True)
            assert e["판정"] == "미확인" and "일부만" in e["비고"]
            assert e["이_번호를_언급한_판결"][0]["사건번호"] == "2019다236385"
        with _FakeGet(fail=requests.exceptions.ConnectTimeout("t")):
            assert server._verify_one_citation("2017나2027639", True)["판정"] == "판단불가"
    finally:
        server._collect_nts_candidates, server._collect_olta_candidates = old_nts, old_olta


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("ok", t.__name__)
