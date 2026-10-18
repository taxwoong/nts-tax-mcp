# -*- coding: utf-8 -*-
"""
test_statute_types.py — v5.9.0 오프라인 테스트
  국세청 법령(statute) 컬렉션의 종류 필터(statute_types)와 집행기준 본문 없음 안내

statute에는 법령 조문·기본통칙·집행기준·훈령·고시·조세조약이 섞여 온다. 검색 요청의 mainIdCtl에 MAIN_ID를
주면 서버가 그 종류만 돌려주지만, mainIdCtl은 요청 전체에 걸려 같은 요청의 다른 컬렉션이 0건이 된다
(2026-10-18 실측). 아래 가짜 서버는 그 동작을 흉내 낸다 — taxlaw에는 접속하지 않는다.

실행:
    python tests/test_statute_types.py
    python -m pytest tests/test_statute_types.py -q
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import nts_enforcement  # noqa: E402
import nts_tax_ruling_search as nts  # noqa: E402

# 집행기준 본문 채우기는 test_enforcement.py가 본다 — 여기서는 빈 보관소로 (서버의 data/를 읽지 않게)
nts_enforcement._default_store = nts_enforcement.EnforcementStore(Path(tempfile.mkdtemp()) / "none.json")

# 2026-10-18 '부당행위계산' 실응답에서 줄인 것
NOTICE = {
    "DOC_ID": "100000000000001563_2024_2024438", "MAIN_ID": "BM001_04", "NM": "법인세법",
    "TEXT_KRN_NM": "52-88…1 【 청산중에 있는 법인에 대한 <!HS>부당행위계산<!HE> 부인규정의 적용 】",
    "TEXT_KRN_CNTN": "&lt;p&gt;법 제52조 및 영 제88조의 부당행위계산 부인규정은 청산 중에 있는 법인에 대하여도 "
                     "적용한다.&lt;/p&gt;",
    "LBL1_NM": "통칙", "STTT_CL_NM": "기본통칙", "PMG_DT": "20240101", "TLAW_CL_CD_CMN": "303",
}
ENFORCEMENT = {      # 집행기준 — 본문(TEXT_KRN_CNTN)이 아예 없다
    "DOC_ID": "100000000000001565_2024_2024647", "MAIN_ID": "BM001_05", "NM": "소득세법",
    "TEXT_KRN_NM": "41-0-1  <!HS>부당행위계산<!HE>", "LBL1_NM": "집행", "STTT_CL_NM": "집행기준",
    "PMG_DT": "20240101", "TLAW_CL_CD_CMN": "305",
}
LAW = {
    "DOC_ID": "100000000000011178_100028255120260101_2011", "MAIN_ID": "LBM001_999", "NM": "법인세법",
    "TEXT_KRN_NM": "<!HS>부당행위계산<!HE>의 부인", "TEXT_KRN_CNTN": "① 납세지 관할 세무서장 또는 …",
    "LBL1_NM": "법령", "STTT_CL_NM": "조세", "PMG_DT": "20251223", "TLAW_CL_CD_CMN": "303",
}
TREATY = {
    "DOC_ID": "t1", "MAIN_ID": "BL027_00", "NM": "바하마", "TEXT_KRN_NM": "요청거부 가능성",
    "TEXT_KRN_CNTN": "…", "LBL1_NM": "조약", "STTT_CL_NM": "조세조약",
}
RULING = {
    "DOC_ID": "010000000000136139", "TTL": "부당행위계산 부인규정 적용여부", "NTST_DCM_CL_NM": "질의",
    "NTST_DCM_DSCM_CNTN": "법인세과-842", "NTST_DCM_SRCS_ORGN_CL_CD": "01", "DCM_RGT_DTM_S": "20090722",
    "NTST_TLAW_CL_NM": "법인세", "GIST_CNTN": "요지",
}
DATA = {"statute": [NOTICE, LAW, ENFORCEMENT, TREATY], "question": [RULING], "precedent": []}


def _client(data=DATA):
    """_post_search를 가짜 서버로 바꿔 끼운 클라이언트. calls에 보낸 paramData가 쌓인다."""
    c = nts.NtsTaxLawClient()
    c.calls = []

    def fake(param):
        c.calls.append(param)
        mids = param["mainIdCtl"]
        colls = []
        for name in param["collection"].split(","):
            items = data.get(name, [])
            if mids:   # 실측: mainIdCtl은 요청 전체에 걸린다 — statute 밖은 0건
                items = [it for it in items if it.get("MAIN_ID") in mids]
            colls.append({"nameEn": name, "nameKr": name, "totalCount": len(items), "resultList": items})
        return {"data": {"ASEISA001MR01": {"searchResultVO": {"collectionList": colls}}}}

    c._post_search = fake
    return c


def test_statute_types_alone_searches_statute_only_with_main_id():
    c = _client()
    out = c.search("부당행위계산", statute_types=["집행기준"], use_cache=False)
    assert len(c.calls) == 1 and c.calls[0]["collection"] == "statute"
    assert c.calls[0]["mainIdCtl"] == ["BM001_05"]
    st = out["statute"]
    assert [it["doc_type"] for it in st["items"]] == ["집행기준"] and st["total_count"] == 1
    assert st["statute_types"] == ["집행기준"]
    assert set(out) == {"statute", "status"}, "다른 컬렉션이 끼면 안 됨"


def test_statute_types_with_other_collections_splits_requests():
    """같은 요청에 mainIdCtl을 실으면 질의회신이 0건이 된다 — statute만 따로 요청해야 한다."""
    c = _client()
    out = c.search("부당행위계산", collections=["question", "statute"],
                   statute_types=["기본통칙", "집행기준"], use_cache=False)
    assert len(c.calls) == 2
    by_coll = {p["collection"]: p["mainIdCtl"] for p in c.calls}
    assert by_coll == {"statute": ["BM001_04", "BM001_05"], "question": []}, by_coll
    assert out["question"]["total_count"] == 1 and out["question"]["items"][0]["doc_no"] == "법인세과-842"
    assert [it["doc_type"] for it in out["statute"]["items"]] == ["기본통칙", "집행기준"]
    assert [k for k in out if k in ("question", "statute")] == ["question", "statute"], "요청한 순서 유지"


def test_statute_types_adds_statute_when_collections_omit_it():
    c = _client()
    out = c.search("부당행위계산", collections=["question"], statute_types=["기본통칙"], use_cache=False)
    assert "statute" in out and "question" in out
    assert {p["collection"] for p in c.calls} == {"statute", "question"}


def test_without_statute_types_is_one_request_with_empty_main_id():
    c = _client()
    out = c.search("부당행위계산", use_cache=False)
    assert len(c.calls) == 1 and c.calls[0]["mainIdCtl"] == []
    assert c.calls[0]["collection"] == ",".join(nts.ALL_COLLECTIONS)
    assert out["statute"]["total_count"] == 4 and "statute_types" not in out["statute"]


def test_unknown_statute_type_is_rejected():
    c = _client()
    try:
        c.search("부당행위계산", statute_types=["예규"], use_cache=False)
    except ValueError as e:
        assert "기본통칙" in str(e) and "예규" in str(e)
    else:
        raise AssertionError("없는 종류는 ValueError여야 함")
    assert not c.calls, "잘못된 값이면 요청을 보내지 않는다"


def test_doc_type_uses_statute_type_names():
    names = {nts.NtsTaxLawClient._clean_item(raw, include_full_text=False, coll="statute")["doc_type"]
             for raw in (NOTICE, ENFORCEMENT, LAW, TREATY)}
    assert names == {"기본통칙", "집행기준", "법령", "조세조약"}
    assert set(nts.STATUTE_TYPES) >= names
    no_main_id = {k: v for k, v in NOTICE.items() if k != "MAIN_ID"}
    assert nts.NtsTaxLawClient._clean_item(no_main_id, coll="statute")["doc_type"] == "통칙", "MAIN_ID 없으면 LBL1_NM"


def test_enforcement_without_body_gets_notice():
    out = _client().search("부당행위계산", statute_types=["집행기준"], use_cache=False)
    it = out["statute"]["items"][0]
    assert it["title"] == "소득세법 41-0-1 부당행위계산" and it["summary"] is None
    assert "번호·제목만" in out["statute"]["안내"]


def test_no_notice_when_no_enforcement_item():
    out = _client().search("부당행위계산", statute_types=["기본통칙"], use_cache=False)
    assert "안내" not in out["statute"]
    with_body = dict(ENFORCEMENT, TEXT_KRN_CNTN="본문이 있는 경우")
    out = _client({"statute": [with_body]}).search("x", statute_types=["집행기준"], use_cache=False)
    assert "안내" not in out["statute"], "본문이 오면 안내하지 않는다"


def test_statute_types_is_part_of_cache_key():
    c = _client()
    a = c.search("부당행위계산", statute_types=["기본통칙"])
    b = c.search("부당행위계산", statute_types=["집행기준"])
    assert len(c.calls) == 2 and a["statute"]["items"][0]["doc_type"] != b["statute"]["items"][0]["doc_type"]


def test_server_tool_returns_invalid_input_for_unknown_type():
    import server
    out = server.nts_ruling_search("부당행위계산", statute_types=["예규"])
    assert out["status"] == "INVALID_INPUT" and "statute_types" in out["오류"]


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
