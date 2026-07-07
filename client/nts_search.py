#!/usr/bin/env python3
"""
nts_search.py
nts-tax-mcp 서버를 커맨드라인에서 바로 검색하는 CLI 도구.
Claude MCP 커넥터를 거치지 않고, 서버에 직접 HTTP로 요청합니다.

사용 예:
    python nts_search.py --ping
    python nts_search.py "홍콩 거주자" -c precedent -n 30
    python nts_search.py "조정대상지역" -c ruling precedent --from 20230101 --to 20241231 --json > result.json
    python nts_search.py --doc-no "조심-2023-서-9465"
    python nts_search.py --list-tools
"""

import argparse
import json
import sys

from nts_client import (
    DEFAULT_ENDPOINT,
    VALID_COLLECTIONS,
    NtsClientError,
    ping,
    list_tools,
    nts_ruling_search,
    nts_ruling_get_by_doc_no,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="국세법령정보시스템(nts-tax-mcp) 커맨드라인 검색 도구")
    parser.add_argument("keyword", nargs="?", help="검색어")
    parser.add_argument(
        "-c", "--collections", nargs="+", choices=sorted(VALID_COLLECTIONS),
        help="검색 범위 (기본값: 전체). 예: -c ruling precedent",
    )
    parser.add_argument("-n", "--view-count", type=int, default=20, help="결과 개수 (기본 20)")
    parser.add_argument("-p", "--page", type=int, default=1, help="페이지 번호 (기본 1)")
    parser.add_argument("--from", dest="date_from", help="검색 시작일 YYYYMMDD")
    parser.add_argument("--to", dest="date_to", help="검색 종료일 YYYYMMDD")
    parser.add_argument(
        "--sort", choices=["relevance", "date_desc", "date_asc"], default="relevance",
        help="정렬 방식 (기본 relevance)",
    )
    parser.add_argument("--tax-type", dest="tax_type_filter", help="세목 필터 (예: 양도소득세)")
    parser.add_argument("--no-full-text", action="store_true", help="본문 생략, 요약만 조회 (응답 가볍게)")
    parser.add_argument("--doc-no", help="사건번호로 직접 조회 (예: 조심-2023-서-9465)")
    parser.add_argument("--json", action="store_true", help="원본 JSON 그대로 stdout에 출력 (파이프/저장용)")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="MCP 서버 URL (기본: 배포된 Railway 서버)")
    parser.add_argument("--ping", action="store_true", help="서버 연결 상태만 점검하고 종료")
    parser.add_argument("--list-tools", action="store_true", help="등록된 도구 목록만 조회하고 종료")
    return parser


def print_search_result(result: dict):
    guidance = result.pop("_guidance", None)
    for coll_name, coll_data in result.items():
        if not isinstance(coll_data, dict) or "items" not in coll_data:
            continue
        print(f"\n=== {coll_data.get('name_kr', coll_name)} (총 {coll_data.get('total_count', 0)}건) ===")
        for item in coll_data.get("items", []):
            print(
                f"- [{item.get('doc_type')}][{item.get('source_org')}] {item.get('title')} "
                f"({item.get('doc_no')}, {item.get('date')})"
            )
    if guidance:
        print(f"\n안내: {guidance}")


def print_doc_no_result(result: dict):
    if result.get("found"):
        for item in result["items"]:
            print(f"[{item.get('doc_type')}][{item.get('source_org')}] {item.get('title')}")
            print(f"  문서번호: {item.get('doc_no')} / 날짜: {item.get('date')} / 세목: {item.get('tax_type')}")
            print(f"  요지: {item.get('summary')}")
            if item.get("content"):
                content = item["content"]
                preview = content if len(content) <= 300 else content[:300] + "..."
                print(f"  본문: {preview}")
    else:
        print(result.get("message"))


def main():
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.ping:
            result = ping(endpoint=args.endpoint)
            print(f"연결 성공 — 서버: {result['server_name']} v{result['server_version']}")
            return

        if args.list_tools:
            tools = list_tools(endpoint=args.endpoint)
            print("등록된 도구:", ", ".join(tools))
            return

        if args.doc_no:
            result = nts_ruling_get_by_doc_no(args.doc_no, endpoint=args.endpoint)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print_doc_no_result(result)
            return

        if not args.keyword:
            parser.error("검색어를 입력하거나 --ping / --doc-no / --list-tools 중 하나를 사용하세요.")

        result = nts_ruling_search(
            keyword=args.keyword,
            collections=args.collections,
            page=args.page,
            view_count=args.view_count,
            date_from=args.date_from,
            date_to=args.date_to,
            sort=args.sort,
            tax_type_filter=args.tax_type_filter,
            include_full_text=not args.no_full_text,
            endpoint=args.endpoint,
        )

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_search_result(result)

    except NtsClientError as e:
        print(f"오류: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
