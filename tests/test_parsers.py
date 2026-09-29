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


def test_olta_exclude_reports_count_without_second_search():
    """중복 건수를 세려고 같은 검색을 또 돌리면 안 된다 — 클라이언트가 세어 돌려준다."""
    from olta_tax_ruling_search import OltaTaxLawClient

    client = OltaTaxLawClient()
    calls = []

    def _fake_uncached(keyword):
        calls.append(keyword)
        return {
            "tax_tribunal": {"total_count": 3, "items": [
                {"doc_no": "조심2026지0284", "tax_type": "취득세"},
                {"doc_no": "조심-2025-인-2268", "tax_type": "취득세"},  # 국세측과 중복
            ]},
            "court": {"total_count": 1, "items": [
                {"doc_no": "조심-2025-인-2268", "tax_type": "취득세"},  # 중복
            ]},
        }

    client._search_uncached = _fake_uncached
    result = client.search(
        keyword="취득세", categories=["tax_tribunal", "court"], view_count=20,
        exclude_doc_nos={"조심-2025-인-2268"},
    )
    assert result["excluded_count"] == 2, f"제외 건수 오답: {result.get('excluded_count')}"
    assert len(calls) == 1, f"검색을 {len(calls)}번 호출했다 — 1번이어야 한다"
    assert [it["doc_no"] for it in result["tax_tribunal"]["items"]] == ["조심2026지0284"]
    assert result["court"]["items"] == []


def test_olta_search_without_exclude_has_no_count_key():
    """일반 검색 응답에는 excluded_count가 붙지 않아야 한다 (응답 계약 유지)."""
    from olta_tax_ruling_search import OltaTaxLawClient

    client = OltaTaxLawClient()
    client._search_uncached = lambda kw: {
        "tax_tribunal": {"total_count": 1, "items": [{"doc_no": "조심2026지0284"}]},
    }
    result = client.search(keyword="취득세", view_count=20)
    assert "excluded_count" not in result, "제외 필터를 안 썼는데 키가 붙었다"


# ---------------------------------------------------------------------------
# 응답 총량 상한
# ---------------------------------------------------------------------------

def test_cap_response_trims_and_keeps_one_per_collection():
    """상한을 넘으면 뒤에서부터 덜어내되, 컬렉션마다 최소 1건은 남겨야 한다."""
    from server import _cap_response, _response_size

    def _big(n):
        return {"name_kr": "심판·심사·판례", "total_count": 999,
                "items": [{"title": "제목 " + "가" * 200, "doc_no": f"조심-2026-서-{i}"}
                          for i in range(n)]}

    result = {"precedent": _big(40), "question": _big(40)}
    assert _response_size(result) > 5000
    out = _cap_response(result, cap=5000)
    assert _response_size(out) <= 5000, f"상한 초과: {_response_size(out)}"
    assert out["precedent"]["items"] and out["question"]["items"], "컬렉션이 통째로 비었다"
    assert "_잘림" in out and "page" in out["_잘림"], f"잘림 안내 누락: {out.get('_잘림')}"
    # total_count는 생략 전 기준 그대로여야 한다 (건수를 0으로 오해하면 안 됨)
    assert out["precedent"]["total_count"] == 999


def test_cap_response_leaves_small_responses_untouched():
    """기본값 응답(1만 자 안쪽)은 손대지 않는다."""
    from server import _cap_response

    result = {"precedent": {"total_count": 3,
                            "items": [{"title": "가", "doc_no": "조심-2026-서-1"}]}}
    out = _cap_response(result, cap=30000)
    assert out == result and "_잘림" not in out


def test_cap_response_reaches_nested_collections():
    """통합검색은 olta 카테고리가 한 단계 안쪽에 있다 — 못 찾으면 상한이 무력해진다."""
    from server import _cap_response, _response_size

    items = [{"title": "제목 " + "나" * 200, "doc_no": f"조심2026지{i}"} for i in range(40)]
    result = {"status": "OK",
              "nts_precedent": {"total_count": 9, "items": list(items)},
              "olta_precedent": {"tax_tribunal": {"total_count": 9, "items": list(items)},
                                 "court": {"total_count": 9, "items": list(items)}}}
    out = _cap_response(result, cap=6000)
    assert _response_size(out) <= 6000, f"중첩 컬렉션 미도달: {_response_size(out)}"
    assert out["olta_precedent"]["tax_tribunal"]["items"], "중첩 컬렉션이 통째로 비었다"


# ---------------------------------------------------------------------------
# 붙임 전문 (HWP 5.0) — hwp_text.py 레코드 파서
#
# 붙임 실물(HWP 170~290KB)을 픽스처로 두는 대신, 깨지기 쉬운 부분인
# 레코드 헤더·제어문자 폭 계산을 합성 바이트로 직접 검증한다.
# ---------------------------------------------------------------------------

def _hwp_record(tag: int, payload: bytes, level: int = 0) -> bytes:
    """레코드 헤더(tag|level|size) + 페이로드. size가 0xFFF 이상이면 확장 4바이트."""
    import struct
    base = (tag & 0x3FF) | ((level & 0x3FF) << 10)
    if len(payload) >= 0xFFF:
        header = base | (0xFFF << 20)
        return struct.pack("<I", header) + struct.pack("<I", len(payload)) + payload
    header = base | (len(payload) << 20)
    return struct.pack("<I", header) + payload


def _utf16(s: str) -> bytes:
    return s.encode("utf-16-le")


def _ctrl(code: int) -> bytes:
    """인라인/확장 제어문자 1개 = 8워드(16바이트)"""
    import struct
    return struct.pack("<H", code) + bytes(12) + struct.pack("<H", code)


def test_hwp_para_text_skips_control_chars():
    """제어문자를 폭만큼 못 건너뛰면 본문에 쓰레기 글자가 섞인다."""
    import struct
    from hwp_text import _para_text

    # 표 컨트롤(11=확장) 뒤에 본문이 이어지는 형태
    data = _utf16("주문") + _ctrl(11) + _utf16("심판청구를 기각한다.")
    assert _para_text(data) == "주문심판청구를 기각한다.", repr(_para_text(data))

    # 인라인 컨트롤 중 탭(9)은 탭으로 살린다
    data = _utf16("가.") + _ctrl(9) + _utf16("청구인")
    assert _para_text(data) == "가.\t청구인", repr(_para_text(data))

    # 줄바꿈(10)은 1워드만 차지한다 — 16바이트로 건너뛰면 뒤 글자가 잘려나간다
    data = _utf16("첫줄") + struct.pack("<H", 10) + _utf16("둘째줄")
    assert _para_text(data) == "첫줄\n둘째줄", repr(_para_text(data))


def test_hwp_record_extended_size():
    """4095바이트 넘는 문단은 확장 크기 헤더를 쓴다 — 못 읽으면 긴 결정문이 통째로 깨진다."""
    from hwp_text import _iter_records, HWPTAG_PARA_TEXT

    long_text = "가" * 3000  # 6000바이트 > 0xFFF
    stream = (_hwp_record(HWPTAG_PARA_TEXT, _utf16("짧은 문단"))
              + _hwp_record(HWPTAG_PARA_TEXT, _utf16(long_text)))
    recs = list(_iter_records(stream))
    assert len(recs) == 2, f"레코드 수 이상: {len(recs)}"
    assert len(recs[1][1]) == 6000, f"확장 크기 오독: {len(recs[1][1])}"


def test_hwp_rejects_non_ole_with_clear_message():
    """PDF·HWPX 붙임을 조용히 빈 값으로 넘기면 '자료 없음'으로 오해된다."""
    from hwp_text import extract_text, HwpExtractError

    for blob, label in [(b"%PDF-1.7 ...", "PDF"), (b"PK\x03\x04 ...", "HWPX(zip)")]:
        try:
            extract_text(blob)
        except HwpExtractError as e:
            assert "HWP 5.0" in str(e), f"{label}: 안내 문구 누락 — {e}"
        else:
            raise AssertionError(f"{label}인데 예외가 나지 않았다")


# 표 — 실제 붙임과 같은 레코드 배치를 합성한다:
#   PARA_HEADER(L) > CTRL_HEADER('tbl ', L+1) > TABLE(L+2),
#   셀마다 LIST_HEADER(L+2) 뒤에 그 셀의 PARA_HEADER(L+2) > PARA_TEXT(L+3)

_HWPTAG_PARA_HEADER = 0x10 + 50  # 66 — 파서는 안 쓰지만 실제 배치대로 넣는다


def _para(text: str, level: int = 0) -> bytes:
    from hwp_text import HWPTAG_PARA_TEXT
    return (_hwp_record(_HWPTAG_PARA_HEADER, bytes(12), level)
            + _hwp_record(HWPTAG_PARA_TEXT, _utf16(text), level + 1))


def _table(rows: int, cols: int, cells: list, level: int = 0) -> bytes:
    """cells: [(col, row, colspan, rowspan, [문단 글자 또는 중첩 표 바이트, ...]), ...]"""
    import struct
    from hwp_text import HWPTAG_CTRL_HEADER, HWPTAG_LIST_HEADER, HWPTAG_TABLE
    out = (_hwp_record(_HWPTAG_PARA_HEADER, bytes(12), level)  # 표를 품은 문단
           + _hwp_record(HWPTAG_CTRL_HEADER, b" lbt" + bytes(40), level + 1)
           + _hwp_record(HWPTAG_TABLE, struct.pack("<IHH", 0, rows, cols) + bytes(20), level + 2))
    for col, row, colspan, rowspan, contents in cells:
        out += _hwp_record(HWPTAG_LIST_HEADER,
                           struct.pack("<HHI", len(contents), 0, 0)
                           + struct.pack("<HHHH", col, row, colspan, rowspan) + bytes(18),
                           level + 2)
        for c in contents:
            out += c if isinstance(c, bytes) else _para(c, level + 2)
    return out


def test_hwp_table_to_markdown():
    """숫자 표를 칸마다 한 줄로 풀면 어느 숫자가 어느 열인지 알 수 없다 (심사 결정문 세액표)."""
    from hwp_text import _body_text

    stream = (_para("가. 처분 내용")
              + _table(4, 3, [
                  (0, 0, 1, 2, ["구분"]),            # 2행 병합 — 머리행이 2줄
                  (1, 0, 2, 1, ["세액"]),            # 2열 병합 — 열 제목에 반복돼야 함
                  (1, 1, 1, 1, ["당초"]), (2, 1, 1, 1, ["경정"]),
                  (0, 2, 1, 1, ["양도가액"]), (1, 2, 1, 1, ["100"]), (2, 2, 1, 1, ["80"]),
                  (0, 3, 1, 1, ["가산세"]),
                  (1, 3, 1, 1, ["253,300\n(25,330)"]),  # 칸 안 줄바꿈 — 한 칸이 두 줄로 쪼개지면 안 됨
                  (2, 3, 1, 1, ["a|b"]),
              ])
              + _para("나. 청구인 주장"))
    text = _body_text(stream)
    lines = text.split("\n")
    assert "| 구분 | 세액 당초 | 세액 경정 |" in lines, text
    assert "|---|---|---|" in lines, text
    assert "| 양도가액 | 100 | 80 |" in lines, text
    assert "| 가산세 | 253,300 (25,330) | a\\|b |" in lines, text  # 파이프는 이스케이프
    assert text.index("가. 처분 내용") < text.index("| 구분") < text.index("나. 청구인 주장"), text


def test_hwp_layout_tables_stay_text():
    """본문을 감싼 1칸 틀·서식형 격자는 표가 아니라 글 — 틀 안의 데이터 표는 다시 표로."""
    from hwp_text import _body_text

    data = _table(2, 2, [(0, 0, 1, 1, ["연도"]), (1, 0, 1, 1, ["세액"]),
                         (0, 1, 1, 1, ["2024"]), (1, 1, 1, 1, ["500"])], level=2)
    frame = _table(1, 1, [(0, 0, 1, 1, ["1. 처분개요", data, "2. 판단"])])
    text = _body_text(frame)
    assert "1. 처분개요\n" in text and "\n2. 판단" in text, text
    assert "| 연도 | 세액 |" in text and "| 2024 | 500 |" in text, text

    # 서식형: 행마다 칸 배치가 다른 가는 격자 — 모든 열로 갈린 행이 없다
    form = _table(3, 6, [
        (0, 0, 2, 1, ["[문서번호]"]), (2, 0, 4, 1, ["재산세제과-213"]),
        (0, 1, 6, 1, ["[제 목]"]),
        (0, 2, 3, 1, ["[세목]"]), (3, 2, 3, 1, ["상증"]),
    ])
    text = _body_text(form)
    assert "|" not in text, f"서식형 격자가 Markdown 표로 나갔다:\n{text}"
    assert text.split("\n") == ["[문서번호]", "재산세제과-213", "[제 목]", "[세목]", "상증"], text


def test_hwp_table_bogus_size_falls_back_to_text():
    """행·열 수가 깨진 표에 65535×65535 격자를 만들면 서버가 멈춘다 — 글로 내보내야 한다."""
    from hwp_text import _body_text

    text = _body_text(_table(65535, 65535, [(0, 0, 1, 1, ["첫 칸"]), (1, 0, 1, 1, ["둘째 칸"])]))
    assert text.split("\n") == ["첫 칸", "둘째 칸"], text


def test_full_text_blank_attachment():
    """글자 없는 붙임(표본 197건 중 12건)을 OK+빈 전문으로 주면 '본문 없음'으로 오해된다."""
    import hwp_text
    from nts_tax_ruling_search import NtsTaxLawClient

    client = NtsTaxLawClient()
    client.get_attachment_meta = lambda fid: [{"fleDwldUri": "/x", "fleXsnNm": "hwp"}]
    client.download_attachment = lambda uri: b"hwp"
    orig = hwp_text.extract_text
    hwp_text.extract_text = lambda data: ""
    try:
        def lookup(content):
            client.get_by_doc_no = lambda d: {"found": True, "items": [
                {"doc_no": d, "file_id": "1", "content": content}]}
            return client.get_full_text("기획재정부 법인세제과-84")

        got = lookup("분할신설법인으로 이전하지 못한 경우는 분할하기 어려운 자산에 해당하지 않는 것임")
        assert got["status"] == "OK" and got["전문"].startswith("분할신설법인"), got
        assert "비어" in got.get("비고", ""), got

        # 심판·심사의 content는 상수 — 이걸 전문이라고 주면 안 된다
        got = lookup("결정내용은 붙임과 같습니다.")
        assert got["status"] == "NOT_FOUND" and "비어" in got["message"], got
    finally:
        hwp_text.extract_text = orig


def test_nts_parse_exposes_file_id():
    """붙임 파일 ID를 파서가 버리면 전문 조회 경로 자체가 막힌다."""
    from nts_tax_ruling_search import NtsTaxLawClient
    client = NtsTaxLawClient()
    raw = json.loads(read_gz("nts_search.json.gz"))
    result = client._parse(raw)
    for coll in ("question", "precedent"):
        item = result[coll]["items"][0]
        assert item.get("file_id"), f"{coll}: file_id 누락 — {item}"
        assert item["file_id"].isdigit(), f"{coll}: file_id 형식 이상 — {item['file_id']}"


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
