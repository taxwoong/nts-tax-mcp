# -*- coding: utf-8 -*-
"""
test_followup.py — v5.8.0 오프라인 테스트
  ① 자치법규 본문 조회: 검색 결과의 일련번호는 MST — ID=로 넘기면 없는 조례이거나 엉뚱한 조례가 열렸다
  ② 자치법규 지자체 필터: 첫 쪽만 거르면 뒤쪽 조례를 놓친다
  ③ 국세청 통합검색: 법령·별표서식·구 법령해석자료·홈택스 상담사례의 빈 필드 채우기
  ④ 개명된 법령의 연혁: 같은 법령ID의 개명 전 시행본 포함 (LID 검색)
  ⑤ 조문 원문의 메타데이터 줄·중복 항번호 제거

law.go.kr·taxlaw에는 접속하지 않는다 (_get을 가짜로 바꿔 끼움). 샘플은 2026-10-10 실응답에서 줄인 것.

실행:
    python tests/test_followup.py
    python -m pytest tests/test_followup.py -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import law_go_kr  # noqa: E402
import nts_tax_ruling_search as nts  # noqa: E402


class _FakeGet:
    """law_go_kr._get을 잠시 바꿔 끼우고 호출 기록을 남긴다. handler(endpoint, **params) → XML 문자열."""

    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def __enter__(self):
        self.orig = law_go_kr._get

        def fake(endpoint, **params):
            self.calls.append((endpoint, params))
            return self.handler(endpoint, **params)

        law_go_kr._get = fake
        return self

    def __exit__(self, *exc):
        law_go_kr._get = self.orig
        return False


# ---------------------------------------------------------------- ① 자치법규 본문
ORDIN_XML = (
    "<?xml version='1.0'?><LawService><자치법규기본정보><자치법규ID>2020399</자치법규ID>"
    "<자치법규일련번호>2118171</자치법규일련번호><공포일자>20260401</공포일자><공포번호>1919</공포번호>"
    "<자치법규명><![CDATA[강릉시 시세 감면 조례]]></자치법규명><시행일자>20260401</시행일자>"
    "<지자체기관명>강원특별자치도 강릉시</지자체기관명><담당부서명>세무과</담당부서명>"
    "<제개정정보>일부개정</제개정정보></자치법규기본정보>"
    "<조문><조 조문번호='000100'><조문번호>000100</조문번호><조문여부>Y</조문여부>"
    "<조제목><![CDATA[목적]]></조제목><조내용><![CDATA[제1조(목적) 이 조례는 시세의 감면을 규정한다.\n]]></조내용></조>"
    "<조 조문번호='000200'><조문번호>000200</조문번호><조문여부>Y</조문여부>"
    "<조제목><![CDATA[감면]]></조제목><조내용><![CDATA[제2조(감면) 자동차세를 면제한다.\n]]></조내용></조></조문>"
    "<부칙><부칙공포일자>20260401</부칙공포일자><부칙공포번호>1919</부칙공포번호>"
    "<부칙내용><![CDATA[부      칙 <조례 제1919호, 2026.4.1.>\n이 조례는 공포한 날부터 시행한다.]]></부칙내용></부칙>"
    "</LawService>"
)
NOT_FOUND_XML = "<?xml version='1.0'?><Law>일치하는 자치법규가 없습니다.  자치법규명을 확인하여 주십시오.</Law>"


def test_ordinance_body_is_fetched_by_mst_not_id():
    def handler(endpoint, **p):
        return ORDIN_XML if p.get("MST") == "2118171" else NOT_FOUND_XML

    with _FakeGet(handler) as g:
        d = law_go_kr.LawGoKrClient().get_ordinance("2118171")
    assert g.calls[0][1].get("MST") == "2118171" and "ID" not in g.calls[0][1], g.calls
    assert len(g.calls) == 1, "일련번호로 찾았으면 ID로 다시 묻지 않는다"
    assert d["자치법규명"] == "강릉시 시세 감면 조례" and d["지자체"] == "강원특별자치도 강릉시"
    assert d["시행일자"] == "20260401" and d["조회방식"] == "일련번호"
    assert d["본문"].startswith("제1조(목적)") and "제2조(감면)" in d["본문"]
    assert d["본문"].count("부      칙") == 1, "부칙 제목이 중복되면 안 됨"
    assert "이 조례는 공포한 날부터 시행한다" in d["본문"]
    assert "주의" not in d and "잘림" not in d


def test_ordinance_falls_back_to_id_only_when_mst_missing():
    def handler(endpoint, **p):
        return ORDIN_XML if p.get("ID") == "2020399" else NOT_FOUND_XML

    with _FakeGet(handler) as g:
        d = law_go_kr.LawGoKrClient().get_ordinance("2020399")
    assert "MST" in g.calls[0][1] and "ID" in g.calls[1][1] and len(g.calls) == 2, g.calls
    assert d["조회방식"] == "자치법규ID" and "자치법규ID" in d["주의"]


def test_ordinance_errors():
    with _FakeGet(lambda e, **p: NOT_FOUND_XML):
        try:
            law_go_kr.LawGoKrClient().get_ordinance("999999999")
            assert False, "없는 번호는 LawGoKrNotFound"
        except law_go_kr.LawGoKrNotFound:
            pass
    for bad in ("abc", "12 34", ""):
        try:
            law_go_kr.LawGoKrClient().get_ordinance(bad)
            assert False, f"형식 오류 거부: {bad!r}"
        except law_go_kr.LawGoKrError:
            pass


def test_ordinance_truncation_messages():
    with _FakeGet(lambda e, **p: ORDIN_XML):
        c = law_go_kr.LawGoKrClient()
        first = c.get_ordinance("2118171", max_chars=20)
        assert "start_char=20" in first["잘림"] and first["본문길이"] > 20
        last = c.get_ordinance("2118171", max_chars=1000, start_char=first["본문길이"] - 10)
        assert "끝까지 읽음" in last["잘림"], last["잘림"]
        whole = c.get_ordinance("2118171", max_chars=1000)
        assert "잘림" not in whole


# ---------------------------------------------------------------- ② 지자체 필터
def _ordin_page(names, total=150):
    return "<LawSearch><totalCnt>%d</totalCnt>" % total + "".join(
        f"<law id='{i}'><자치법규일련번호>{i}</자치법규일련번호><자치법규ID>{i + 9000}</자치법규ID>"
        f"<자치법규명><![CDATA[{n}]]></자치법규명><지자체기관명>{org}</지자체기관명>"
        f"<공포일자>20260101</공포일자><시행일자>20260101</시행일자></law>"
        for i, (n, org) in enumerate(names, 1)) + "</LawSearch>"


def test_ordinance_region_filter_scans_beyond_first_page():
    page1 = [(f"경기도 가{i}시 시세 감면 조례", f"경기도 가{i}시") for i in range(100)]   # 전부 다른 지역
    page2 = [("서울특별시 시세 감면 조례", "서울특별시")] + [(f"전라도 나{i}시 조례", f"전라도 나{i}시") for i in range(49)]

    def handler(endpoint, **p):
        return _ordin_page(page1 if p.get("page") == 1 else page2)

    with _FakeGet(handler) as g:
        out = law_go_kr.LawGoKrClient().search_ordinances("시세 감면", region="서울", display=5)
    assert [r["자치법규명"] for r in out] == ["서울특별시 시세 감면 조례"], out
    assert out[0]["일련번호"] and out[0]["자치법규ID"], "검색 결과에 일련번호와 자치법규ID를 모두 준다"
    assert [c[1].get("page") for c in g.calls] == [1, 2] and all(c[1].get("display") == 100 for c in g.calls)


def test_ordinance_region_filter_warns_when_scan_is_partial():
    page = [(f"경기도 가{i}시 조례", f"경기도 가{i}시") for i in range(100)]
    with _FakeGet(lambda e, **p: _ordin_page(page, total=1000)) as g:
        out = law_go_kr.LawGoKrClient().search_ordinances("조례", region="제주", display=5)
    assert len(g.calls) == law_go_kr.LawGoKrClient._ORDIN_SCAN_PAGES, "훑는 쪽 수에 상한이 있다"
    assert isinstance(out, dict) and out["status"] == "NOT_FOUND" and out["결과"] == []
    assert "앞쪽" in out["_guidance"] and "1000건" in out["_guidance"], "훑지 못한 뒤쪽이 있으면 '없다'고 단정하지 않는다"


def test_ordinance_region_filter_complete_scan_returns_plain_list():
    page = [(f"경기도 가{i}시 조례", f"경기도 가{i}시") for i in range(40)]
    with _FakeGet(lambda e, **p: _ordin_page(page, total=40)):
        out = law_go_kr.LawGoKrClient().search_ordinances("조례", region="제주", display=5)
    assert out == [], "끝까지 훑고 없으면 빈 목록(NOT_FOUND로 처리됨)"


def test_ordinance_without_region_is_single_request():
    with _FakeGet(lambda e, **p: _ordin_page([("강릉시 시세 감면 조례", "강원특별자치도 강릉시")])) as g:
        out = law_go_kr.LawGoKrClient().search_ordinances("시세 감면", display=7)
    assert len(g.calls) == 1 and g.calls[0][1]["display"] == 7 and out[0]["지자체"] == "강원특별자치도 강릉시"


# ---------------------------------------------------------------- ③ 국세청 컬렉션 매핑
STATUTE_NOTICE = {   # 기본통칙 — 본문이 HTML 이스케이프된 채 온다
    "DOC_ID": "100000000000001563_2024_2024438", "DATE": "20230926212739", "NM": "법인세법",
    "TEXT_KRN_NM": "52-88…1 【 청산중에 있는 법인에 대한 <!HS>부당행위계산<!HE> <!HS>부인<!HE>규정의 적용 】",
    "TEXT_KRN_CNTN": "&lt;p&gt;법 제52조 및 영 제88조의 <!HS>부당행위계산<!HE> 부인규정은 청산 중에 있는 법인에 "
                     "대하여도 적용한다. &lt;개정 2001.11.01&gt;&lt;/p&gt;",
    "LBL1_NM": "통칙", "STTT_CL_NM": "기본통칙", "PMG_DT": "20240101", "TLAW_CL_CD_CMN": "303",
}
STATUTE_LAW = {      # 법령 조 항 조각
    "DOC_ID": "100000000000011178_100028255120260101_2011", "DATE": "20260102", "NM": "조세특례제한법",
    "TEXT_KRN_NM": "제97조 제1항", "TEXT_KRN_CNTN": "① 대통령령으로 정하는 내국인이 <개정 2019.12.31.>",
    "LBL1_NM": "법령", "STTT_CL_NM": "조세", "PMG_DT": "20251223", "ENFR_DT": "20260701", "PMG_NO": "21223",
    "TLAW_CL_CD_CMN": "309",
}
APPEND_FORM = {
    "DOC_ID": "100000000000287787_100000000000007229_102006300", "DATE": "20260702022313",
    "NM": "법인세법 시행규칙", "FRML_NM": "감가상각방법<!HS>신고서<!HE>, 내용연수신고서", "STTT_CL_NM": "법령서식",
    "LBL1_NM": "서식(법)", "FILE_CN": "■ 법인세법 시행규칙 [별지 제63호서식] <개정 2025. 3. 21.>",
    "PMG_DT": "20260701", "ENFR_DT": "20260701", "PMG_NO": "00037", "NTST_FLE_ID": "701000000001204791",
    "TLAW_CL_CD_CMN": "303",
}
FORMER_LIBRARY = {
    "DOC_ID": "511100000000000006_511100000046_703619", "DATE": "20231203153525",
    "NM": "양도소득세 집행기준 2018.08.22", "NTST_PLCN_BK_TTL": "양도소득세 집행기준", "LBL1_NM": "책자",
    "NTST_JRSD_DNO_NM": "법규과", "PLCN_DT": "20180822", "NTST_FLE_ID": "300000000000839605",
    "FILE_CN": "...<!HS>부당행위계산<!HE> 적용 여부】 ····· 8 5 집행기준 101-167-10",
}
HOMETAX = {
    "DOC_ID": "1380", "DATE": "20220323175338", "REGST_DT": "20220323175338", "LBL1_NM": "상담사례",
    "STD_TITLE": "특수관계자 간 증여 후 양도에 따른 <!HS>부당행위계산<!HE>이란 무엇입니까?",
    "ANSWER_STD_CONTENT": "◆ 특수관계자 간의 증여 및 수증 후 5년 이내 양도(우회양도) …",
    "REQ_TP_NM": "양도소득세-특수한거래양도소득-증여후5년내양도부당행위계산",
}
RULING = {
    "DOC_ID": "010000000000136139", "TTL": "<!HS>부당행위계산<!HE> 부인규정 적용여부", "NTST_DCM_CL_NM": "질의",
    "NTST_DCM_DSCM_CNTN": "법인세과-842", "NTST_DCM_SRCS_ORGN_CL_CD": "01", "DCM_RGT_DTM_S": "20090722",
    "NTST_TLAW_CL_NM": "법인세", "GIST_CNTN": "요지", "NTST_FLE_ID": "100000000001346493",
}


def _clean(raw, coll, full=False):
    return nts.NtsTaxLawClient._clean_item(raw, include_full_text=full, coll=coll)


def test_statute_notice_maps_title_summary_and_unescapes_html():
    it = _clean(STATUTE_NOTICE, "statute")
    assert it["title"] == "법인세법 52-88…1 【 청산중에 있는 법인에 대한 부당행위계산 부인규정의 적용 】", it["title"]
    assert it["doc_type"] == "통칙" and it["tax_type"] == "법인세" and it["date"] == "20240101"
    assert "<p>" not in it["summary"] and "&lt;" not in it["summary"] and "<!H" not in it["summary"]
    assert "<개정 2001.11.01>" in it["summary"], "법령 개정 표기의 꺾쇠는 지우지 않는다"
    assert it["law_name"] == "법인세법" and it["doc_id"] == STATUTE_NOTICE["DOC_ID"]


def test_statute_law_fragment_keeps_angle_notes_and_adds_dates():
    it = _clean(STATUTE_LAW, "statute")
    assert it["title"] == "조세특례제한법 제97조 제1항" and it["doc_type"] == "법령"
    assert it["summary"].endswith("<개정 2019.12.31.>")
    assert it["promulgation_no"] == "21223" and it["enforce_date"] == "20260701" and it["tax_type"] == "조세특례"


def test_other_collections_fill_title_and_summary():
    form = _clean(APPEND_FORM, "appendForm")
    assert form["title"].startswith("감가상각방법신고서") and form["doc_type"] == "법령서식"
    assert form["file_id"] == "701000000001204791" and form["law_name"] == "법인세법 시행규칙"
    old = _clean(FORMER_LIBRARY, "formerLibrary")
    assert old["title"] == "양도소득세 집행기준 2018.08.22" and old["source_org"] == "법규과" and old["date"] == "20180822"
    assert "<!H" not in old["summary"] and old["file_id"] == "300000000000839605"
    ht = _clean(HOMETAX, "hometaxCnslThan")
    assert ht["title"].endswith("이란 무엇입니까?") and ht["tax_type"] == "양도소득세" and ht["doc_type"] == "상담사례"
    assert ht["summary"].startswith("◆ 특수관계자")


def test_ruling_items_unchanged_by_mapping():
    it = _clean(RULING, "question")
    assert it["title"] == "부당행위계산 부인규정 적용여부" and it["doc_no"] == "법인세과-842"
    assert it["source_org"] == "국세청" and it["tax_type"] == "법인세" and it["summary"] == "요지"
    assert not ({"law_name", "promulgation_no", "enforce_date"} & set(it)), "질의회신 항목에 법령 필드가 붙으면 안 됨"
    assert _clean(RULING, None)["title"] == it["title"], "컬렉션을 몰라도 질의회신은 그대로"


def test_collection_is_inferred_from_fields_when_unknown():
    assert _clean(STATUTE_NOTICE, None)["title"].startswith("법인세법 ")
    assert _clean(HOMETAX, None)["tax_type"] == "양도소득세"
    assert _clean(FORMER_LIBRARY, None)["source_org"] == "법규과"


def test_long_snippet_is_capped():
    raw = dict(STATUTE_LAW, TEXT_KRN_CNTN="가" * 3000)
    s = _clean(raw, "statute")["summary"]
    assert len(s) < 1100 and s.endswith("…(이하 생략)")


def test_parse_passes_collection_name():
    raw = {"data": {"ASEISA001MR01": {"searchResultVO": {"collectionList": [
        {"nameEn": "statute", "nameKr": "법령", "totalCount": 1, "resultList": [STATUTE_LAW]},
        {"nameEn": "question", "nameKr": "질의회신", "totalCount": 1, "resultList": [RULING]}]}}}}
    out = nts.NtsTaxLawClient()._parse(raw, include_full_text=False)
    assert out["statute"]["items"][0]["title"] == "조세특례제한법 제97조 제1항"
    assert out["question"]["items"][0]["doc_no"] == "법인세과-842"


# ---------------------------------------------------------------- ④ 개명된 법령의 연혁
def _eflaw_xml(rows):
    return "<LawSearch>" + "".join(
        f"<law id='{i}'><법령명한글><![CDATA[{n}]]></법령명한글><법령ID>{lid}</법령ID>"
        f"<법령일련번호>{mst}</법령일련번호><시행일자>{eff}</시행일자><공포일자>{pub}</공포일자>"
        f"<공포번호>{no}</공포번호><제개정구분명>{kind}</제개정구분명></law>"
        for i, (n, lid, mst, eff, pub, no, kind) in enumerate(rows, 1)) + "</LawSearch>"


OLD = [("상속세법", "900010", "7001", "19500322", "19500322", "00001", "제정"),
       ("상속세법", "900010", "7002", "19950101", "19941222", "04803", "일부개정")]
NEW = [("상속세및증여세법", "900010", "7003", "19970101", "19961230", "05230", "전부개정"),
       ("상속세 및 증여세법", "900010", "7004", "20260101", "20251223", "21219", "일부개정")]
OTHER = [("상속세 및 증여세법 시행령", "900011", "7100", "20260101", "20260101", "36000", "일부개정")]


def _rows(tuples):
    """연혁 튜플 → law_history가 돌려주는 행(dict), 시행일 순."""
    keys = ("법령명", "법령ID", "MST", "시행일자", "공포일자", "공포번호", "제개정구분")
    return sorted((dict(zip(keys, t)) for t in tuples), key=lambda r: r["시행일자"])


def _history_handler(name_rows, lid_rows):
    def handler(endpoint, **p):
        if p.get("page", 1) != 1:
            return "<LawSearch></LawSearch>"
        if "LID" in p:
            return _eflaw_xml(lid_rows.get(p["LID"], []))
        return _eflaw_xml(name_rows)

    return handler


def test_history_adds_rows_from_before_the_rename():
    with _FakeGet(_history_handler(NEW + OTHER, {"900010": OLD + NEW})) as g:
        rows = law_go_kr.LawGoKrClient().law_history("상속세 및 증여세법")
    assert [r["MST"] for r in rows] == ["7001", "7002", "7003", "7004"], "시행령 제외 + 개명 전 행 포함, 시행일 순"
    assert [r["법령명"] for r in rows][:2] == ["상속세법", "상속세법"]
    assert any(c[1].get("LID") == "900010" for c in g.calls)


def test_history_skips_lid_query_when_enactment_row_present():
    full = OLD + NEW
    with _FakeGet(_history_handler(full + OTHER, {})) as g:
        rows = law_go_kr.LawGoKrClient().law_history("상속세 및 증여세법")
    assert len(rows) == 4 and not any("LID" in c[1] for c in g.calls), "제정 행이 있으면 LID 왕복을 늘리지 않는다"
    assert len(g.calls) == 1, "한 쪽이 100건 미만이면 빈 쪽을 더 요청하지 않는다"


def test_history_with_law_id_uses_lid_directly_and_filters():
    mixed = OLD + NEW + OTHER
    with _FakeGet(_history_handler([], {"900010": mixed})) as g:
        rows = law_go_kr.LawGoKrClient().law_history("상속세 및 증여세법", law_id="900010")
    assert [r["MST"] for r in rows] == ["7001", "7002", "7003", "7004"], "다른 법령ID 행은 거른다"
    assert all("LID" in c[1] and "query" not in c[1] for c in g.calls), g.calls


def test_history_falls_back_to_name_when_lid_search_is_empty():
    with _FakeGet(_history_handler(NEW + OLD + OTHER, {})) as g:
        rows = law_go_kr.LawGoKrClient().law_history("상속세 및 증여세법", law_id="900010")
    assert [r["MST"] for r in rows] == ["7001", "7002", "7003", "7004"]
    assert any("query" in c[1] for c in g.calls)


def test_as_of_before_rename_returns_old_name_with_notice():
    c = law_go_kr.LawGoKrClient()
    c.law_history = lambda name, law_id="", **k: _rows(OLD + NEW)
    c.law_article = lambda mst, art, max_chars=6000, **k: {"조문": "제1조", "조문제목": "상속세부과기준", "원문": "제1조 …"}
    c._check_promulgated = lambda art, *a, **k: art
    out = c.law_article_as_of("상속세 및 증여세법", "19960101", "1")
    assert out["적용시행본"]["법령명"] == "상속세법" and out["적용시행본"]["MST"] == "7002"
    assert "상속세법" in out["주의"] and "같은 법령ID" in out["주의"], out.get("주의")
    # 개명이 없는 시점에는 안내를 붙이지 않는다
    now = c.law_article_as_of("상속세 및 증여세법", "20270101", "1")
    assert "주의" not in now, now.get("주의")


def test_as_of_scheduled_future_rename_is_not_the_current_name():
    """개명이 시행 예정(미래 행)이어도 '현재 법령명'은 오늘 시행 중인 이름이다 — 오늘 기준 조회에 개명 안내가 붙으면 안 됨."""
    future = ("새이름법", "900010", "7005", "29990101", "29981201", "99999", "일부개정")
    c = law_go_kr.LawGoKrClient()
    c.law_history = lambda name, law_id="", **k: _rows(NEW + [future])
    c.law_article = lambda mst, art, max_chars=6000, **k: {"조문": "제1조", "조문제목": "t", "원문": "x"}
    c._check_promulgated = lambda art, *a, **k: art
    assert "주의" not in c.law_article_as_of("상속세 및 증여세법", "20270101", "1")


def test_as_of_spacing_only_name_difference_is_not_a_rename():
    c = law_go_kr.LawGoKrClient()
    c.law_history = lambda name, law_id="", **k: _rows(NEW)
    c.law_article = lambda mst, art, max_chars=6000, **k: {"조문": "제1조", "조문제목": "t", "원문": "x"}
    c._check_promulgated = lambda art, *a, **k: art
    assert "주의" not in c.law_article_as_of("상속세 및 증여세법", "19980101", "1")   # 상속세및증여세법 = 띄어쓰기만 다름


# ---------------------------------------------------------------- ⑤ 조문 원문 정리
BLOCK = (
    "<조문번호>59</조문번호>\n<조문가지번호>2</조문가지번호>\n<조문여부>조문</조문여부>\n"
    "<조문제목><![CDATA[자녀세액공제]]></조문제목>\n<조문시행일자>20260701</조문시행일자>\n"
    "<조문제개정유형>일부개정</조문제개정유형>\n<조문이동이전></조문이동이전>\n<조문이동이후></조문이동이후>\n"
    "<조문변경여부>N</조문변경여부>\n<조문내용><![CDATA[제59조의2(자녀세액공제)]]></조문내용>\n"
    "<항>\n<항번호><![CDATA[①]]></항번호>\n<항내용><![CDATA[①종합소득이 있는 거주자의 자녀로서 13세 이상의 사람에 대해서는 "
    "다음 금액을 공제한다. <개정 2024.12.31, 2026.4.21>]]></항내용>\n"
    "<호>\n<호번호><![CDATA[1.]]></호번호>\n<호내용><![CDATA[1. 1명인 경우: 연 25만원]]></호내용>\n</호>\n</항>\n"
    "<항>\n<항번호><![CDATA[②]]></항번호>\n<항내용><![CDATA[번호가 본문에 없는 항]]></항내용>\n</항>\n"
)


def test_block_text_drops_metadata_lines_and_duplicate_numbers():
    t = law_go_kr.LawGoKrClient._block_text(BLOCK)
    assert t.startswith("제59조의2(자녀세액공제)"), repr(t[:40])
    for junk in ("20260701", "일부개정", "000000", "\nN\n", "조문\n"):
        assert junk not in t, (junk, t)
    assert t.count("①") == 1, "항번호와 항내용 첫머리의 ①이 두 번 나오면 안 됨"
    assert "1. 1명인 경우: 연 25만원" in t and t.count("1.") == 1
    assert "〈개정 2024.12.31, 2026.4.21〉" in t, "개정 주석은 보존"
    assert "②\n번호가 본문에 없는 항" in t.replace("\n\n", "\n"), "번호가 본문에 없으면 번호를 지우지 않는다"


def test_block_text_keeps_every_content_line():
    c = law_go_kr.LawGoKrClient
    t = "".join(c._block_text(BLOCK).split())
    for ln in c._article_content_lines(BLOCK):
        assert "".join(ln.split()) in t, ln


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
