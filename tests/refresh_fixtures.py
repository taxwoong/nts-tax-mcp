# -*- coding: utf-8 -*-
"""
refresh_fixtures.py — 파서 회귀 테스트용 픽스처 갱신 (라이브 캡처)

세 원천(NTS·OLTA·law.go.kr)의 실제 응답을 tests/fixtures/*.gz 로 저장한다.
test_parsers.py는 이 픽스처만으로 네트워크 없이 파싱 로직을 검증하고,
compare_with_site.py는 라이브 응답을 픽스처 캡처 당시 구조와 대조해
사이트 개편(파서 파손)을 감지한다.

실행 (저장소 루트에서, law.go.kr 캡처에는 LAW_API_OC 필요):
    python tests/refresh_fixtures.py

주의: 원천 사이트에 실제 요청을 보낸다 (총 10회 안팎, 클라이언트 자체 스로틀 적용).
"""
import gzip
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# 캡처에 쓰는 대표 검색어 — 바꾸면 test_parsers.py의 기대값도 함께 확인할 것
NTS_KEYWORD = "조정대상지역"
OLTA_KEYWORD = "취득세"
EMPTY_KEYWORD = "zzqq없는검색어xx991122"
LAW_NAME, LAW_ID = "소득세법", "001565"
OLD_DATE = "20200101"  # 구 시행본 기준일 (diff 테스트용)


def scrub(text: str) -> str:
    """픽스처를 공개 저장소에 커밋하기 전 민감정보 제거 (2026-08-28 실측 기준).

    ① law.go.kr 검색 응답(eflaw 등)에는 요청에 쓴 OC(기관코드)가 그대로 에코된다.
    ② NTS 응답의 debugMsg 필드에는 국세청 내부망 주소·포트·검색엔진 설정이 들어 있다
       (예: [IP] http://10.x.x.x [PORT] 18000). 원천 인프라 정보라 저장하지 않는다."""
    oc = os.environ.get("LAW_API_OC", "")
    if oc and oc in text:
        text = text.replace(oc, "OC-MASKED")
    # 사설 IP 대역(10/172.16-31/192.168) 주소를 마스킹
    text = re.sub(r"\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))(?:\.\d{1,3}){1,3}\b",
                  "0.0.0.0", text)
    return text


def save_gz(name: str, text: str):
    text = scrub(text)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / name
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(text)
    print(f"  저장: {name} ({path.stat().st_size:,} bytes gz)")


def capture_nts(manifest: dict):
    print("[1/3] NTS (taxlaw.nts.go.kr) 캡처")
    from nts_tax_ruling_search import NtsTaxLawClient

    client = NtsTaxLawClient()
    captured = {}
    orig = NtsTaxLawClient._post_search

    def tee(self, param_data):
        raw = orig(self, param_data)
        captured["raw"] = raw
        return raw

    NtsTaxLawClient._post_search = tee
    try:
        client.search(keyword=NTS_KEYWORD, collections=["question", "precedent"],
                      view_count=5, use_cache=False)
        save_gz("nts_search.json.gz", json.dumps(captured["raw"], ensure_ascii=False))
        client.search(keyword=EMPTY_KEYWORD, view_count=3, use_cache=False)
        save_gz("nts_search_empty.json.gz", json.dumps(captured["raw"], ensure_ascii=False))
    finally:
        NtsTaxLawClient._post_search = orig
    manifest["nts"] = {"keyword": NTS_KEYWORD, "collections": ["question", "precedent"],
                       "empty_keyword": EMPTY_KEYWORD}


def capture_olta(manifest: dict):
    print("[2/3] OLTA (olta.re.kr) 캡처")
    from olta_tax_ruling_search import OltaTaxLawClient

    client = OltaTaxLawClient()
    save_gz("olta_search.html.gz", client._post_search(OLTA_KEYWORD))
    save_gz("olta_search_empty.html.gz", client._post_search(EMPTY_KEYWORD))
    manifest["olta"] = {"keyword": OLTA_KEYWORD, "empty_keyword": EMPTY_KEYWORD}


def capture_law(manifest: dict):
    print("[3/3] law.go.kr 캡처 (LAW_API_OC 필요)")
    import law_go_kr as L

    if not L.OC:
        print("  건너뜀: LAW_API_OC 미설정 — law 픽스처는 갱신되지 않음")
        return

    captured = []  # (endpoint, params, text)
    orig = L._get

    def tee(endpoint, **params):
        text = orig(endpoint, **params)
        captured.append((endpoint, dict(params), text))
        return text

    # 캡처 시점을 고정해 두고 테스트가 이 날짜를 쓰게 한다 — date_to를 생략하면
    # 도구가 '오늘'을 쓰므로, 픽스처에 미래 시행본이 있는 한 실행 날짜에 따라
    # 다른 MST가 선택되어 오프라인 테스트가 어느 날 갑자기 깨진다
    date_to = time.strftime("%Y%m%d")
    L._get = tee
    try:
        client = L.LawGoKrClient()
        history = client.law_history(LAW_NAME, law_id=LAW_ID)
        # 실제 도구 경로를 그대로 태워, 테스트에 필요한 시행본 XML이 빠짐없이 캡처되게 한다
        # (단계 시행 소급 탐색이 추가 시행본을 조회할 수 있음)
        diff = client.law_article_diff(LAW_NAME, "104", OLD_DATE, date_to,
                                       law_id=LAW_ID, max_chars=200_000, find_change=False)
        client.law_article_as_of(LAW_NAME, date_to, "104", law_id=LAW_ID, max_chars=200_000)
    finally:
        L._get = orig
    cur = diff["비교기준"]["B"]["시행본"]
    old = diff["비교기준"]["A"]["시행본"]

    eflaw_pages, seen = [], set()
    for endpoint, params, text in captured:
        if params.get("target") == "eflaw":
            page = params.get("page", 1)
            if page not in seen:
                seen.add(page)
                save_gz(f"law_eflaw_p{page}.xml.gz", text)
                eflaw_pages.append(page)
        elif params.get("target") == "law" and params.get("MST"):
            save_gz(f"law_{params['MST']}.xml.gz", text)

    manifest["law"] = {
        "law_name": LAW_NAME, "law_id": LAW_ID,
        "mst_current": cur["MST"], "current_시행일자": cur["시행일자"],
        "mst_old": old["MST"], "old_시행일자": old["시행일자"],
        "old_date": OLD_DATE, "date_to": date_to,
        "eflaw_pages": sorted(eflaw_pages),
        "history_rows": len(history),
        "history_first_date": history[0]["시행일자"],
    }


def main():
    prev = {}
    path = FIXTURES / "manifest.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            prev = json.load(f)

    manifest = {"captured_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    capture_nts(manifest)
    capture_olta(manifest)
    capture_law(manifest)
    # 캡처를 건너뛴 소스(예: LAW_API_OC 미설정)의 항목은 기존 값을 유지한다 —
    # 매번 새로 쓰면 그 소스의 픽스처 파일은 남는데 manifest 항목만 사라져 테스트가 깨진다
    for key in ("nts", "olta", "law"):
        if key not in manifest and key in prev:
            manifest[key] = prev[key]
            print(f"  manifest의 기존 '{key}' 항목 유지 (이번 실행에서 캡처 안 됨)")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("완료 — manifest.json 갱신됨. test_parsers.py를 실행해 파싱을 확인하세요.")


if __name__ == "__main__":
    main()
