# -*- coding: utf-8 -*-
"""
compare_with_site.py — 사이트 구조 변경(드리프트) 감지 (라이브)

픽스처 캡처 당시와 같은 검색을 원천 사이트에 실제로 보내, 지금도 같은 구조로
파싱되는지 확인한다. NTS·OLTA는 비공식 엔드포인트/HTML 스크래핑이라 사이트가
개편되면 소리 없이 깨지는데, 이 스크립트가 그걸 조기에 잡는 용도다.

실행 (저장소 루트에서, law.go.kr 점검에는 LAW_API_OC 필요):
    python tests/compare_with_site.py

종료 코드 0 = 전부 정상, 1 = 드리프트 감지 (출력의 DRIFT 줄 확인).
주기적으로(예: 주 1회) 돌려보고, DRIFT가 나오면 refresh_fixtures.py로 픽스처를
갱신한 뒤 test_parsers.py로 파서 수정을 검증한다.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"

_drift = 0
_unreachable = 0


def report(source: str, ok: bool, detail: str = ""):
    global _drift
    if ok:
        print(f"PASS   {source} {detail}")
    else:
        _drift += 1
        print(f"DRIFT  {source} — {detail}")


def report_unreachable(source: str, detail: str):
    """네트워크·SSL 장애는 구조 변경이 아니다 — 드리프트로 세지 않는다
    (주기 실행 시 일시 장애가 '사이트 개편' 오탐을 내면 경보를 신뢰할 수 없게 된다)."""
    global _unreachable
    _unreachable += 1
    print(f"SKIP   {source} — 접속 실패(구조 변경 아님): {detail}")


def check_nts(m: dict):
    import requests
    from nts_tax_ruling_search import NtsTaxLawClient, NtsParseError
    client = NtsTaxLawClient()
    try:
        result = client.search(keyword=m["nts"]["keyword"],
                               collections=["question", "precedent"],
                               view_count=5, use_cache=False)
    except NtsParseError as e:
        report("NTS 검색", False, f"파싱 실패: {e}")
        return
    except requests.exceptions.RequestException as e:
        report_unreachable("NTS 검색", f"{type(e).__name__}: {e}")
        return
    except Exception as e:  # noqa: BLE001
        report("NTS 검색", False, f"예상 밖 오류: {type(e).__name__}: {e}")
        return
    if result.get("status") != "OK":
        report("NTS 검색", False, f"status={result.get('status')} — 대표 검색어가 0건이면 "
               "구조 변경 또는 검색어 문제")
        return
    # 요청한 컬렉션을 모두 검증한다 — 하나만 보면 부분 드리프트(예: precedent만 필드 변경)를 놓친다
    for coll in ("question", "precedent"):
        data = result.get(coll)
        if not data or not data.get("items"):
            report(f"NTS 검색[{coll}]", False,
                   f"결과 없음(total={(data or {}).get('total_count')}) — 컬렉션 코드 변경 의심")
            continue
        missing = [f for f in ("title", "doc_no", "date", "doc_type") if not data["items"][0].get(f)]
        if missing:
            report(f"NTS 검색[{coll}]", False, f"필드 누락: {missing} — 응답 필드명 변경 의심")
            continue
        report(f"NTS 검색[{coll}]", True, f"({data.get('total_count')}건, 필드 정상)")


def check_olta(m: dict):
    import requests
    from olta_tax_ruling_search import OltaTaxLawClient, OltaParseError
    client = OltaTaxLawClient()
    try:
        result = client.search(keyword=m["olta"]["keyword"], view_count=3, use_cache=False)
    except OltaParseError as e:
        report("OLTA 검색", False, f"파싱 실패(카나리 포함): {e}")
        return
    except requests.exceptions.RequestException as e:
        report_unreachable("OLTA 검색", f"{type(e).__name__}: {e}")
        return
    except Exception as e:  # noqa: BLE001
        report("OLTA 검색", False, f"예상 밖 오류: {type(e).__name__}: {e}")
        return
    if result.get("status") != "OK" or "tax_tribunal" not in result:
        report("OLTA 검색", False,
               f"status={result.get('status')}, 카테고리={[k for k in result if not k.startswith('_')]} "
               "— p.se_title/ul.search_out 구조 변경 의심")
        return
    item = result["tax_tribunal"]["items"][0] if result["tax_tribunal"]["items"] else {}
    problems = []
    if not item.get("doc_no"):
        problems.append("doc_no")
    if not (item.get("date") and re.match(r"^\d{8}$", item["date"])):
        problems.append("date")
    if not item.get("doc_id"):
        problems.append("doc_id")
    if problems:
        report("OLTA 검색", False, f"항목 필드 이상: {problems}")
        return
    report("OLTA 검색", True, f"({result['tax_tribunal']['total_count']}건, 필드 정상)")

    # 본문 조회 경로도 확인 (doc_id → 상세 페이지 파싱)
    try:
        detail = client.get_detail("tax_tribunal", item["doc_id"])
        report("OLTA 본문", detail.get("status") == "OK",
               "본문 추출 실패 — 상세 페이지 구조 변경 의심" if detail.get("status") != "OK"
               else f"({len(detail.get('content', ''))}자)")
    except requests.exceptions.RequestException as e:
        report_unreachable("OLTA 본문", f"{type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        report("OLTA 본문", False, f"예상 밖 오류: {type(e).__name__}: {e}")


def check_law(m: dict):
    import requests
    import law_go_kr as L
    if not L.OC:
        print("SKIP   law.go.kr — LAW_API_OC 미설정")
        return
    lm = m.get("law")
    if not lm:
        print("SKIP   law.go.kr — 픽스처 manifest에 law 항목 없음")
        return
    client = L.LawGoKrClient()
    try:
        rows = client.law_history(lm["law_name"], law_id=lm["law_id"])
        ok = len(rows) >= lm["history_rows"] and rows[0]["시행일자"] <= lm["history_first_date"]
        report("law 연혁", ok,
               f"{len(rows)}행(캡처시 {lm['history_rows']}), 최초 {rows[0]['시행일자']}"
               + ("" if ok else " — 연혁 감소/과거 누락은 API 변경 의심"))

        today_mst = rows[-1]["MST"]
        art = client.law_article(lm["mst_current"], "104", max_chars=1000)
        report("law 조문", "양도소득세의 세율" in art.get("조문제목", ""),
               f"조문제목={art.get('조문제목')!r}")

        add = client.law_addenda(mst=lm["mst_current"], recent=3)
        report("law 부칙", add.get("부칙총수", 0) > 50, f"부칙 {add.get('부칙총수')}건")
        _ = today_mst
    except L.LawGoKrAuthError as e:
        report_unreachable("law.go.kr", f"인증 실패(OC·IP 등록 확인): {e}")
    except requests.exceptions.RequestException as e:
        report_unreachable("law.go.kr", f"{type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        report("law.go.kr", False, f"실패: {type(e).__name__}: {e}")


def main():
    with open(FIXTURES / "manifest.json", encoding="utf-8") as f:
        m = json.load(f)
    print(f"픽스처 캡처 시점: {m.get('captured_at')}")
    check_nts(m)
    check_olta(m)
    check_law(m)
    if _drift:
        print(f"\nDRIFT {_drift}건 — refresh_fixtures.py로 픽스처를 갱신하고 파서를 점검하세요.")
        return 1
    if _unreachable:
        print(f"\n구조 드리프트 없음 (접속 실패로 확인 못한 항목 {_unreachable}건 — "
              "네트워크 회복 후 다시 실행하세요).")
        return 0
    print("\n전부 정상 — 파서와 원천 사이트 구조가 일치합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
