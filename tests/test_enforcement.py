# -*- coding: utf-8 -*-
"""
test_enforcement.py — v5.9.0 오프라인 테스트: 집행기준 책자 보관소 (nts_enforcement.py)
  ① 책자 쪽 → 항목 쪼개기 (번호·제목·절 제목·본문, 본문 속 인용, 여러 법 합본, 대비표 제외)
  ② 보관소 조회 (국세청 검색이 법을 잘못 단 항목, 번호 표기 차이, 조문 단위 목록)
  ③ 갱신 (판이 같으면 안 받음, 바뀐 책만, 실패·급감 시 옛 것 유지)
  ④ 검색 결과의 빈 집행기준 본문 채우기 · nts_enforcement_standard 도구

쪽 기록은 2026-10-18 실제 책자(2024.10.31판)를 pypdf로 읽은 모양을 줄여 흉내 낸 것 — PDF·네트워크 불필요.

실행:
    python tests/test_enforcement.py
    python -m pytest tests/test_enforcement.py -q
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import nts_enforcement as ne  # noqa: E402
import nts_tax_ruling_search as nts  # noqa: E402


def _page(text, lines=()):
    """lines: [(y, [(글자, 크기), ...])] — 위에서 아래로. 실제 책자: '집행기준'·제목·절 제목 12pt, 번호·본문 11.5pt."""
    return {"text": text, "chunks": [(t, y, s) for y, parts in lines for t, s in parts]}


# 양도소득세 책 32쪽·29쪽을 줄인 것
COVER = _page("양도소득세 집행기준\n")
P_A = _page(
    "8_ 양도소득세 집행기준\n"
    "집행기준\n89-154-1\n교회 소유 1주택 양도시 비과세 여부｢소득세법｣에 따라 1거주자로 보는 교회가 주택을 "
    "양도하는 경우에는 1세대 1주택 비과세 규정이 적용되지 않는다(집행기준 89-154-2 참조).주택과 그에 딸린 토지\n"
    "집행기준\n89-154-2\n주택의 정의주택은 사실상 주거용으로 사용하는 건물을 말한다.\n",
    [(800, [("8", 11.0), ("_ ", 11.0), ("양도소득세 ", 11.0), ("집행기준", 11.0)]),
     (729.5, [("집행기준", 12.0), ("89-154-1", 11.5), ("교회 ", 12.0), ("소유 1주택 양도시 비과세 여부", 12.0)]),
     (694.0, [("｢", 11.5), ("소득세법", 11.5), ("｣에 따라 … 적용되지 않는다", 11.5), ("(", 11.5)]),
     (694.0, [("집행기준", 11.5), ("89-154-2", 11.5), (" 참조).", 11.5)]),       # 본문 속 인용 — 표시가 아니다
     (616.2, [("주택과 ", 12.0), ("그에 딸린 토지", 12.0)]),                      # 절 제목
     (560.0, [("집행기준", 12.0), ("89-154-2", 11.5), ("주택의 ", 12.0), ("정의", 12.0)]),
     (525.0, [("주택은 사실상 주거용으로 사용하는 건물을 말한다.", 11.5)])])
P_B = _page(
    "제2장 1세대 1주택 비과세 _9\n"
    "집행기준\n88-152의3-1\n1세대의 정의거주자와 그 배우자가 생계를 같이하는 가족을 1세대라고 한다.\n"
    "집행기준\n88-152의3-2\n기타소득 과세최저한의 경우 원천징수이행상황신고 및 지급명세서 제출여부과세최저한…\n",
    [(431.3, [("집행기준", 12.0), ("88-152", 11.5), ("의", 11.5), ("3-1", 11.5), ("1", 12.0), ("세대의 정의", 12.0)]),
     (396.0, [("거주자와 그 배우자가 …", 11.5)]),
     (277.6, [("집행기준", 12.0), ("88-152", 11.5), ("의", 11.5), ("3-2", 11.5),
              ("기타소득 과세최저한의 경우 원천징수이행상황신고 및 지급명세서", 12.0)]),
     (261.0, [("제출여부", 12.0)]),                                              # 두 줄로 넘어간 제목
     (230.0, [("과세최저한…", 11.5)])])


def test_split_single_law_book():
    items = ne.split_items([COVER, P_A, P_B], "양도소득세 집행기준")
    by = {i["no"]: i for i in items}
    assert list(by) == ["89-154-1", "89-154-2", "88-152의3-1", "88-152의3-2"], list(by)
    assert all(i["law"] == "소득세법" for i in items)
    a = by["89-154-1"]
    assert a["title"] == "교회 소유 1주택 양도시 비과세 여부" and a["page"] == 2
    assert a["text"].startswith("｢소득세법｣에 따라") and a["text"].endswith("참조)."), a["text"]
    assert "주택과 그에 딸린 토지" not in a["text"], "다음 항목의 절 제목이 앞 항목 본문 끝에 붙으면 안 됨"
    assert by["89-154-2"]["heading"] == "주택과 그에 딸린 토지"
    assert by["89-154-2"]["text"] == "주택은 사실상 주거용으로 사용하는 건물을 말한다."
    b = by["88-152의3-1"]
    assert b["title"] == "1세대의 정의", "번호가 조각나 오고 제목이 숫자로 시작해도 갈라야 함"
    assert b["text"].startswith("거주자와")
    c = by["88-152의3-2"]
    assert c["title"].endswith("지급명세서 제출여부"), "다음 줄로 넘어간 제목"
    assert c["text"].startswith("과세최저한"), c["text"]


# 개별소비세·인지세·… 합본 — 법마다 번호가 1-0-1부터 다시 시작한다
def _sec(header, first_line, no, title, body):
    return _page(f"{header}\n{first_line}집행기준\n{no}\n{title}{body}\n",
                 [(700, [("집행기준", 12.0), (no, 11.5), (title, 12.0)]), (670, [(body, 11.5)])])


MULTI = [
    _page("개별소비세･인지세･주세･주류면허법 집행기준\n"),
    _sec("✲ 개별소비세 _1", "✲개별소비세\n", "1-0-1", "과세대상의 의의", "개별소비세는 특정한 물품에 부과한다."),
    _sec("2_ 개별소비세･인지세･주세･주류면허법 집행기준", "", "1-0-2", "과세물품", "짝수 쪽 — 책 제목 머리글"),
    _sec("✲ 인지세 _55", "✲인지세\n", "1-0-1", "납세의무", "문서를 작성하는 자는 인지세를 낸다."),
    # 부가 짝수 쪽에서 시작한다(종합부동산세·증권거래세 합본 36쪽) — 머리글은 책 제목, 둘째 줄이 부 제목
    _sec("56_ 개별소비세･인지세･주세･주류면허법 집행기준", "주세 집행기준\n✲주세\n", "2-0-1", "불순물 알코올", "주세 본문."),
    _page("✲ 2024년 세법집행기준 신구조문 대비표(개별소비세) _135\n집행기준\n1-0-1\n신구 대비표의 칸\n"),
    _page("이 책에 실린 내용을 실무에 적용할 때는 반드시 관계법령 및관련해석 등의 원본을 찾아 확인하시기 바랍니다.\n"),
]


def test_split_multi_law_book_uses_section_titles_not_even_headers():
    items = ne.split_items(MULTI, "개별소비세, 인지세, 주세, 주류면허법 집행기준")
    got = [(i["law"], i["no"]) for i in items]
    assert got == [("개별소비세법", "1-0-1"), ("개별소비세법", "1-0-2"), ("인지세법", "1-0-1"),
                   ("주세법", "2-0-1")], got
    assert all("신구 대비표" not in i["text"] for i in items), "대비표는 항목이 아니다"


def test_cover_marks_multi_law_book_even_if_library_title_is_single():
    """전자도서관에는 종합부동산세·증권거래세 합본이 '종합부동산세 집행기준'·'증권거래세 집행기준'으로 따로 올라 있다."""
    pages = [_page("종합부동산세･증권거래세집행기준\n"),
             _sec("종합부동산세 집행기준 _1", "", "1-0-1", "목적", "종부세."),
             _sec("36_ 종합부동산세·증권거래세 집행기준", "증권거래세 집행기준\n✲증권거래세\n", "1-0-1", "의의", "증권.")]
    items = ne.split_items(pages, "증권거래세 집행기준")
    assert [(i["law"], i["no"]) for i in items] == [("종합부동산세법", "1-0-1"), ("증권거래세법", "1-0-1")]


def test_number_pattern_covers_ranges_and_branch_serials():
    import re
    for no in ("89-154-1", "88-152의3-12", "44-0-3의2", "75∼75의9-0-1", "104의3-168의14-38"):
        assert re.fullmatch(ne.NO_RE, no), no
    assert ne.normalize_no("75~75의9-0-1") == "75∼75의9-0-1" and ne.normalize_no("제89조-154-1") == "89-154-1"


def test_normalize_law_aliases():
    cases = {"양도소득세": "소득세법", "소득세법 시행령": "소득세법", "상증법": "상속세 및 증여세법",
             "주류면허법": "주류 면허 등에 관한 법률", "국조법": "국제조세조정에 관한 법률", "부가세": "부가가치세법",
             "종부세": "종합부동산세법", "조특법": "조세특례제한법", "국세기본법": "국세기본법"}
    for k, v in cases.items():
        assert ne.normalize_law(k) == v, (k, ne.normalize_law(k))
    assert ne.normalize_law("지방세법") is None


# ---------------------------------------------------------------- ② 조회
def _item(law, no, title, text="본문", book="책", edition="2024.10.31", file_id="F1", page=1, heading=None):
    return {"law": law, "no": no, "title": title, "heading": heading, "text": text, "page": page,
            "book": book, "edition": edition, "file_id": file_id}


ITEMS = [
    _item("국세기본법", "12-0-1", "송달의 효력발생", "국세기본법 본문", "국세기본법 집행기준"),
    _item("주류 면허 등에 관한 법률", "12-0-1", "면허의 양도･대여 등에 대한 처리", "주류면허 본문", "개별소비세… 집행기준"),
    _item("법인세법", "75∼75의9-0-1", "법인세법상 가산세 종류", "가산세 표", "법인세 집행기준"),
    _item("소득세법", "89-154-2", "주택의 정의", "주택은 …", "양도소득세 집행기준"),
    _item("소득세법", "89-154-1", "교회 소유 1주택", "교회 …", "양도소득세 집행기준"),
    _item("소득세법", "89-155-1", "일시적 2주택", "일시적 …", "양도소득세 집행기준"),
    _item("소득세법", "104의3-168의14-1", "비사업용 토지", "비사업용 …", "양도소득세 집행기준"),
    # 합본이 두 이름으로 올라 있어 같은 항목이 두 번 — 하나로 본다
    _item("증권거래세법", "1-0-2", "과세대상", "증권 본문", "종합부동산세 집행기준", file_id="F9"),
    _item("증권거래세법", "1-0-2", "과세대상", "증권 본문", "증권거래세 집행기준", file_id="F10"),
]


def _store(items=ITEMS, checked_at=None):
    d = tempfile.mkdtemp()
    path = Path(d) / "enforcement.json"
    data = {"version": 1, "checked_at": checked_at or ne._now(), "books": [], "items": items}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return ne.EnforcementStore(path)


def test_find_prefers_title_over_mislabeled_law():
    s = _store()
    assert s.find("국세기본법", "12-0-1")["title"] == "송달의 효력발생"
    hit = s.find("국세기본법", "12-0-1", "면허의 양도･대여 등에 대한 처리")
    assert hit["law"] == "주류 면허 등에 관한 법률", "국세청 검색이 주류면허법 항목을 국세기본법으로 달아 둔 경우"


def test_find_by_title_when_number_is_written_differently():
    hit = _store().find("법인세법", "75-75의9-1", "법인세법상 가산세 종류")   # 검색 표기 ↔ 책 '75∼75의9-0-1'
    assert hit and hit["no"] == "75∼75의9-0-1"
    assert _store().find("법인세법", "75-75의9-1") is None


def test_browse_orders_numbers_and_respects_prefix():
    s = _store()
    assert [x["no"] for x in s.browse("소득세법", "89")] == ["89-154-1", "89-154-2", "89-155-1"]
    assert [x["no"] for x in s.browse("소득세법", "89-154")] == ["89-154-1", "89-154-2"]
    assert [x["no"] for x in s.browse("소득세법", "104의3")] == ["104의3-168의14-1"]
    assert len(s.browse("증권거래세법")) == 1, "합본 중복은 하나로"


def test_store_reloads_after_file_changes():
    s = _store()
    assert s.find("소득세법", "89-155-1")
    data = json.loads(s.path.read_text(encoding="utf-8"))
    data["items"] = [i for i in data["items"] if i["no"] != "89-155-1"]
    s.path.write_text(json.dumps(data, ensure_ascii=False) + " ", encoding="utf-8")   # 크기가 바뀌게
    assert s.find("소득세법", "89-155-1") is None


def test_needs_refresh_uses_checked_at():
    old = (datetime.now(ne.KST) - timedelta(days=40)).isoformat(timespec="seconds")
    assert ne.needs_refresh(30, _store(checked_at=old).path)
    assert not ne.needs_refresh(30, _store().path)
    assert ne.needs_refresh(30, Path(tempfile.mkdtemp()) / "none.json")
    assert ne.start_background_refresh(lambda: None, 0) is None, "0이면 끈다"


# ---------------------------------------------------------------- ③ 갱신
class _FakeLibrary:
    """전자도서관 검색·붙임 내려받기 흉내. books: [(책 제목, 판, file_id)]."""

    def __init__(self, books):
        self.books, self.downloads = books, []

    def _post_search(self, param):
        rows = [{"NTST_PLCN_BK_TTL": t, "PLCN_DT": ed, "NTST_FLE_ID": fid} for t, ed, fid in self.books]
        rows.append({"NTST_PLCN_BK_TTL": "세법해석 사례집", "PLCN_DT": "20251120", "NTST_FLE_ID": "X"})
        rows.append({"NTST_PLCN_BK_TTL": "양도소득세 집행기준", "PLCN_DT": "20221028", "NTST_FLE_ID": "OLD"})
        start = param["startCount"]
        page = rows[start - 1:start - 1 + int(param["viewCount"])]
        return {"data": {"ASEISA001MR01": {"searchResultVO": {"collectionList": [
            {"nameEn": "formerLibrary", "totalCount": len(rows), "resultList": page}]}}}}

    def get_attachment_meta(self, fid):
        return [{"fleXsnNm": "pdf", "fleDwldUri": f"/dl/{fid}"}]

    def download_attachment(self, uri):
        self.downloads.append(uri)
        return uri.encode()


def _parse_n(n):
    def parse(pdf, title):
        return [_item("소득세법", f"1-0-{k}", f"제목{k}", f"{pdf.decode()} {k}") for k in range(1, n + 1)]
    return parse


def test_refresh_downloads_only_changed_books():
    path = Path(tempfile.mkdtemp()) / "e.json"
    lib = _FakeLibrary([("양도소득세 집행기준", "20241031", "A1"), ("법인세 집행기준", "20241031", "B1")])
    rep = ne.refresh(lib, path, parse=_parse_n(3))
    assert len(rep["updated"]) == 2 and sorted(lib.downloads) == ["/dl/A1", "/dl/B1"], "옛 판(OLD)·사례집은 받지 않는다"
    data = ne.load_data(path)
    assert {b["edition"] for b in data["books"]} == {"20241031"} and len(data["items"]) == 6
    assert data["items"][0]["edition"] == "2024.10.31" and data["items"][0]["book"] == "법인세 집행기준"

    lib2 = _FakeLibrary([("양도소득세 집행기준", "20241031", "A1"), ("법인세 집행기준", "20261031", "B2")])
    rep = ne.refresh(lib2, path, parse=_parse_n(4))
    assert lib2.downloads == ["/dl/B2"] and rep["unchanged"] == ["양도소득세 집행기준"]
    assert len(ne.load_data(path)["items"]) == 3 + 4


def test_refresh_keeps_old_book_when_parse_collapses_or_fails():
    path = Path(tempfile.mkdtemp()) / "e.json"
    ne.refresh(_FakeLibrary([("양도소득세 집행기준", "20241031", "A1")]), path, parse=_parse_n(10))
    rep = ne.refresh(_FakeLibrary([("양도소득세 집행기준", "20261031", "A2")]), path, parse=_parse_n(2))
    assert rep["kept_old"] and len(ne.load_data(path)["items"]) == 10, "절반 넘게 줄면 형식이 바뀐 신호"

    def boom(pdf, title):
        raise RuntimeError("PDF 깨짐")
    rep = ne.refresh(_FakeLibrary([("양도소득세 집행기준", "20261031", "A2")]), path, parse=boom)
    assert rep["failed"] and len(ne.load_data(path)["items"]) == 10
    assert ne.load_data(path)["books"][0]["file_id"] == "A1", "실패하면 다음에 다시 받도록 옛 판 기록을 둔다"


def test_refresh_with_empty_listing_does_not_wipe_store():
    path = Path(tempfile.mkdtemp()) / "e.json"
    ne.refresh(_FakeLibrary([("양도소득세 집행기준", "20241031", "A1")]), path, parse=_parse_n(5))
    before = path.read_text(encoding="utf-8")
    empty = _FakeLibrary([])
    empty._post_search = lambda p: {"data": {"ASEISA001MR01": {"searchResultVO": {"collectionList": [
        {"nameEn": "formerLibrary", "totalCount": 0, "resultList": []}]}}}}
    try:
        ne.refresh(empty, path)
    except ValueError:
        pass
    else:
        raise AssertionError("목록이 비면 ValueError")
    assert path.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------- ④ 검색 결과 채우기 · 도구
def _statute_result(*items):
    return {"statute": {"name_kr": "법령", "total_count": len(items), "items": list(items)}}


def _enf(law, rest):
    return {"title": f"{law} {rest}", "doc_type": "집행기준", "summary": None, "law_name": law}


def test_fill_enforcement_bodies_from_store():
    res = _statute_result(_enf("소득세법", "89-154-1 교회 소유 1주택"),
                          _enf("국세기본법", "12-0-1 면허의 양도･대여 등에 대한 처리"),
                          {"title": "법인세법 52-88…1 통칙", "doc_type": "기본통칙", "summary": "통칙 본문"})
    nts.NtsTaxLawClient._fill_enforcement(res, _store())
    a, b, c = res["statute"]["items"]
    assert a["summary"] == "교회 …" and a["source_book"] == "양도소득세 집행기준 2024.10.31판"
    assert b["law_name"] == "주류 면허 등에 관한 법률" and b["title"].startswith("주류 면허 등에 관한 법률 12-0-1")
    assert c["summary"] == "통칙 본문" and "source_book" not in c
    assert "책자" in res["statute"]["안내"] and "채우지 못해" not in res["statute"]["안내"]


def test_fill_enforcement_notes_missing_bodies():
    res = _statute_result(_enf("소득세법", "1-0-99 없는 번호"))
    nts.NtsTaxLawClient._fill_enforcement(res, _store())
    assert res["statute"]["items"][0]["summary"] is None and "채우지 못해" in res["statute"]["안내"]
    empty = _statute_result({"title": "법인세법 52-88…1", "doc_type": "기본통칙", "summary": "x"})
    nts.NtsTaxLawClient._fill_enforcement(empty, _store())
    assert "안내" not in empty["statute"], "집행기준이 없으면 안내도 없다"


def _with_store(store, fn):
    orig = ne._default_store
    ne._default_store = store
    try:
        return fn()
    finally:
        ne._default_store = orig


def test_tool_full_item_list_and_toc():
    import server
    s = _store()
    one = _with_store(s, lambda: server.nts_enforcement_standard("양도소득세", "89-154-1"))
    assert one["status"] == "OK" and one["본문"] == "교회 …" and "2024.10.31판" in one["출처"] and "안내" in one
    lst = _with_store(s, lambda: server.nts_enforcement_standard("소득세법", "89", max_items=2))
    assert [x["번호"] for x in lst["항목"]] == ["89-154-1", "89-154-2"] and lst["건수"] == 3 and "잘림" in lst
    toc = _with_store(s, lambda: server.nts_enforcement_standard("소득세법"))
    assert toc["조문별_건수"] == {"89": 3, "104의3": 1} and toc["총건수"] == 4


def test_tool_errors():
    import server
    s = _store()
    bad = _with_store(s, lambda: server.nts_enforcement_standard("지방세법", "1-0-1"))
    assert bad["status"] == "INVALID_INPUT" and "소득세법" in bad["가능"]
    nf = _with_store(s, lambda: server.nts_enforcement_standard("소득세법", "89-154-9"))
    assert nf["status"] == "NOT_FOUND" and "89-154-1" in nf["_guidance"], "같은 조문의 번호를 알려 준다"
    empty = ne.EnforcementStore(Path(tempfile.mkdtemp()) / "none.json")
    out = _with_store(empty, lambda: server.nts_enforcement_standard("소득세법", "89-154-1"))
    assert out["status"] == "UPSTREAM_ERROR" and "없다는 뜻이 아닙니다" in out["_guidance"]


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
