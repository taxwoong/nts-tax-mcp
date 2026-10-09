# -*- coding: utf-8 -*-
"""
test_law_eflaw.py — 시행본 문안을 시행일 기준 통합본(target=eflaw)으로 받는지 (오프라인)

2026-10-09 실측: 시행본 문안을 lawService target=law&MST=…(그 MST 공포 당시 원문)로 받아,
같은 MST가 더 늦은 시행일자 행으로 현행이 되면 그 MST보다 늦게 공포됐지만 먼저 시행된 개정이
통째로 빠졌다(상속세 및 증여세법 2025.12.23 개정 10개 조문, 소득세법 2026.4.21 개정 등).
law.go.kr 응답(_get)을 가짜 XML로 바꿔 끼워 확인한다. 법령·MST·공포번호는 모두 시험용 가짜 값.
'오늘'(law_go_kr._today_kst)도 가짜 값으로 고정한다 — 현행/과거 판정과 '아직 시행 전' 판정이 실행 날짜에
따라 달라지지 않게.

실행:
    python tests/test_law_eflaw.py
    python -m pytest tests/test_law_eflaw.py -q
"""
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import law_go_kr  # noqa: E402

NO_MATCH = ('<?xml version="1.0" encoding="utf-8"?><Law>일치하는 법령이 없습니다.  '
            '법령명을 확인하여 주십시오.</Law>')  # law.go.kr 실제 응답 형태


def _list_xml(rows):
    return "<LawSearch>" + "".join(
        f"<law id='{i}'><법령명한글><![CDATA[{r['법령명']}]]></법령명한글><법령ID>{r['법령ID']}</법령ID>"
        f"<법령일련번호>{r['MST']}</법령일련번호><시행일자>{r['시행일자']}</시행일자>"
        f"<공포일자>{r['공포일자']}</공포일자><공포번호>{r['공포번호']}</공포번호>"
        f"<제개정구분명>{r['제개정구분']}</제개정구분명></law>"
        for i, r in enumerate(rows, 1)) + "</LawSearch>"


def _law_xml(name, law_id, pub, no, eff, articles, addenda, amend=""):
    """articles: [(조번호, 제목, 본문, 조문참고자료)] — 조번호 '11의2'는 가지조문,
    addenda: [(공포일자, 공포번호, 본문)]"""
    def unit(n, t, body, ref):
        base, _, br = n.partition("의")
        label = f"제{base}조" + (f"의{br}" if br else "")
        return (f"<조문단위 조문키='{n}'><조문번호>{base}</조문번호>"
                + (f"<조문가지번호>{br}</조문가지번호>" if br else "")
                + f"<조문여부>조문</조문여부><조문제목><![CDATA[{t}]]></조문제목><조문시행일자>{eff}</조문시행일자>"
                f"<조문내용><![CDATA[{label}({t}) {body}]]></조문내용>"
                + (f"<조문참고자료><![CDATA[{ref}]]></조문참고자료>" if ref else "") + "</조문단위>")
    arts = "".join(unit(*a) for a in articles)
    adds = "".join(
        f"<부칙단위 부칙키='{d}{k}'><부칙공포일자>{d}</부칙공포일자><부칙공포번호>{k}</부칙공포번호>"
        f"<부칙내용><![CDATA[{b}]]></부칙내용></부칙단위>" for d, k, b in addenda)
    return (f"<?xml version='1.0' encoding='UTF-8'?><법령><기본정보><법령ID>{law_id}</법령ID>"
            f"<공포일자>{pub}</공포일자><공포번호>{no}</공포번호>"
            f"<법령명_한글><![CDATA[{name}]]></법령명_한글><시행일자>{eff}</시행일자>"
            f"<제개정구분>일부개정</제개정구분></기본정보><조문>{arts}</조문><부칙>{adds}</부칙>"
            f"<개정문><개정문내용><![CDATA[{amend}]]></개정문내용></개정문></법령>")


def _row(mst, eff, pub, no, kind="일부개정", name="가법", law_id="900001"):
    return {"법령명": name, "법령ID": law_id, "MST": mst, "시행일자": eff, "공포일자": pub,
            "공포번호": no, "제개정구분": kind}


class FakeLawGoKr:
    """law_go_kr._get 대역. docs 키: ("law", MST) 또는 ("eflaw", MST, efYd). today: 가짜 '오늘'(YYYYMMDD)."""

    def __init__(self, history, current, docs, errors=None, today="20251015"):
        self.history, self.current, self.docs = history, current, docs
        self.errors = errors or {}
        self.today = today
        self.calls = []

    def __call__(self, endpoint, **params):
        t = params.get("target")
        key = ("eflaw", params.get("MST"), params.get("efYd")) if t == "eflaw" else ("law", params.get("MST"))
        self.calls.append((endpoint,) + key)
        if endpoint == "lawSearch.do":
            if t == "eflaw":
                return _list_xml(self.history) if params.get("page", 1) == 1 else "<LawSearch></LawSearch>"
            return _list_xml(self.current)
        if key in self.errors:
            raise self.errors[key]
        return self.docs.get(key, NO_MATCH)

    def __enter__(self):
        self.orig = law_go_kr._get
        self.orig_today = law_go_kr._today_kst
        law_go_kr._get = self
        law_go_kr._today_kst = lambda: self.today
        return law_go_kr.LawGoKrClient()

    def __exit__(self, *exc):
        law_go_kr._get = self.orig
        law_go_kr._today_kst = self.orig_today
        return False


def _raises(exc_type, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc_type as e:
        return e
    raise AssertionError(f"{exc_type.__name__}가 나야 함")


# ---------------------------------------------------------------------------
# '늦게 공포·먼저 시행' — 가법 (법령ID 900001), 오늘 = 2025.10.15
#   제1000호(2024.12.31 공포): 2025.1.1 시행, 단 제7조는 2025.10.1 시행 (단계 시행)
#   제1100호(2025.6.1 공포):   2025.7.1 시행, 제5조 세율 20% → 15%
#   제1200호(2025.9.1 공포):   2027.1.1 시행 — 오늘 현재 공포됐지만 아직 시행 전
# 연혁(시행일자 순): 100@20250101 · 200@20250701 · 100@20251001 ← 2025.10.15 현행 = 옛 MST 100
# MST 100 공포 당시 원문에는 제1100호 개정(15%)이 없다 — 통합본에는 있다.
# ---------------------------------------------------------------------------
ADD_1000 = ("20241231", "1000", "부칙 <제1000호,2024.12.31>\n제1조(시행일) 이 법은 2025년 1월 1일부터 "
                                "시행한다. 다만, 제7조의 개정규정은 2025년 10월 1일부터 시행한다.")
ADD_1100 = ("20250601", "1100", "부칙 <제1100호,2025.6.1>\n제1조(시행일) 이 법은 2025년 7월 1일부터 시행한다.\n"
                                "제2조(세율에 관한 적용례) 제5조제1항의 개정규정은 이 법 시행 이후 발생하는 소득분부터 적용한다.")
ADD_1200 = ("20250901", "1200", "부칙 <제1200호,2025.9.1>\n제1조(시행일) 이 법은 2027년 1월 1일부터 시행한다.\n"
                                "제2조(세율에 관한 경과조치) 이 법 시행 전에 발생한 소득에 대해서는 제5조의 개정규정에도 "
                                "불구하고 종전의 규정에 따른다.")
RATE_20 = "세율은 100분의 20으로 한다. <개정 2024.12.31>"
RATE_15 = "세율은 100분의 15로 한다. <개정 2024.12.31, 2025.6.1>"
DUE_2M = "신고기한은 2개월로 한다."
DUE_3M = "신고기한은 3개월로 한다. <개정 2024.12.31>"


def _late_promulgation_fake(**over):
    history = [_row("100", "20250101", "20241231", "1000"),
               _row("200", "20250701", "20250601", "1100"),
               _row("100", "20251001", "20241231", "1000"),
               _row("300", "20270101", "20250901", "1200")]   # 공포됐지만 아직 시행 전
    current = [_row("100", "20251001", "20241231", "1000")]   # 현행 검색 결과도 연혁 행과 같다
    x = lambda mst, pub, no, eff, rate, due, adds, ref="": _law_xml(  # noqa: E731
        "가법", "900001", pub, no, eff, [("5", "세율", rate, ref), ("7", "신고기한", due, "")], adds)
    docs = {
        # 공포 당시 원문 — 자기보다 앞서 공포된 개정만, 시행 전 문안(제7조 3개월)도 미리 들어 있다
        ("law", "100"): x("100", "20241231", "1000", "20250101", RATE_20, DUE_3M, [ADD_1000]),
        ("law", "200"): x("200", "20250601", "1100", "20250701", RATE_15, DUE_3M, [ADD_1000, ADD_1100]),
        ("law", "300"): x("300", "20250901", "1200", "20270101", RATE_15, DUE_3M,
                          [ADD_1000, ADD_1100, ADD_1200]),
        # 시행일 기준 통합본 — 그날 시행 중인 문안과 그날까지 시행된 개정의 부칙
        ("eflaw", "100", "20250101"): x("100", "20241231", "1000", "20250101", RATE_20, DUE_2M, [ADD_1000]),
        ("eflaw", "200", "20250701"): x("200", "20250601", "1100", "20250701", RATE_15, DUE_2M,
                                        [ADD_1000, ADD_1100]),
        ("eflaw", "100", "20251001"): x("100", "20241231", "1000", "20251001", RATE_15, DUE_3M,
                                        [ADD_1000, ADD_1100], over.pop("ref", "")),
    }
    docs.update(over.pop("docs", {}))
    for k in over.pop("drop", []):
        docs.pop(k)
    return FakeLawGoKr(history, current, docs, errors=over.pop("errors", None), today=over.pop("today", "20251015"))


def test_current_article_includes_later_promulgated_amendment():
    fake = _late_promulgation_fake()
    with fake as client:
        art = client.law_article_as_of("가법", "20251015", "5")
        legacy = client.law_article("100", "5")   # MST만 주면 공포 당시 원문 (예전 동작)
    assert "100분의 15" in art["원문"], art["원문"]
    assert (art["적용시행본"]["MST"], art["적용시행본"]["시행일자"]) == ("100", "20251001"), art["적용시행본"]
    assert "주의" not in art, art.get("주의")
    assert ("lawService.do", "eflaw", "100", "20251001") in fake.calls, fake.calls
    assert ("lawService.do", "law", "100") not in fake.calls[:-1], "도구 경로가 공포 당시 원문을 요청함"
    assert "100분의 20" in legacy["원문"], "시험 전제(공포 당시 원문에는 제1100호 개정 없음)가 깨짐"


def test_consolidated_article_has_no_misleading_article_date_and_keeps_notes():
    """통합본의 <조문시행일자>는 늘 시행본 날짜라 '조문시행일자'로 내보내지 않는다 (공포 당시 원문에서는 그대로).
    원문의 개정 주석 '<개정 2024.12.31, 2025.6.1>'은 태그로 지워지지 않고 〈…〉로 남는다."""
    with _late_promulgation_fake() as client:
        art = client.law_article_as_of("가법", "20251015", "5")
        legacy = client.law_article("100", "5")
    assert "조문시행일자" not in art, art
    assert legacy.get("조문시행일자") == "20250101", legacy
    assert "〈개정 2024.12.31, 2025.6.1〉" in art["원문"], art["원문"]


def test_staged_text_not_yet_effective_is_not_returned():
    """2025.8.1 기준 제7조: 제1000호의 2025.10.1 시행분(3개월)은 아직 시행 전 — 옛 경로는
    MST 200 공포 당시 원문(3개월이 미리 들어 있음)을 줬다."""
    with _late_promulgation_fake() as client:
        art = client.law_article_as_of("가법", "20250801", "7")
    assert "2개월" in art["원문"] and "3개월" not in art["원문"], art["원문"]
    assert art["적용시행본"]["MST"] == "200"


def test_article_no_forms_are_normalized_and_bad_forms_are_invalid_input():
    """'제5조'·'5조'·'05'는 같은 조문, '5-2'·'제5조제1항'은 INVALID_INPUT(LawGoKrError) — NOT_FOUND(부존재)로
    나가면 '그런 조문은 없다'로 읽힌다 (2026-10-09 실측: '제59조의2' → NOT_FOUND '제제59조의2조')."""
    with _late_promulgation_fake() as client:
        for a in ("제5조", "5조", "05", " 5 "):
            assert client.law_article_as_of("가법", "20251015", a)["조문"] == "제5조", a
        for bad in ("5-2", "제5조제1항", "오조", ""):
            e = _raises(law_go_kr.LawGoKrError, client.law_article_as_of, "가법", "20251015", bad)
            assert not isinstance(e, law_go_kr.LawGoKrNotFound), (bad, e)
    assert law_go_kr._norm_article_no("제76조의 2") == "76의2"
    assert law_go_kr._norm_article_no("013의02") == "13의2"


def test_name_must_match_exactly_without_law_id():
    """law_id 없이 이름이 정확히 같은 법령이 연혁에 없으면 다른 법령 조문을 OK로 내지 않는다(INVALID_INPUT)."""
    with _late_promulgation_fake() as client:
        e = _raises(law_go_kr.LawGoKrError, client.law_article_as_of, "가", "20251015", "5")
        assert not isinstance(e, law_go_kr.LawGoKrNotFound) and "가법" in str(e), e
        # 공백·가운뎃점만 다른 이름은 같은 법령
        assert client.law_article_as_of("가 법", "20251015", "5")["조문"] == "제5조"


def test_diff_finds_late_promulgated_amendment_and_its_addendum():
    with _late_promulgation_fake() as client:
        out = client.law_article_diff("가법", "5", "20250315", "20251015")
    assert out["변경여부"] is True and out["변경유형"] == "개정", out
    start = out["현행_문안_시작"]
    assert (start["MST"], start["시행일자"], start["공포번호"]) == ("200", "20250701", "1100"), start
    assert "이_조문의_시행일자" not in start, start
    assert out["개정_부칙"]["공포번호"] == "1100", out["개정_부칙"]
    assert any("제5조" in p for p in out["개정_부칙"].get("이_조문_언급_항", [])), out["개정_부칙"]
    assert "주의" not in out, out.get("주의")


def test_same_mst_different_dates_are_different_versions():
    """같은 MST(100)라도 20250101 행과 20251001 행은 문안이 다르다 — MST만 보고 '변경 없음'으로 끝내면 안 된다."""
    with _late_promulgation_fake() as client:
        out = client.law_article_diff("가법", "7", "20250315", "20251015")
    assert out["변경여부"] is True, out
    assert out["비교기준"]["A"]["시행본"]["MST"] == out["비교기준"]["B"]["시행본"]["MST"] == "100"
    assert out["현행_문안_시작"]["시행일자"] == "20251001", out["현행_문안_시작"]


def test_addenda_current_and_as_of_include_later_promulgated_addendum():
    with _late_promulgation_fake() as client:
        cur = client.law_addenda(law_name="가법")                         # 현행 검색 결과 → 통합본
        asof = client.law_addenda(law_name="가법", as_of_date="20251015")
        art = client.law_addenda(law_name="가법", article_no="5")
    for out in (cur, asof):
        assert [r["공포번호"] for r in out["부칙목록_최신순"]] == ["1100", "1000"], out
        assert "통합본" in out["부칙범위"] and "주의" not in out, out
    assert art["언급된_부칙_최신순"][0]["공포번호"] == "1100", art


def test_addenda_by_mst_uses_that_rows_consolidated_text_and_as_of():
    """mst만 주면 그 MST의 연혁 행(기준일 이하 마지막 시행일자)의 통합본 — 예전에는 MST 공포 당시 원문이라
    늦게 공포·먼저 시행된 제1100호 부칙이 빠지고 as_of_date도 무시했다 (2026-10-09 실측: mst=280405)."""
    with _late_promulgation_fake() as client:
        by_mst = client.law_addenda(mst="100")                               # 오늘(20251015) → 100@20251001
        old = client.law_addenda(mst="100", as_of_date="20250315")           # → 100@20250101
        pr = client.law_addenda(mst="100", as_of_date="20250315", promul_no="1100")
    assert [r["공포번호"] for r in by_mst["부칙목록_최신순"]] == ["1100", "1000"], by_mst
    assert by_mst["시행본_시행일자"] == "20251001" and "통합본" in by_mst["부칙범위"], by_mst
    assert [r["공포번호"] for r in old["부칙목록_최신순"]] == ["1000"] and old["시행본_시행일자"] == "20250101", old
    # '아직 시행 전'은 기준일로 판정 — 기준일 20250315 현재 제1100호(20250701 시행)는 시행 전
    assert pr["부칙"]["공포번호"] == "1100" and "20250315 현재 아직 시행 전" in pr["주의"], pr.get("주의")
    assert "부칙범위" in pr, pr


def test_addenda_promul_no_not_yet_effective_comes_from_its_own_promulgation():
    """통합본에는 그날까지 시행된 개정의 부칙만 있다 — 공포됐지만 시행 전인 제1200호는 그 공포본 원문에서."""
    fake = _late_promulgation_fake()
    with fake as client:
        out = client.law_addenda(law_name="가법", as_of_date="20251015", promul_no="1200")
    assert out["부칙"]["공포번호"] == "1200" and "2027년 1월 1일" in out["부칙"]["본문"], out
    assert "아직 시행 전" in out["주의"] and "20270101" in out["주의"], out["주의"]
    assert ("lawService.do", "law", "300") in fake.calls


def test_addenda_lists_and_searches_pending_promulgations():
    """기준일 현재 공포됐지만 아직 시행 전인 개정(제1200호)은 통합본 부칙에 없다 — 목록 모드는 '시행전_개정',
    article_no 모드는 그 공포본 원문의 부칙에서 '시행전_개정_부칙_언급'으로 따로 알린다 (2026-10-09 실측:
    소득세법 article_no='21'에서 2027.1.1 시행 예정 제19563호 부칙 ⑥이 빠짐)."""
    fake = _late_promulgation_fake()
    with fake as client:
        lst = client.law_addenda(law_name="가법")
        art = client.law_addenda(law_name="가법", article_no="5")
    assert [p["공포번호"] for p in lst.get("시행전_개정", [])] == ["1200"], lst
    assert lst["시행전_개정"][0]["연혁상_시행일자"] == ["20270101"], lst["시행전_개정"]
    assert [p["공포번호"] for p in art.get("시행전_개정_부칙_언급", [])] == ["1200"], art
    assert "제5조의 개정규정에도" in art["시행전_개정_부칙_언급"][0]["해당항"][0], art
    assert "아직 시행 전" in art["주의"], art.get("주의")
    assert ("lawService.do", "law", "300") in fake.calls   # 기준일 이전 마지막 공포본 원문


def test_fallback_when_consolidated_text_missing():
    """통합본이 '일치하는 법령이 없습니다'면 공포 당시 원문으로 폴백하고 '주의'로 알린다."""
    with _late_promulgation_fake(drop=[("eflaw", "100", "20251001")]) as client:
        art = client.law_article_as_of("가법", "20251015", "5")
        add = client.law_addenda(law_name="가법")
    assert "100분의 20" in art["원문"], "폴백하면 공포 당시 원문"
    assert "통합본" in art["주의"] and "일치하는 법령 없음" in art["주의"], art.get("주의")
    assert "통합본" in add["주의"] and "공포 당시 원문" in add["부칙범위"], add


def test_fallback_without_the_article_is_upstream_not_not_found():
    """폴백한 공포 당시 원문에 조문이 없다고 시행본에 없는 것은 아니다(늦게 공포된 개정이 신설한 조문) —
    NOT_FOUND(부존재 단정 가능)가 아니라 원천 이상(LawGoKrUpstreamError → UPSTREAM_ERROR)."""
    art9 = ("9", "신설조", "이 조는 제1100호로 신설되었다. <신설 2025.6.1>", "")
    x9 = _law_xml("가법", "900001", "20241231", "1000", "20251001",
                  [("5", "세율", RATE_15, ""), ("7", "신고기한", DUE_3M, ""), art9], [ADD_1000, ADD_1100])
    with _late_promulgation_fake(docs={("eflaw", "100", "20251001"): x9}) as client:
        assert client.law_article_as_of("가법", "20251015", "9")["조문"] == "제9조"
    with _late_promulgation_fake(drop=[("eflaw", "100", "20251001")]) as client:
        e = _raises(law_go_kr.LawGoKrUpstreamError, client.law_article_as_of, "가법", "20251015", "9")
        assert "단정할 수 없음" in str(e), e
        e2 = _raises(law_go_kr.LawGoKrUpstreamError, client.law_article_diff, "가법", "9", "20250315", "20251015")
        assert "통합본 조회에 실패" in str(e2), e2
    assert not issubclass(law_go_kr.LawGoKrUpstreamError, law_go_kr.LawGoKrError), "INVALID_INPUT으로 분류되면 안 됨"


def test_consolidated_retry_with_same_date_mst_before_fallback():
    """통합본 조회가 실패하면 같은 시행일자의 다른 MST(통합본 문안이 같다)로 먼저 다시 시도한다."""
    hist = [_row("100", "20250101", "20241231", "1000"), _row("110", "20250101", "20241231", "1001", kind="타법개정")]
    x = _law_xml("가법", "900001", "20241231", "1001", "20250101", [("5", "세율", RATE_20, "")], [ADD_1000])
    fake = FakeLawGoKr(hist, hist[-1:], {("eflaw", "100", "20250101"): x})   # 110은 '일치하는 법령 없음'
    with fake as client:
        art = client.law_article_as_of("가법", "20250601", "5")
    assert art["적용시행본"]["MST"] == "110" and "주의" not in art, art
    assert ("lawService.do", "eflaw", "100", "20250101") in fake.calls, fake.calls


def test_fallback_on_http_500_but_not_on_connection_error():
    resp = requests.Response()
    resp.status_code = 500
    http500 = requests.exceptions.HTTPError("500 Server Error", response=resp)
    with _late_promulgation_fake(errors={("eflaw", "100", "20251001"): http500}) as client:
        art = client.law_article_as_of("가법", "20251015", "5")
    assert "HTTP 500" in art["주의"], art.get("주의")
    down = requests.exceptions.ConnectionError("연결 실패")
    with _late_promulgation_fake(errors={("eflaw", "100", "20251001"): down}) as client:
        try:
            client.law_article_as_of("가법", "20251015", "5")
            raise AssertionError("접속 실패를 폴백으로 덮으면 안 된다 (UPSTREAM_ERROR로 나가야 함)")
        except requests.exceptions.ConnectionError:
            pass


def test_partially_staged_article_is_flagged():
    """통합본은 조문 일부만 늦게 시행되면 그 문안을 미리 싣고 '[시행일: …]'로 표시한다 —
    기준일 뒤의 표시만 '시행예정_부분'으로 알린다."""
    ref = "[시행일: 2025.7.1] 제5조제3항\n[시행일: 2027.1.1] 제5조제2항"
    with _late_promulgation_fake(ref=ref) as client:
        art = client.law_article_as_of("가법", "20251015", "5")
    assert art["시행예정_부분"] == [{"시행일자": "20270101", "대상": "제5조제2항"}], art.get("시행예정_부분")
    assert "제5조제2항: 20270101 시행" in art["주의"], art.get("주의")


def test_staged_marker_compared_with_as_of_not_version_date():
    """[시행일]이 시행본 시행일자와 기준일 사이면 기준일에는 이미 시행 중 — '아직 시행 전'으로 표시하지 않는다."""
    with _late_promulgation_fake(ref="[시행일: 2025.12.1] 제5조제2항", today="20260101") as client:
        art = client.law_article_as_of("가법", "20260101", "5")
    assert "시행예정_부분" not in art and "주의" not in art, art


def test_conditional_staged_marker_by_workplace_size():
    """'[시행일] … 다음 각 호의 구분에 따른 날' (사업장 규모별) 형식도 시행예정_부분으로 알린다
    (2026-10-09 실측: 근로기준법 제55조 20200526 통합본, 산업안전보건법 제175조 20270101 통합본)."""
    ref = ("[시행일] 제5조제2항의 개정규정은 다음 각 호의 구분에 따른 날부터 시행한다.\n"
           "  1. 상시 300명 이상의 근로자를 사용하는 사업 또는 사업장: 2025년 1월 1일\n"
           "  2. 상시 30명 이상 300명 미만의 근로자를 사용하는 사업 또는 사업장: 2026년 1월 1일\n"
           "  3. 상시 5인 이상 30명 미만의 근로자를 사용하는 사업 또는 사업장: 2027년 1월 1일")
    with _late_promulgation_fake(ref=ref) as client:
        art = client.law_article_as_of("가법", "20251015", "5")
    got = art.get("시행예정_부분", [])
    assert [(s["시행일자"], s["대상"]) for s in got] == [("20260101", "제5조제2항"), ("20270101", "제5조제2항")], got
    assert got[0]["조건"].startswith("상시 30명 이상 300명 미만"), got
    assert "사업장 규모" in art["주의"], art.get("주의")


# ---------------------------------------------------------------------------
# 과거 통합본 시대착오 (함정 6) — 다법 (법령ID 900003)
#   제1800호(2022.12.31 공포) 20230101 시행: 제11조③ 신설 — 단 ③1호는 2026.1.1 시행 예정('2026년 …')
#   제1900호(2023.12.31 공포) 20240101 시행: 제11조 개정, 제18조(2026.1.1 시행 예정) 신설
#   제2100호(2025.12.23 공포) 20260101 시행: '법률 제1800호 … 제11조제3항제1호 중 "2026년"을 "2027년"으로'
#     → 법제처 과거 통합본(20240101·20250101)에도 '2027년' 문안이 소급돼 실린다. 그 시점 공포본은 '2026년'.
#   제12조: 통합본은 '삭제 <2010.12.27>', 공포본(제1900호)은 제1900호가 다시 만든 조문(시행 전)
# ---------------------------------------------------------------------------
P11_OLD = "③ 다음 각 호의 경우 가산세를 부과하지 아니한다. <신설 2022.12.31, 2023.12.31>\n1. 2026년 1월 1일부터 2026년 12월 31일까지 지급하는 경우"
P11_NEW = "③ 다음 각 호의 경우 가산세를 부과하지 아니한다. <신설 2022.12.31, 2023.12.31, 2025.12.23>\n1. 2027년 1월 1일부터 2027년 12월 31일까지 지급하는 경우"
REF11 = "[본조신설 2019.12.31]\n[시행일: 2027.1.1] 제11조제3항제1호"
AMEND_2100 = ("⊙법률 제2100호 다법 일부개정법률 다법 일부를 다음과 같이 개정한다. 제3조 중 \"5일\"을 \"7일\"로 한다. "
              "법률 제1800호 다법 일부개정법률 제11조제3항제1호 중 \"2026년 1월 1일부터 2026년 12월 31일\"을 "
              "\"2027년 1월 1일부터 2027년 12월 31일\"로 한다. 부칙제1조(시행일) 이 법은 2026년 1월 1일부터 시행한다.")
AMEND_2101 = ("⊙법률 제2101호(2025.12.23) 라법 일부개정법률 [본문 생략] 부칙제1조(시행일) 이 법은 2026년 1월 1일부터 "
              "시행한다. 제2조부터 제10조까지 생략 제11조(다른 법률의 개정) ① 다법 일부를 다음과 같이 개정한다. "
              "제3조제2항 중 \"「라법」 제11조\"를 \"「라법」 제12조\"로 한다. ② 및 ③ 생략")


def _anachronism_fake(today="20261009"):
    h = lambda mst, eff, pub, no, kind="일부개정": _row(mst, eff, pub, no, kind=kind, name="다법", law_id="900003")  # noqa: E731
    hist = [h("400", "20230101", "20221231", "1800"), h("500", "20240101", "20231231", "1900"),
            h("600", "20250101", "20241231", "2000"), h("700", "20260101", "20251223", "2100"),
            h("710", "20260101", "20251223", "2101", kind="타법개정")]
    adds = [("20221231", "1800", "부칙 <제1800호,2022.12.31>\n제1조(시행일) 이 법은 2023년 1월 1일부터 시행한다.")]
    a11_v0 = ("11", "가산세", "① 가산세를 부과한다. <개정 2022.12.31>", "[본조신설 2019.12.31]")
    a11_old = ("11", "가산세", "① 가산세를 부과한다. <개정 2022.12.31, 2023.12.31>\n" + P11_OLD, "[본조신설 2019.12.31]")
    a11_new = ("11", "가산세", "① 가산세를 부과한다. <개정 2022.12.31, 2023.12.31>\n" + P11_NEW, REF11)
    a12_del = ("12", "", "삭제 <2010.12.27>", "")
    a12_prom = ("12", "정의", "이 장에서 쓰는 말의 뜻은 다음과 같다. <신설 2023.12.31>", "")
    a18 = ("18", "기본공제", "기본공제는 5천만원으로 한다. <신설 2023.12.31>", "")
    a3 = ("3", "기한", "기한은 5일로 한다.", "")
    x = lambda mst, pub, no, eff, arts, amend="": _law_xml("다법", "900003", pub, no, eff, arts, adds, amend)  # noqa: E731
    docs = {
        ("eflaw", "400", "20230101"): x("400", "20221231", "1800", "20230101", [a3, a11_v0, a12_del]),
        # 과거 통합본 — 2025.12.23 개정 문안이 소급돼 들어 있다 (시대착오), 제18조는 시행 전이라 없음
        ("eflaw", "500", "20240101"): x("500", "20231231", "1900", "20240101", [a3, a11_new, a12_del]),
        ("eflaw", "600", "20250101"): x("600", "20241231", "2000", "20250101", [a3, a11_new, a12_del]),
        ("eflaw", "700", "20260101"): x("700", "20251223", "2100", "20260101", [a3, a11_new, a12_del, a18], AMEND_2100),
        ("eflaw", "710", "20260101"): x("710", "20251223", "2101", "20260101", [a3, a11_new, a12_del, a18], AMEND_2101),
        # 공포 당시 원문 — 그때 공포돼 있던 문안 (시행 전 규정 포함)
        ("law", "500"): x("500", "20231231", "1900", "20240101", [a3, a11_old, a12_prom, a18]),
        ("law", "600"): x("600", "20241231", "2000", "20250101", [a3, a11_old, a12_prom, a18]),
    }
    return FakeLawGoKr(hist, hist[-1:], docs, today=today)


def test_past_consolidated_anachronism_gets_promulgated_reference():
    """과거 기준일: 통합본 문안의 개정 주석(2025.12.23)이 기준일보다 늦으면 '주의' + 기준일 이전 마지막 공포본의
    같은 조문을 '공포본_참고'로 (2026-10-09 실측: 소득세법 제81조의11 @20240101·20250101, 법인세법 제75조의7)."""
    fake = _anachronism_fake()
    with fake as client:
        art = client.law_article_as_of("다법", "20240301", "11")
    assert "2027년 1월 1일부터" in art["원문"], "시험 전제(과거 통합본에 뒤 개정 문안)가 깨짐"
    ref = art.get("공포본_참고") or {}
    assert ref.get("MST") == "500" and "2026년 1월 1일부터" in ref.get("원문", ""), ref
    assert "20251223" in art["주의"] and "공포본_참고" in art["주의"], art.get("주의")
    assert "과거 기준일 조회" in art["주의"], "과거 조회의 [시행일] 표시 주의가 없음"
    assert ("lawService.do", "law", "500") in fake.calls


def test_past_article_missing_from_consolidated_but_promulgated_is_not_not_found():
    """과거 통합본에 없지만 기준일 당시 공포본에 있는 조문(시행 전 신설) — NOT_FOUND가 아니라 '공포본_참고'로
    (2026-10-09 실측: 소득세법 제87조의18 @20240101). 둘 다 없으면 NOT_FOUND."""
    with _anachronism_fake() as client:
        art = client.law_article_as_of("다법", "20240301", "18")
        dele = client.law_article_as_of("다법", "20240301", "12")
        e = _raises(law_go_kr.LawGoKrNotFound, client.law_article_as_of, "다법", "20240301", "19")
    assert art["원문"] == "" and "통합본_문안_없음" in art, art
    assert "5천만원" in art["공포본_참고"]["원문"] and "시행 전" in art["주의"], art
    # 통합본 '삭제'인데 공포본에는 다른 문안 — 공포본_참고
    assert "삭제" in dele["원문"] and "〈2010.12.27〉" in dele["원문"], dele["원문"]
    assert "정의" in dele["공포본_참고"]["원문"] and "삭제" in dele["주의"], dele
    assert "제19조" in str(e)


def test_current_query_does_not_fetch_promulgated_text():
    """현행 조회(기준일 = 오늘)는 시대착오 점검을 하지 않는다 — 공포본 추가 왕복 없음."""
    fake = _anachronism_fake(today="20260301")
    with fake as client:
        art = client.law_article_as_of("다법", "20260301", "11")
    assert "공포본_참고" not in art, art
    assert not any(c[1] == "law" for c in fake.calls if c[0] == "lawService.do"), fake.calls


def test_diff_flags_attribution_when_b_text_has_later_notes():
    """B 문안에 '현행_문안_시작' 시행일자보다 늦은 개정 주석이 있으면 특정을 '불확실'로 바꾸고 그 날짜 공포본을
    후보로 (2026-10-09 실측: 소득세법 제81조의11 20230101→20261009 → 20240101 제19933호로 확신 특정됨,
    실제 '2027년' 문구는 2025.12.23 제21221호). B의 [시행일] 미시행 부분도 표시한다."""
    with _anachronism_fake() as client:
        out = client.law_article_diff("다법", "11", "20230601", "20261009")
    start = out["현행_문안_시작"]
    assert start["시행일자"] == "20240101", start
    assert start["특정근거"].startswith("불확실"), start["특정근거"]
    cands = start.get("뒤_개정_후보", [])
    assert cands and cands[0]["공포번호"] == "2100" and cands[0]["개정문_언급"] is True, cands
    assert [c["공포번호"] for c in cands if c["개정문_언급"]] == ["2100"], "타법개정 개정문의 다른 법 조번호에 오매치"
    assert out["비교기준"]["B"]["시행예정_부분"] == [{"시행일자": "20270101", "대상": "제11조제3항제1호"}], out
    assert "불확실" in out["주의"] and "B 시점" in out["주의"], out.get("주의")


def test_diff_ignores_table_format_only_changes():
    """표 괘선 폭·전각 기호만 다른 시행본은 '변경 없음(서식 차이)' (2026-10-09 실측: 소득세법 제35조)."""
    t1 = "세액은 다음 표와 같다.\n┌──────┬───┐\n│1억원 ＋ [(수입금액 - 500억원) × 3/10,000]│\n└──────┴───┘"
    t2 = "세액은 다음 표와 같다.\n┌────┬─────┐\n│1억원 ＋ [(수입금액 － 500억원)  × 3/10,000]│\n└────┴─────┘"
    hist = [_row("100", "20250101", "20241231", "1000"), _row("200", "20250701", "20250601", "1100")]
    x = lambda mst, pub, no, eff, t: _law_xml("가법", "900001", pub, no, eff, [("35", "세액", t, "")], [ADD_1000])  # noqa: E731
    docs = {("eflaw", "100", "20250101"): x("100", "20241231", "1000", "20250101", t1),
            ("eflaw", "200", "20250701"): x("200", "20250601", "1100", "20250701", t2)}
    with FakeLawGoKr(hist, hist[-1:], docs) as client:
        out = client.law_article_diff("가법", "35", "20250301", "20250801")
    assert out["변경여부"] is False and out.get("서식차이") is True, out


def test_amend_scope_ignores_other_laws_addendum_numbers():
    """타법개정 개정문 속 다른 법의 부칙 조번호('제2조부터 제10조까지 생략', '제11조(다른 법률의 개정)')와
    「다른 법」 인용 조문은 이 법 조문 언급으로 보지 않는다 (2026-10-09 실측: 소득세법 MST 280411)."""
    C = law_go_kr.LawGoKrClient
    row_other = _row("710", "20260101", "20251223", "2101", kind="타법개정", name="다법")
    row_own = _row("700", "20260101", "20251223", "2100", name="다법")
    for label in ("제11조", "제2조", "제10조", "제12조"):
        assert not C._amend_mentions(AMEND_2101, row_other, label), label
    assert C._amend_mentions(AMEND_2101, row_other, "제3조")
    assert C._amend_mentions(AMEND_2100, row_own, "제11조") and C._amend_mentions(AMEND_2100, row_own, "제3조")
    assert not C._amend_mentions(AMEND_2100, row_own, "제12조")
    rng = "다법 일부를 다음과 같이 개정한다. 제87조의2부터 제87조의27까지를 각각 삭제한다. 부칙 이 법은 공포한 날부터 시행한다."
    assert C._amend_mentions(rng, row_own, "제87조의18") and not C._amend_mentions(rng, row_own, "제87조의28")


def test_article_pattern_allows_possessive_but_not_branch():
    """'제30조의 개정규정'은 제30조 언급이고 '제30조의2'·'제300조'는 아니다. '제59조의2'는 '제59조의21'이 아니다
    (2026-10-09 실측: 노인장기요양보험법 부칙 '제30조의 개정규정은 공포한 날부터 시행한다'를 (?!의) 가드가 놓침)."""
    p = law_go_kr._article_pat("제30조")
    assert p.search("다만, 제30조의 개정규정은 공포한 날부터 시행한다.")
    assert not p.search("제30조의2제1항") and not p.search("제300조")
    p2 = law_go_kr._article_pat("제59조의2")
    assert p2.search("제59조의2제1항의 개정규정") and not p2.search("제59조의21")


def test_same_name_multiple_ids_keep_only_that_law():
    """이름이 같은 법령ID가 둘이면(폐지 후 재제정) 그 둘로 거르고 시행령·시행규칙은 뺀다 (2026-10-09 실측:
    '노동조합 및 노동관계조정법' 000142·000143 → 시행규칙 제2조가 나옴). 같은 날 옛 법 폐지 행은 새 법 앞에 둔다."""
    rows = [_row("1", "19970301", "19961231", "5244", kind="제정", name="노동조합및노동관계조정법", law_id="000142"),
            _row("2", "19970313", "19970313", "5310", kind="폐지", name="노동조합및노동관계조정법", law_id="000142"),
            _row("3", "19970313", "19970313", "5310", kind="제정", name="노동조합및노동관계조정법", law_id="000143"),
            _row("4", "20260310", "20260310", "1", kind="일부개정", name="노동조합 및 노동관계조정법 시행규칙",
                 law_id="006935"),
            _row("5", "20251201", "20251201", "21000", name="노동조합 및 노동관계조정법", law_id="000143")]
    kept = law_go_kr._same_name_only(rows, "노동조합 및 노동관계조정법")
    assert {r["법령ID"] for r in kept} == {"000142", "000143"}, kept
    with FakeLawGoKr(rows, rows[-1:], {}) as client:
        hist = client.law_history("노동조합 및 노동관계조정법")
    assert [r["MST"] for r in hist] == ["1", "2", "3", "5"], [r["MST"] for r in hist]


# ---------------------------------------------------------------------------
# 같은 시행일자에 여러 공포본 — 통합본 문안이 같아 이진탐색만으로는 고친 공포본을 못 가린다
#   나법: 제500호(2024.12.31) 20250101 시행 + 20260101 단계 시행,
#         2025.12.23 같은 날 제600호(일부개정, 제9조 개정)·제601호(타법개정) — 모두 20260101 시행
#   제601호의 개정문은 라법 부칙 속 '제9조(다른 법률의 개정)'라 조번호만 보면 제9조로 오인된다
# ---------------------------------------------------------------------------

def _same_date_fake(pub_601="20251223"):
    hist = [_row("10", "20250101", "20241231", "500", name="나법", law_id="900002"),
            _row("10", "20260101", "20241231", "500", name="나법", law_id="900002"),
            _row("30", "20260101", "20251223", "600", name="나법", law_id="900002"),
            _row("40", "20260101", pub_601, "601", kind="타법개정", name="나법", law_id="900002")]
    a = [("9", "기한", "기한은 1년으로 한다. <개정 2024.12.31>", "")]
    b = [("9", "기한", "기한은 2년으로 한다. <개정 2024.12.31, 2025.12.23>", "")]
    adds_a = [("20241231", "500", "부칙 <제500호,2024.12.31>\n제1조(시행일) 이 법은 2025년 1월 1일부터 시행한다.")]
    adds_b = adds_a + [
        ("20251223", "600", "부칙 <제600호,2025.12.23>\n제1조(시행일) 이 법은 2026년 1월 1일부터 시행한다.\n"
                            "제2조(기한에 관한 적용례) 제9조제1항의 개정규정은 이 법 시행 이후 개시하는 분부터 적용한다."),
        (pub_601, "601", "부칙 <제601호>\n제1조(시행일) 이 법은 2026년 1월 1일부터 시행한다.")]
    x = lambda mst, pub, no, eff, arts, adds, amend: _law_xml(  # noqa: E731
        "나법", "900002", pub, no, eff, arts, adds, amend)
    amend_600 = "⊙법률 제600호 나법 일부개정법률 나법 일부를 다음과 같이 개정한다. 제9조 중 \"1년\"을 \"2년\"으로 한다. 부칙제1조(시행일) …"
    amend_601 = ("⊙법률 제601호 라법 일부개정법률 [본문 생략] 부칙제1조(시행일) 이 법은 2026년 1월 1일부터 시행한다. "
                 "제2조부터 제8조까지 생략 제9조(다른 법률의 개정) ① 나법 일부를 다음과 같이 개정한다. "
                 "제3조제1항 중 \"「라법」 제9조\"를 \"「라법」 제10조\"로 한다. ② 생략")
    docs = {
        ("eflaw", "10", "20250101"): x("10", "20241231", "500", "20250101", a, adds_a, "제3조제1항 중 …"),
        ("eflaw", "10", "20260101"): x("10", "20241231", "500", "20260101", b, adds_b, "제3조제1항 중 …"),
        ("eflaw", "30", "20260101"): x("30", "20251223", "600", "20260101", b, adds_b, amend_600),
        ("eflaw", "40", "20260101"): x("40", pub_601, "601", "20260101", b, adds_b, amend_601),
    }
    return FakeLawGoKr(hist, hist[-1:], docs, today="20260601")


def test_same_date_group_attributed_by_amendment_text():
    """같은 날 공포된 제600호·제601호 — 주석 날짜로는 못 가리고, 이 법을 고치는 개정문 부분이 제9조를 언급하는
    제600호 (제601호 개정문의 '제9조(다른 법률의 개정)'는 라법 부칙 조번호라 언급이 아님)."""
    with _same_date_fake() as client:
        out = client.law_article_diff("나법", "9", "20250601", "20260601")
    start = out["현행_문안_시작"]
    assert (start["MST"], start["공포번호"]) == ("30", "600"), start
    assert "개정문" in start["특정근거"], start["특정근거"]
    assert out["개정_부칙"]["공포번호"] == "600" and out["개정_부칙"].get("이_조문_언급_항"), out["개정_부칙"]


def test_same_date_group_attributed_by_note_date():
    """새로 붙은 개정 주석 날짜(2025.12.23)와 공포일자가 하나만 맞으면 그것 — 개정문 대조 없이."""
    fake = _same_date_fake(pub_601="20251130")
    with fake as client:
        out = client.law_article_diff("나법", "9", "20250601", "20260601")
    start = out["현행_문안_시작"]
    assert (start["MST"], start["공포번호"]) == ("30", "600"), start
    assert "주석 날짜" in start["특정근거"], start["특정근거"]
    # 개정문 대조 없이 정해져야 한다 — 이진탐색의 상태 조회 외에 같은 시행본을 다시 받지 않음
    assert fake.calls.count(("lawService.do", "eflaw", "40", "20260101")) <= 1, fake.calls


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
