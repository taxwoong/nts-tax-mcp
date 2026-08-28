# -*- coding: utf-8 -*-
"""
test_parsers.py — 파서 회귀 테스트 (오프라인, 네트워크 불필요)

tests/fixtures/*.gz (refresh_fixtures.py가 캡처한 실제 응답)를 파싱해
스크래핑·XML 파서가 깨지지 않았는지 검증한다. 원천 사이트가 개편되면
refresh_fixtures.py로 픽스처를 갱신한 뒤 이 테스트로 파서 수정을 검증한다.

실행:
    python tests/test_parsers.py          # 단독 실행 (pytest 불필요)
    python -m pytest tests/test_parsers.py -q   # pytest가 있으면 그대로 호환
"""
import gzip
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def read_gz(name: str) -> str:
    with gzip.open(FIXTURES / name, "rt", encoding="utf-8") as f:
        return f.read()


def manifest() -> dict:
    with open(FIXTURES / "manifest.json", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# NTS (taxlaw.nts.go.kr) — JSON 파싱
# ---------------------------------------------------------------------------

def test_nts_parse_search():
    from nts_tax_ruling_search import NtsTaxLawClient
    client = NtsTaxLawClient()
    raw = json.loads(read_gz("nts_search.json.gz"))
    result = client._parse(raw)
    assert "question" in result and "precedent" in result, f"컬렉션 누락: {list(result)}"
    q = result["question"]
    assert q["total_count"] > 0, "질의 컬렉션 0건 — 캡처 검색어 확인"
    item = q["items"][0]
    for field in ("title", "doc_no", "date", "doc_type"):
        assert item.get(field), f"필수 필드 비어 있음: {field} / {item}"
    assert re.match(r"^\d{8}", item["date"]), f"날짜 형식 이상: {item['date']}"
    assert "<!HS>" not in (item["title"] or "") and "<!HS>" not in (item["doc_no"] or ""), \
        "하이라이트 마커가 제거되지 않음"


def test_nts_parse_empty_is_not_error():
    """0건 검색은 정상 구조로 파싱되어야 하고(NOT_FOUND), 예외가 나면 안 된다."""
    from nts_tax_ruling_search import NtsTaxLawClient
    client = NtsTaxLawClient()
    raw = json.loads(read_gz("nts_search_empty.json.gz"))
    result = client._parse(raw)
    total = sum(v.get("total_count", 0) for v in result.values() if isinstance(v, dict))
    assert total == 0, f"빈 검색 픽스처인데 결과가 있음: {total}"
    finalized = client._attach_guidance(result, "빈검색어")
    assert finalized["status"] == "NOT_FOUND"
    assert finalized.get("_guidance")


def test_nts_parse_broken_structure_raises():
    """구조가 바뀐 응답은 0건이 아니라 NtsParseError여야 한다 (부존재 오판 방지)."""
    from nts_tax_ruling_search import NtsTaxLawClient, NtsParseError
    client = NtsTaxLawClient()
    for garbage in ({}, {"data": {}}, {"data": {"ASEISA001MR01": {}}}, {"status": "500"}):
        try:
            client._parse(garbage)
            raise AssertionError(f"NtsParseError가 발생해야 함: {garbage}")
        except NtsParseError:
            pass


# ---------------------------------------------------------------------------
# OLTA (olta.re.kr) — HTML 파싱
# ---------------------------------------------------------------------------

def test_olta_parse_search():
    from olta_tax_ruling_search import OltaTaxLawClient
    result = OltaTaxLawClient._parse(read_gz("olta_search.html.gz"))
    assert "tax_tribunal" in result, f"조세심판원 카테고리 누락: {list(result)}"
    assert len(result) >= 3, f"카테고리 수 이상: {list(result)}"
    data = result["tax_tribunal"]
    assert data["total_count"] > 0
    item = data["items"][0]
    assert item.get("doc_no"), f"사건번호 누락: {item}"
    assert item.get("date") and re.match(r"^\d{8}$", item["date"]), f"날짜 정규화 실패: {item}"
    assert item.get("doc_id") and item["doc_id"].isdigit(), f"doc_id 추출 실패: {item}"


def test_olta_parse_empty_returns_nothing():
    """0건 페이지는 빈 dict — search()의 카나리 판별이 이 동작을 전제로 한다."""
    from olta_tax_ruling_search import OltaTaxLawClient
    result = OltaTaxLawClient._parse(read_gz("olta_search_empty.html.gz"))
    assert result == {}, f"빈 검색 픽스처에서 카테고리가 나옴: {list(result)}"


# ---------------------------------------------------------------------------
# law.go.kr — XML 파싱 (모듈의 _get을 픽스처 서버로 교체해 오프라인 실행)
# ---------------------------------------------------------------------------

def _fixture_get(endpoint, **params):
    target = params.get("target")
    if target == "eflaw":
        page = params.get("page", 1)
        path = FIXTURES / f"law_eflaw_p{page}.xml.gz"
        if path.exists():
            return read_gz(path.name)
        return "<법령검색></법령검색>"  # 캡처 범위 밖 페이지 = 빈 페이지 (루프 종료)
    if target == "law" and params.get("MST"):
        path = FIXTURES / f"law_{params['MST']}.xml.gz"
        if path.exists():
            return read_gz(path.name)
        raise AssertionError(f"픽스처에 없는 MST 요청: {params['MST']} — "
                             "오프라인 테스트는 find_change=False로 호출해야 함")
    raise AssertionError(f"픽스처에 없는 요청: {endpoint} {params}")


class _fixture_law_client:
    """law_go_kr._get을 픽스처 서버로 바꿔치기하는 컨텍스트."""
    def __enter__(self):
        import law_go_kr as L
        self.L = L
        self.orig = L._get
        L._get = _fixture_get
        return L.LawGoKrClient()

    def __exit__(self, *exc):
        self.L._get = self.orig
        return False


def test_law_history_sorted_and_covers_old_versions():
    m = manifest()["law"]
    with _fixture_law_client() as client:
        rows = client.law_history(m["law_name"], law_id=m["law_id"])
    assert len(rows) == m["history_rows"], f"연혁 행수 변동: {len(rows)} != {m['history_rows']}"
    dates = [r["시행일자"] for r in rows if r["시행일자"]]
    assert dates == sorted(dates), "연혁이 시행일자 오름차순이 아님"
    assert rows[0]["시행일자"] == m["history_first_date"], \
        f"과거 연혁 누락: 최초 {rows[0]['시행일자']} (기대 {m['history_first_date']})"
    assert all(r["MST"] for r in rows), "MST 없는 행 존재"


def test_law_article_slice():
    m = manifest()["law"]
    with _fixture_law_client() as client:
        art = client.law_article(m["mst_current"], "104", max_chars=200_000)
        # 절(節) 첫 조문 케이스: 표제 노드가 아니라 실제 조문이 잡혀야 한다 (v5.1 버그)
        assert art["조문"] == "제104조"
        assert "양도소득세의 세율" in art["조문제목"], f"조문제목 이상: {art['조문제목']}"
        assert "제104조(" in art["원문"]
        assert art.get("조문시행일자") and re.match(r"^\d{8}$", art["조문시행일자"])
        # 가지조문 케이스
        art3 = client.law_article(m["mst_current"], "104의3", max_chars=200_000)
        assert art3["조문"] == "제104조의3"
        assert "비사업용" in art3["조문제목"]
        # 없는 조문은 NotFound (v5.3 이후 계약)
        from law_go_kr import LawGoKrNotFound
        try:
            client.law_article(m["mst_current"], "999의9")
            raise AssertionError("없는 조문인데 예외가 발생하지 않음")
        except LawGoKrNotFound:
            pass


def test_law_last_article_not_flooded_by_addenda():
    """마지막 조문 조회 시 부칙 수십만 자가 딸려 나오던 버그(v5.3 수정) 회귀 방지."""
    m = manifest()["law"]
    xml = read_gz(f"law_{m['mst_current']}.xml.gz")
    assert "<부칙>" in xml, "픽스처에 부칙 섹션이 없음 — 캡처 확인"
    import law_go_kr as L
    positions = re.findall(r"<조문번호>(\d+)</조문번호>", xml[:xml.find("<부칙>")])
    last_no = positions[-1]
    label, block = L.LawGoKrClient._find_article_block(xml, last_no)
    # 마지막 조문 블록에서 가지번호를 붙여 정확 조회
    bm = re.search(r"<조문가지번호>(\d+)</조문가지번호>", block[:300]) if block else None
    article_no = f"{last_no}의{bm.group(1)}" if bm else last_no
    with _fixture_law_client() as client:
        art = client.law_article(m["mst_current"], article_no, max_chars=500_000)
    assert len(art["원문"]) < 50_000, \
        f"마지막 조문({article_no}) 본문이 비정상적으로 큼({len(art['원문'])}자) — 부칙 혼입 의심"


def test_law_addenda():
    m = manifest()["law"]
    with _fixture_law_client() as client:
        out = client.law_addenda(mst=m["mst_current"], recent=5)
        assert out["부칙총수"] > 50, f"부칙 수 이상: {out['부칙총수']}"
        rows = out["부칙목록_최신순"]
        assert len(rows) == 5
        assert all(not r["공포번호"].startswith("0") for r in rows if r["공포번호"]), \
            "공포번호 0 패딩이 제거되지 않음"
        assert rows[0]["공포일자"] >= rows[-1]["공포일자"], "부칙 목록이 최신순이 아님"
        # 특정 조문 발췌 모드: '제96조' 패턴이 '제96조의2'에 오매치되면 안 됨
        art_out = client.law_addenda(mst=m["mst_current"], article_no="96", max_chars=6000)
        for entry in art_out.get("언급된_부칙_최신순", []):
            for para in entry["해당항"]:
                cleaned = para.replace("제96조의", "")
                assert "제96조" in cleaned, f"제96조 자체가 없는 항이 발췌됨: {para[:100]}"


def test_law_article_diff_offline():
    m = manifest()["law"]
    with _fixture_law_client() as client:
        # date_to는 manifest에 고정된 캡처 시점을 쓴다 — 생략하면 '오늘'이 기준이 되어
        # 픽스처의 미래 시행본 때문에 실행 날짜에 따라 결과가 달라진다
        out = client.law_article_diff(m["law_name"], "104", m["old_date"], m["date_to"],
                                      law_id=m["law_id"], max_chars=50_000,
                                      find_change=False)  # 중간 시행본 픽스처가 없으므로
    assert out["변경여부"] is True, "2020→현행 제104조는 개정이 있었음 (실측)"
    assert out["변경유형"] == "개정"
    assert out["diff"].startswith("--- A"), f"diff 헤더 이상: {out['diff'][:80]}"
    assert "\n+" in out["diff"] and "\n-" in out["diff"], "diff에 변경 줄이 없음"
    assert out["비교기준"]["A"]["시행본"]["MST"] == m["mst_old"]
    assert out["비교기준"]["B"]["시행본"]["MST"] == m["mst_current"]


def test_law_history_picks_latest_promulgation_on_same_date():
    """같은 시행일자에 여러 공포본이 오면 최신 공포본이 선택되어야 한다.
    시행일자 단독 정렬 키로는 가장 오래된 공포본이 잡혀 옛 문안을 반환했다."""
    m = manifest()["law"]
    with _fixture_law_client() as client:
        rows = client.law_history(m["law_name"], law_id=m["law_id"])
    groups = {}
    for r in rows:
        groups.setdefault(r["시행일자"], []).append(r)
    dupes = {d: g for d, g in groups.items() if len(g) > 1}
    assert dupes, "픽스처에 동일 시행일자 중복 행이 없음 — 이 회귀를 검증할 수 없음"
    for date, g in dupes.items():
        promuls = [r["공포일자"] for r in g]
        assert promuls == sorted(promuls), f"{date} 그룹이 공포일자 오름차순이 아님: {promuls}"
        # 'date 이하 마지막 행'을 고르는 pick 계열이 그 그룹의 최신 공포본을 집는지
        picked = [r for r in rows if r["시행일자"] and r["시행일자"] <= date][-1]
        assert picked["공포일자"] == max(promuls), \
            f"{date} 기준 선택이 최신 공포본이 아님: {picked['공포일자']} (기대 {max(promuls)})"


def test_pick_effective_version_backtracks_on_staged_enforcement():
    """조문시행일자가 기준일보다 미래인 문안(단계 시행)은 이전 시행본으로 소급해야 한다."""
    from law_go_kr import LawGoKrClient
    history = [
        {"시행일자": "20250101", "MST": "OLD", "법령명": "x", "공포일자": "20241231",
         "공포번호": "1", "제개정구분": "일부개정"},
        {"시행일자": "20260101", "MST": "NEW", "법령명": "x", "공포일자": "20251223",
         "공포번호": "2", "제개정구분": "일부개정"},
    ]
    # NEW 시행본의 XML에는 조문시행일자 20270101(아직 발효 전)인 문안이 들어 있다
    states = {"OLD": (["구 문안"], "20250101"), "NEW": (["신 문안"], "20270101")}
    client = LawGoKrClient()
    row, lines, art = client._pick_effective_version(history, "20260301", "57의2",
                                                     state_fn=lambda mst: states[mst])
    assert row["MST"] == "OLD" and lines == ["구 문안"] and art == "20250101", \
        f"미래 문안으로 소급 실패: {row['MST']} / {lines}"
    # 조문시행일자가 기준일 이하이면 소급하지 않는다
    row2, _, _ = client._pick_effective_version(history, "20270601", "57의2",
                                                state_fn=lambda mst: states[mst])
    assert row2["MST"] == "NEW", f"불필요한 소급 발생: {row2['MST']}"
    # 조문이 아예 없는 시점(신설 전)은 소급하지 않고 그대로 반환
    row3, lines3, _ = client._pick_effective_version(
        history, "20260301", "57의2", state_fn=lambda mst: (None, ""))
    assert row3["MST"] == "NEW" and lines3 is None, "신설 전 케이스 처리 오류"


def test_law_date_format_validation():
    """잘못된 날짜 형식은 NOT_FOUND(부존재)가 아니라 INVALID_INPUT 계열이어야 한다."""
    from law_go_kr import LawGoKrClient, LawGoKrError, LawGoKrNotFound
    m = manifest()["law"]
    with _fixture_law_client() as client:
        for bad in ("2025-03-15", "", "20250101x", "2025"):
            for call in (
                lambda d: client.law_article_as_of(m["law_name"], d, "104", law_id=m["law_id"]),
                lambda d: client.law_article_diff(m["law_name"], "104", d, law_id=m["law_id"]),
            ):
                try:
                    call(bad)
                    raise AssertionError(f"형식 오류가 통과됨: {bad!r}")
                except LawGoKrNotFound as e:
                    raise AssertionError(f"{bad!r}이 NOT_FOUND로 분류됨(부존재 오판): {e}")
                except LawGoKrError:
                    pass  # 기대 동작


def test_article_content_lines_excludes_metadata():
    """diff 비교 대상에서 메타데이터(조번호·시행일자 숫자·번호 태그 중복)가 빠져야
    문안이 같은 시행본이 '변경'으로 오판되지 않는다."""
    m = manifest()["law"]
    import law_go_kr as L
    xml = read_gz(f"law_{m['mst_current']}.xml.gz")
    _, block = L.LawGoKrClient._find_article_block(xml, "104의3")
    lines = L.LawGoKrClient._article_content_lines(block)
    assert lines, "문안 추출 실패"
    assert lines[0].startswith("제104조의3("), f"첫 줄 이상: {lines[0]}"
    for ln in lines:
        assert not re.match(r"^\d+$", ln), f"숫자 메타데이터 줄이 섞임: {ln!r}"
        assert ln not in ("조문", "Y", "N"), f"메타데이터 줄이 섞임: {ln!r}"
        assert not re.match(r"^[①-⑮]$", ln), f"항번호 중복 줄이 섞임: {ln!r}"
        assert not re.match(r"^\d+\.$", ln), f"호번호 중복 줄이 섞임: {ln!r}"


# ---------------------------------------------------------------------------
# 순수 함수 · 오류 계약 (픽스처 불필요)
# ---------------------------------------------------------------------------

def test_normalize_doc_no():
    from olta_tax_ruling_search import normalize_doc_no
    assert normalize_doc_no("조심-2023-서-9465") == "조심2023서9465"
    assert normalize_doc_no("조심 2023 서 9465") == "조심2023서9465"
    assert normalize_doc_no("") == ""


def test_match_citation_partial_rule():
    import server
    cands = [("기획재정부 재산세제과-73", {"문서번호": "기획재정부 재산세제과-73"})]
    # 정확 일치
    meta, how = server._match_citation("기획재정부재산세제과73", cands)
    assert meta and how == "정확"
    # 축약 표기 (기관 접두 생략)
    meta, how = server._match_citation("재산세제과73", cands)
    assert meta and how.startswith("부분")
    # 너무 짧은 입력은 부분 일치 금지 (오매치 방지)
    meta, _ = server._match_citation("과73", cands)
    assert meta is None


def test_match_citation_rejects_year_only_prefix():
    """'조심2023'처럼 연도만 있는 입력이 그 해 아무 사건에나 '확인'되면 안 된다."""
    import server
    cands = [("조심-2023-서-9465", {"문서번호": "조심-2023-서-9465"}),
             ("서면-2019-법규재산-4276", {"문서번호": "서면-2019-법규재산-4276"})]
    for bogus in ("조심2023", "서면2019", "조심-2023"):
        meta, _ = server._match_citation(server_normalize(bogus), cands)
        assert meta is None, f"연도만 있는 입력이 매치됨: {bogus} → {meta}"
    # 일련번호까지 있으면 정상 매치
    meta, how = server._match_citation(server_normalize("조심-2023-서-9465"), cands)
    assert meta and how == "정확"


def test_match_citation_respects_digit_boundary():
    """'재산세제과-73'이 '…재산세제과-732'에 걸리면 안 된다 (접미 일치 규칙)."""
    import server
    cands = [("기획재정부 재산세제과-732", {"문서번호": "기획재정부 재산세제과-732"}),
             ("기획재정부 재산세제과-73", {"문서번호": "기획재정부 재산세제과-73"})]
    meta, how = server._match_citation(server_normalize("재산세제과-73"), cands)
    assert meta is not None, "정당한 축약 표기가 매치되지 않음"
    assert meta["문서번호"].endswith("-73"), f"접두 숫자 오매치: {meta['문서번호']}"
    assert how.startswith("부분")
    # 후보에 -732만 있으면 매치되지 않아야 한다
    meta2, _ = server._match_citation(server_normalize("재산세제과-73"), cands[:1])
    assert meta2 is None, f"숫자 경계 무시하고 매치됨: {meta2}"
    # 일련번호 부분만으로도 접미가 맞으면 매치 (앞쪽 생략은 실무 축약 형태)
    meta3, _ = server._match_citation(server_normalize("2023서9465"),
                                      [("조심-2023-서-9465", {"문서번호": "조심-2023-서-9465"})])
    assert meta3 is not None, "접미 축약 표기가 매치되지 않음"


def server_normalize(s):
    from olta_tax_ruling_search import normalize_doc_no
    return normalize_doc_no(s)


def test_local_tax_doc_pattern():
    """지방세 표기 판별 — 2자리 연도 헌재·'감심 제YYYY-N호' 표기를 포함해야 한다."""
    import server
    from olta_tax_ruling_search import normalize_doc_no
    for s in ("조심2026지0284", "감심2022-433", "감심 제2020-123호", "2017헌바363", "96헌바14"):
        assert server._LOCAL_TAX_DOC_RE.match(normalize_doc_no(s)), f"지방세 표기 미인식: {s}"
    for s in ("조심-2023-서-9465", "서면-2019-법규재산-4276", "기획재정부 재산세제과-73"):
        assert not server._LOCAL_TAX_DOC_RE.match(normalize_doc_no(s)), f"국세 표기 오인식: {s}"


def test_verify_citations_input_validation():
    """빈/비문자열 입력이 '정상 완료(0건 검증)'로 보이면 안 된다."""
    import server
    for bad in ([], "조심-2023-서-9465", None, ["", "   "], [None]):
        out = server.verify_citations(bad)
        assert out["status"] == "INVALID_INPUT", f"{bad!r} → {out.get('status')}"


def test_invalid_collection_names_are_rejected():
    """무효 collections/categories를 조용히 버리면 전체검색으로 둔갑한다."""
    import server
    out = server.nts_ruling_search("취득세", collections=["없는컬렉션"])
    assert out["status"] == "INVALID_INPUT", f"nts: {out.get('status')}"
    out2 = server.olta_ruling_search("취득세", categories=["없는카테고리"])
    assert out2["status"] == "INVALID_INPUT", f"olta: {out2.get('status')}"


def test_error_contract_tool_guard():
    import requests
    import server
    from nts_tax_ruling_search import NtsParseError

    def boom_parse():
        raise NtsParseError("구조 변경")

    def boom_net():
        raise requests.exceptions.ConnectionError("연결 실패")

    def boom_input():
        raise ValueError("sort는 …")

    assert server._tool_guard(boom_parse)["status"] == "PARSE_ERROR"
    assert server._tool_guard(boom_net)["status"] == "UPSTREAM_ERROR"
    assert server._tool_guard(boom_input)["status"] == "INVALID_INPUT"
    assert server._tool_guard(lambda: {"status": "OK"})["status"] == "OK"


def test_error_contract_safe():
    import requests
    import server_ext
    from law_go_kr import LawGoKrError, LawGoKrAuthError, LawGoKrNotFound

    def _r(exc):
        def f():
            raise exc
        return f

    assert server_ext._safe(_r(LawGoKrAuthError("인증")))["status"] == "AUTH_ERROR"
    assert server_ext._safe(_r(LawGoKrNotFound("없음")))["status"] == "NOT_FOUND"
    assert server_ext._safe(_r(LawGoKrError("입력")))["status"] == "INVALID_INPUT"
    assert server_ext._safe(_r(requests.exceptions.Timeout("t")))["status"] == "UPSTREAM_ERROR"
    # 성공 경로: 리스트는 status+결과로 래핑, 빈 리스트는 NOT_FOUND
    assert server_ext._safe(lambda: [1])["status"] == "OK"
    assert server_ext._safe(lambda: [])["status"] == "NOT_FOUND"
    assert server_ext._safe(lambda: {"본문": "x"})["status"] == "OK"
    assert server_ext._safe(lambda: {"오류": "본문 없음"})["status"] == "NOT_FOUND"


def test_split_addendum_and_effective_summary():
    from law_go_kr import LawGoKrClient
    text = ("부칙 〈제21221호,2025.12.23〉\n"
            "제1조(시행일) 이 법은 2026년 1월 1일부터 시행한다. 다만, 다음 각 호의 개정규정은 각 호에서 정한 날부터 시행한다.\n"
            "1. 제57조의2의 개정규정: 2026년 7월 1일\n"
            "2. 제17조제3항의 개정규정: 2027년 1월 1일\n"
            "제2조(양도소득세에 관한 적용례) 제96조의 개정규정은 이 법 시행 이후 양도하는 분부터 적용한다.\n")
    parts = LawGoKrClient._split_addendum(text)
    assert len(parts) == 3, f"분할 수 이상: {len(parts)}"
    assert parts[0].startswith("부칙")
    assert parts[1].startswith("제1조")
    assert parts[2].startswith("제2조")
    summary = LawGoKrClient._effective_summary(text)
    assert "시행한다" in summary and "제57조의2" in summary, f"호별 시행일 요약 누락: {summary}"


def _run_all():
    fns = [(name, fn) for name, fn in sorted(globals().items())
           if name.startswith("test_") and callable(fn)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
