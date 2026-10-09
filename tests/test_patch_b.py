# -*- coding: utf-8 -*-
"""
test_patch_b.py — v5.7.3(패치 B) 오프라인 테스트: 기준일 생략=오늘, 조문 도구 NOT_FOUND 안내,
기관코드(OC) 가림, 부칙 법령명 정확 일치, 서버 안내문.

law.go.kr에는 접속하지 않는다 (requests.get·_get을 가짜로 바꿔 끼움). OC도 가짜 값.

실행:
    python tests/test_patch_b.py
    python -m pytest tests/test_patch_b.py -q
"""
import inspect
import io
import logging
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import law_go_kr  # noqa: E402

FAKE_OC = "DUMMYOC123"


class _FakeOC:
    """law_go_kr.OC를 가짜 값으로 잠시 바꾼다."""

    def __enter__(self):
        self.orig = law_go_kr.OC
        law_go_kr.OC = FAKE_OC
        return self

    def __exit__(self, *exc):
        law_go_kr.OC = self.orig
        return False


def _resp(status, url):
    r = requests.Response()
    r.status_code = status
    r.url = url
    r._content = b"<html>error</html>"
    return r


def test_mask_oc_hides_param_and_value():
    with _FakeOC():
        s = law_go_kr.mask_oc(f"500 Server Error for url: http://www.law.go.kr/DRF/lawService.do?OC={FAKE_OC}&type=XML")
        assert FAKE_OC not in s and "OC=***" in s, s
        assert FAKE_OC not in law_go_kr.mask_oc(f"기관코드 {FAKE_OC} 오류"), "값 자체도 가려야 함"
    assert law_go_kr.mask_oc("lawSearch.do?OC=abc&x=1") == "lawSearch.do?OC=***&x=1"


def test_get_raises_masked_http_error_keeping_status():
    """HTTP 500 — 예외 문자열에 OC가 없고, 폴백 판정용 response.status_code는 남는다. 원래 체인은 숨긴다."""
    orig = law_go_kr.requests.get
    law_go_kr.requests.get = lambda url, params=None, **kw: _resp(
        500, f"{url}?OC={params['OC']}&type=XML&target=law&MST=1")
    try:
        with _FakeOC():
            try:
                law_go_kr._get("lawService.do", target="law", MST="1")
                raise AssertionError("HTTPError가 나야 함")
            except requests.exceptions.HTTPError as e:
                assert FAKE_OC not in str(e) and "OC=***" in str(e), str(e)
                assert e.response is not None and e.response.status_code == 500
                assert e.__suppress_context__ and e.__cause__ is None, "원래 예외 체인(OC 포함)이 붙어 있음"
    finally:
        law_go_kr.requests.get = orig


def test_get_masks_connection_error():
    def boom(url, params=None, **kw):
        raise requests.exceptions.ConnectionError(
            f"HTTPConnectionPool(host='www.law.go.kr'): Max retries exceeded with url: /DRF/lawSearch.do?OC={params['OC']}&type=XML")
    orig = law_go_kr.requests.get
    law_go_kr.requests.get = boom
    try:
        with _FakeOC():
            try:
                law_go_kr._get("lawSearch.do", target="law", query="소득세법")
                raise AssertionError("ConnectionError가 나야 함")
            except requests.exceptions.ConnectionError as e:
                assert FAKE_OC not in str(e), str(e)
    finally:
        law_go_kr.requests.get = orig


def test_safe_masks_error_field():
    import server_ext
    with _FakeOC():
        def f():
            raise requests.exceptions.HTTPError(f"500 for url: http://x/DRF/lawService.do?OC={FAKE_OC}&MST=1")
        out = server_ext._safe(f)
        assert out["status"] == "UPSTREAM_ERROR" and FAKE_OC not in out["오류"], out

        def g():
            raise RuntimeError(f"예상 못한 오류 OC={FAKE_OC}")
        out2 = server_ext._safe(g)
        assert out2["status"] == "UPSTREAM_ERROR" and FAKE_OC not in out2["오류"], out2


def test_log_filter_masks_message_and_traceback():
    buf = io.StringIO()
    lg = logging.getLogger("test_patch_b.oc")
    lg.propagate = False
    h = logging.StreamHandler(buf)
    lg.addHandler(h)
    law_go_kr.install_oc_log_mask(lg)
    try:
        with _FakeOC():
            lg.error("실패: %s", f"url ?OC={FAKE_OC}&type=XML")
            try:
                raise ValueError(f"내부 오류 OC={FAKE_OC}")
            except ValueError:
                lg.exception("트레이스백")
        text = buf.getvalue()
        assert FAKE_OC not in text and "OC=***" in text and "Traceback" in text, text
    finally:
        lg.removeHandler(h)


def test_law_article_as_of_defaults_to_today_kst():
    """as_of_date를 생략하면 오늘(KST) — 인자 순서는 (law_name, article_no, as_of_date, …)."""
    import server_ext
    params = list(inspect.signature(server_ext.law_article_as_of).parameters.values())
    assert [p.name for p in params[:3]] == ["law_name", "article_no", "as_of_date"], params
    assert params[2].default == "", params[2]
    assert server_ext.law_article_as_of.__doc__.startswith("법령 조문 원문 — 현행(기준일 생략 시 오늘)")
    seen = []
    orig = server_ext._law.law_article_as_of
    server_ext._law.law_article_as_of = lambda name, as_of, art, law_id="", max_chars=6000: (
        seen.append((name, as_of, art)) or {"조문": "제59조의2", "원문": "x"})
    try:
        out = server_ext.law_article_as_of(law_name="소득세법", article_no="59의2")
        past = server_ext.law_article_as_of(law_name="소득세법", article_no="59의2", as_of_date="20200101")
    finally:
        server_ext._law.law_article_as_of = orig
    assert out["status"] == "OK" and past["status"] == "OK"
    assert seen == [("소득세법", law_go_kr._today_kst(), "59의2"), ("소득세법", "20200101", "59의2")], seen


def test_article_tools_not_found_guidance_only_for_article_tools():
    """법령 조문 도구의 NOT_FOUND는 '단정하지 말 것' 안내, 다른 도구의 NOT_FOUND 의미는 그대로."""
    import server_ext
    nf = law_go_kr.LawGoKrNotFound("MST 1(시행일자 20260101 통합본)에서 제9조 조문을 찾지 못함")

    def raise_nf(*a, **k):
        raise nf
    orig = (server_ext._law.law_article_as_of, server_ext._law.law_article_diff, server_ext._law.get_case)
    server_ext._law.law_article_as_of = raise_nf
    server_ext._law.law_article_diff = raise_nf
    server_ext._law.get_case = raise_nf
    try:
        a = server_ext.law_article_as_of(law_name="가법", article_no="9", as_of_date="20260101")
        d = server_ext.law_article_diff(law_name="가법", article_no="9", date_from="20250101", date_to="20260101")
        c = server_ext.court_case_detail(case_serial="1")
    finally:
        server_ext._law.law_article_as_of, server_ext._law.law_article_diff, server_ext._law.get_case = orig
    for out in (a, d):
        assert out["status"] == "NOT_FOUND" and "단정하지 말고" in out["_guidance"], out
        assert "law_history_search" in out["_guidance"], out
    assert c["status"] == "NOT_FOUND" and "단정하지 말고" not in c["_guidance"], c


def _search_xml(names):
    return "<LawSearch>" + "".join(
        f"<law id='{i}'><법령명한글><![CDATA[{n}]]></법령명한글><법령ID>{900000 + i}</법령ID>"
        f"<법령일련번호>{1000 + i}</법령일련번호><시행일자>20260910</시행일자><공포일자>20260310</공포일자>"
        f"<공포번호>{i}</공포번호><제개정구분명>일부개정</제개정구분명></law>"
        for i, n in enumerate(names, 1)) + "</LawSearch>"


def test_addenda_name_must_match_exactly_with_wide_search():
    """'상법' — display=20 검색 결과가 모두 '…보상…' 법령이라 첫 결과로 내려가면 엉뚱한 법 부칙이 OK로 나갔다
    (2026-10-09 실측: '1980년해직공무원의보상등에관한특별조치법'). display=100으로 넓게 찾고 정확 일치만 쓴다."""
    names = [f"{i}보상등에관한법률" for i in range(33)] + ["상법"] + [f"{i}보상법" for i in range(22)]
    calls = []
    doc = ("<?xml version='1.0' encoding='UTF-8'?><법령><기본정보><법령ID>900034</법령ID><법령명_한글><![CDATA[상법]]>"
           "</법령명_한글><시행일자>20260910</시행일자></기본정보><조문><조문단위 조문키='1'><조문번호>1</조문번호>"
           "<조문내용><![CDATA[제1조(목적) 이 법은 상사에 관하여 정한다.]]></조문내용></조문단위></조문>"
           "<부칙><부칙단위><부칙공포일자>20260310"
           "</부칙공포일자><부칙공포번호>34</부칙공포번호><부칙내용><![CDATA[부칙\n제1조(시행일) 이 법은 공포한 날부터 "
           "시행한다.]]></부칙내용></부칙단위></부칙></법령>")

    def fake(endpoint, **params):
        calls.append((endpoint, params.get("target"), params.get("display"), params.get("MST"), params.get("efYd")))
        if endpoint == "lawSearch.do" and params.get("target") == "law":
            return _search_xml(names[:params.get("display", 20)])
        if endpoint == "lawSearch.do":
            return "<LawSearch></LawSearch>"
        if params.get("target") == "eflaw" and params.get("MST") == "1034":
            return doc
        return "<Law>일치하는 법령이 없습니다.</Law>"
    orig = law_go_kr._get
    law_go_kr._get = fake
    try:
        c = law_go_kr.LawGoKrClient()
        out = c.law_addenda(law_name="상법", recent=1)
        assert out["법령명"] == "상법" and out["MST"] == "1034", out
        assert ("lawSearch.do", "law", 100, None, None) in calls, calls
        try:
            c.law_addenda(law_name="보상", recent=1)
            raise AssertionError("정확 일치가 없으면 INVALID_INPUT이어야 함")
        except law_go_kr.LawGoKrNotFound as e:
            raise AssertionError(f"NOT_FOUND로 나감: {e}")
        except law_go_kr.LawGoKrError as e:
            assert "이름이 정확히 같은" in str(e) and "검색된 법령" in str(e), e
    finally:
        law_go_kr._get = orig


def test_server_instructions_cover_law_tools():
    import server
    text = server.mcp.instructions
    for phrase in ("law_article_as_of(as_of_date 생략 = 오늘)", "적용시행본", "law_addenda_search(article_no)",
                   "시행예정_부분", "국회 계류", "NOT_FOUND는 조문이 없다고 단정하지 말고"):
        assert phrase in text, phrase


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok", name)
            except Exception as e:  # noqa: BLE001
                failed += 1
                print("FAIL", name, f"{type(e).__name__}: {e}")
    sys.exit(1 if failed else 0)
