# -*- coding: utf-8 -*-
"""
test_tax_tribunal.py — v5.10 오프라인 테스트: 국가법령정보센터 조세심판원 재결례 보충 조회,
olta 만료 인증서 대비.

law.go.kr에는 접속하지 않는다 (law_go_kr._get을 가짜로 바꿔 끼움).

실행:
    python tests/test_tax_tribunal.py
    python -m pytest tests/test_tax_tribunal.py -q
"""
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import law_go_kr  # noqa: E402
import olta_tax_ruling_search as olta  # noqa: E402
import server  # noqa: E402
import server_ext  # noqa: E402

# 2026-11-23 실측 응답을 줄인 것 (청구번호 표기 '조심 2020중2305', 상세 응답은 청구번호 빈칸)
SEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?><Decc><target>ttSpecialDecc</target>
<totalCnt>{n}</totalCnt><page>1</page>{rows}</Decc>"""
ROW = """<decc id="1"><특별행정심판재결례일련번호>{serial}</특별행정심판재결례일련번호>
<사건명><![CDATA[{title}]]></사건명><청구번호><![CDATA[{no}]]></청구번호><처분일자></처분일자>
<의결일자>{date}</의결일자><재결청>조세심판원</재결청><데이터기준일시>2024.12.18</데이터기준일시></decc>"""
DETAIL_XML = """<?xml version="1.0" encoding="UTF-8"?><SpecialDeccService>
<특별행정심판재결례일련번호>182286</특별행정심판재결례일련번호>
<사건명><![CDATA[쟁점금액을 업무무관 가지급금으로 보아 결손금을 감액 경정한 처분의 당부]]></사건명>
<사건번호></사건번호><청구번호></청구번호><의결일자>20210706</의결일자><재결청>조세심판원</재결청>
<세목><![CDATA[법인]]></세목><재결요지><![CDATA[처분은 잘못이 있는 것으로 판단된다.]]></재결요지>
<주문><![CDATA[과세표준을 경정한다.]]></주문><청구취지><![CDATA[]]></청구취지>
<이유><![CDATA[1. 처분개요 가. 청구법인은 ...<br/>2. 청구법인 주장 ...]]></이유>
<관련법령><![CDATA[법인세법 제52조]]></관련법령></SpecialDeccService>"""


class _FakeGet:
    """law_go_kr._get을 잠시 바꾼다. rows_by_query: 검색어 → [(serial, 청구번호, 의결일자)]."""

    def __init__(self, rows_by_query=None, fail=None):
        self.rows_by_query = rows_by_query or {}
        self.fail = fail
        self.calls = []

    def __call__(self, endpoint, **params):
        self.calls.append((endpoint, params))
        if self.fail:
            raise self.fail
        if endpoint == "lawService.do":
            return DETAIL_XML
        rows = self.rows_by_query.get(params.get("query"), [])
        body = "".join(ROW.format(serial=s, title="사건명", no=no, date=d) for s, no, d in rows)
        return SEARCH_XML.format(n=len(rows), rows=body)

    def __enter__(self):
        self._old = law_go_kr._get
        law_go_kr._get = self
        return self

    def __exit__(self, *exc):
        law_go_kr._get = self._old


def test_parse_tax_tribunal_no_variants():
    p = law_go_kr.parse_tax_tribunal_no
    assert p("조심 2020중2305") == ("조심", "2020", "중", "2305")
    assert p("조심-2023-서-9465") == ("조심", "2023", "서", "9465")
    assert p("조심2024지63") == ("조심", "2024", "지", "63")
    assert p("국심2007서3619") == ("국심", "2007", "서", "3619")
    assert p("2026서1112") == ("", "2026", "서", "1112")
    assert p("서면-2019-법규재산-4276") is None
    assert p("사전-2026-법규법인-0502") is None


def test_find_matches_exact_number_and_zero_padding():
    rows = {"2024지63": [], "2024지0063": [("1", "조심 2024지0063", "2026.04.23")],
            "2020중2305": [("182286", "조심 2020중2305", "2021.07.06"),
                          ("999", "조심 2020중23050", "2021.01.01")]}
    with _FakeGet(rows):
        c = law_go_kr.LawGoKrClient()
        assert [h["청구번호"] for h in c.find_tax_tribunal("조심2024지63")] == ["조심 2024지0063"]
        # 검색어가 앞부분만 같은 번호(23050)는 버린다
        assert [h["일련번호"] for h in c.find_tax_tribunal("조심-2020-중-2305")] == ["182286"]
        # 머리말이 다르면(국심 ↔ 조심) 다른 결정이다
        assert c.find_tax_tribunal("국심2020중2305") == []


def test_full_text_by_no_joins_sections_and_pages():
    rows = {"2020중2305": [("182286", "조심 2020중2305", "2021.07.06")]}
    with _FakeGet(rows):
        c = law_go_kr.LawGoKrClient()
        out = c.get_tax_tribunal_by_no("조심 2020중2305", max_chars=20)
        assert out["청구번호"] == "조심 2020중2305" and out["의결일자"] == "2021.07.06"
        assert out["전문"].startswith("[주문]") and len(out["전문"]) == 20
        assert "잘림" in out and "start_char=20" in out["잘림"]
        full = c.get_tax_tribunal_by_no("조심 2020중2305")
        assert "[이유]" in full["전문"] and "[관련법령]" in full["전문"] and "<br/>" not in full["전문"]
        assert "[청구취지]" not in full["전문"]  # 빈 칸은 싣지 않는다


def test_get_by_doc_no_falls_back_when_nts_misses():
    rows = {"2020중2305": [("182286", "조심 2020중2305", "2021.07.06")]}
    nts_miss = {"status": "NOT_FOUND", "found": False, "items": [], "message": "못 찾음"}
    with _FakeGet(rows):
        out = server._with_tt_lookup("조심 2020중2305", nts_miss)
        assert out["status"] == "OK" and out["found"] and out["출처"] == server.TT_SOURCE
        assert out["items"][0]["일련번호"] == "182286"
        # 국세청에서 찾았으면 그대로
        hit = {"status": "OK", "found": True, "items": [{"doc_no": "조심-2020-중-2305"}]}
        assert server._with_tt_lookup("조심 2020중2305", hit) is hit
        # 조세심판원 번호가 아니면 법제처를 부르지 않는다 (판결 번호 '2022누45199'도 머리말이 없어 제외)
        assert server._with_tt_lookup("서면-2019-법규재산-4276", nts_miss) is nts_miss
        assert server._with_tt_lookup("2022누45199", nts_miss) is nts_miss


def test_get_by_doc_no_fallback_failure_is_not_absence():
    nts_miss = {"status": "NOT_FOUND", "found": False, "items": []}
    with _FakeGet(fail=requests.exceptions.ConnectTimeout("timeout")):
        out = server._with_tt_lookup("조심 2020중2305", nts_miss)
        # 법제처 확인이 실패했으면 NOT_FOUND('부존재 판단 가능')가 아니라 UPSTREAM_ERROR
        assert out["status"] == "UPSTREAM_ERROR" and "조회 실패" in out["국가법령정보센터_확인"]
        out = server._with_tt_full_text("조심 2020중2305", {"status": "NOT_FOUND"}, 30000, 0)
        assert out["status"] == "UPSTREAM_ERROR" and "조회 실패" in out["국가법령정보센터_확인"]
        assert "단정하지" in out["_guidance"]
    with _FakeGet({}):
        out = server._with_tt_lookup("조심 2020중2305", nts_miss)
        assert out["국가법령정보센터_확인"].startswith("없음")


def test_law_get_retries_once_on_dropped_connection():
    calls = []

    class _Resp:
        text = "<ok/>"

        def raise_for_status(self):
            pass

    def fake_get(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise requests.exceptions.ConnectionError("Remote end closed connection without response")
        return _Resp()

    old_get, old_oc = law_go_kr.requests.get, law_go_kr.OC
    law_go_kr.requests.get, law_go_kr.OC = fake_get, "DUMMYOC123"
    law_go_kr._cache.clear()
    try:
        assert law_go_kr._get("lawSearch.do", target="ttSpecialDecc", query="retrytest") == "<ok/>"
        assert len(calls) == 2
        calls.clear()
        law_go_kr.requests.get = lambda *a, **kw: calls.append(1) or (_ for _ in ()).throw(
            requests.exceptions.ReadTimeout("read timeout"))
        try:
            law_go_kr._get("lawSearch.do", target="ttSpecialDecc", query="retrytest2")
        except requests.exceptions.ReadTimeout:
            pass
        assert len(calls) == 1  # 읽기 시간 초과는 다시 하지 않는다
    finally:
        law_go_kr.requests.get, law_go_kr.OC = old_get, old_oc
        law_go_kr._cache.clear()


def test_full_text_falls_back_when_nts_misses():
    rows = {"2020중2305": [("182286", "조심 2020중2305", "2021.07.06")]}
    with _FakeGet(rows):
        out = server._with_tt_full_text("조심 2020중2305", {"status": "NOT_FOUND"}, 30000, 0)
        assert out["status"] == "OK" and out["출처"].startswith("국가법령정보센터")
        assert "[이유]" in out["전문"] and "NOT_FOUND" in out["비고"]
        ok = {"status": "OK", "전문": "국세청 붙임"}
        assert server._with_tt_full_text("조심 2020중2305", ok, 30000, 0) is ok


def test_verify_citation_confirms_from_law_go_kr():
    rows = {"2020중2305": [("182286", "조심 2020중2305", "2021.07.06")]}
    old_nts, old_olta = server._collect_nts_candidates, server._collect_olta_candidates
    olta_called = []
    server._collect_nts_candidates = lambda raw: []
    server._collect_olta_candidates = lambda raw: olta_called.append(raw) or []
    try:
        with _FakeGet(rows):
            e = server._verify_one_citation("조심 2020중2305", True)
            assert e["판정"] == "확인" and e["출처"] == server.TT_SOURCE and not olta_called
        with _FakeGet(fail=requests.exceptions.ConnectTimeout("timeout")):
            e = server._verify_one_citation("조심 2020중2305", True)
            assert e["판정"] == "판단불가"  # 법제처 확인 실패 + 다른 데도 없음 → 없다고 단정 못 함
        with _FakeGet({}):
            e = server._verify_one_citation("조심 2020중2305", True)
            assert e["판정"] == "미확인"
    finally:
        server._collect_nts_candidates, server._collect_olta_candidates = old_nts, old_olta


def test_new_tool_routes_and_validates():
    rows = {"2020중2305": [("182286", "조심 2020중2305", "2021.07.06")],
            "가지급금": [("182286", "조심 2020중2305", "2021.07.06")]}
    with _FakeGet(rows) as fake:
        assert server_ext.tax_tribunal_decision_search()["status"] == "INVALID_INPUT"
        r = server_ext.tax_tribunal_decision_search(keyword="가지급금", search_body=True,
                                                    date_from="20200101", date_to="20201231")
        assert r["decisions"][0]["청구번호"] == "조심 2020중2305"
        params = fake.calls[-1][1]
        assert params["search"] == 2 and params["sort"] == "ddes" and params["rslYd"] == "20200101~20201231"
        assert server_ext.tax_tribunal_decision_search(doc_no="조심2020중2305")["청구번호"] == "조심 2020중2305"
        assert "전문" in server_ext.tax_tribunal_decision_search(serial="182286")
        assert server_ext.tax_tribunal_decision_search(doc_no="서면-2019-법규재산-4276")["status"] == "INVALID_INPUT"
        assert server_ext.tax_tribunal_decision_search(keyword="없는말")["status"] == "NOT_FOUND"


def test_search_results_carry_tribunal_hint():
    old = server._client.search
    server._client.search = lambda **kw: {"precedent": {"total_count": 0, "items": []}}
    try:
        assert "_조세심판원_보충" in server.nts_ruling_search(keyword="가지급금")
        assert "_조세심판원_보충" in server.nts_ruling_search(keyword="가지급금", collections=["precedent"])
        assert "_조세심판원_보충" not in server.nts_ruling_search(keyword="가지급금", collections=["ruling"])
        assert "_조세심판원_보충" not in server.nts_ruling_search(keyword="가지급금", statute_types=["집행기준"])
    finally:
        server._client.search = old


def test_instructions_mention_tribunal_gap():
    text = server.mcp.instructions
    assert "tax_tribunal_decision_search" in text and "국세법령정보시스템에 다 있지 않습니다" in text


def test_expired_cert_fallback_only_for_expiry():
    adapter = olta.ExpiredCertFallbackAdapter()
    sent = []

    class _Relaxed:
        def send(self, request, **kw):
            sent.append(request.url)
            return "relaxed-response"

        def close(self):
            pass

    adapter._relaxed = _Relaxed()
    req = requests.Request("GET", "https://olta.re.kr/").prepare()
    base_send = requests.adapters.HTTPAdapter.send
    try:
        requests.adapters.HTTPAdapter.send = lambda self, request, **kw: (_ for _ in ()).throw(
            requests.exceptions.SSLError("certificate verify failed: certificate has expired"))
        assert adapter.send(req, verify=True) == "relaxed-response" and sent
        # 끄라고(verify=False) 한 호출은 손대지 않는다 — 원래 오류 그대로
        try:
            adapter.send(req, verify=False)
        except requests.exceptions.SSLError:
            pass
        else:
            raise AssertionError
        requests.adapters.HTTPAdapter.send = lambda self, request, **kw: (_ for _ in ()).throw(
            requests.exceptions.SSLError("certificate verify failed: Hostname mismatch"))
        try:
            adapter.send(req, verify=True)
        except requests.exceptions.SSLError:
            pass
        else:
            raise AssertionError("만료가 아닌 인증서 오류는 그대로 실패해야 한다")
    finally:
        requests.adapters.HTTPAdapter.send = base_send
    assert isinstance(olta.new_session().get_adapter("https://olta.re.kr/"), olta.ExpiredCertFallbackAdapter)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("ok", t.__name__)
