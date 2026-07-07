"""
test_mcp_client.py

Claude 커넥터를 거치지 않고, MCP 서버에 직접 요청을 보내서
서버 자체가 정상 동작하는지 확인하는 독립 테스트 스크립트입니다.

Claude 쪽 채팅에서 "도구가 안 잡힌다"는 문제가 생겼을 때,
- 이 스크립트가 성공하면 -> 서버는 정상. 문제는 Claude 커넥터 인식/캐싱 쪽.
- 이 스크립트도 실패하면 -> 서버 자체(Railway 배포, 코드) 문제.
로 원인을 빠르게 나눠볼 수 있습니다.

사용법:
    python test_mcp_client.py
    python test_mcp_client.py --url https://web-production-10fe2.up.railway.app/mcp
    python test_mcp_client.py --url http://127.0.0.1:8000/mcp
"""

import argparse
import json
import sys

import requests

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _parse_sse(text: str) -> dict:
    """서버가 text/event-stream(SSE) 형식으로 응답하므로 'data: {...}' 줄만 파싱합니다."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    # SSE가 아니라 순수 JSON으로 온 경우 (stateless 모드 등)
    return json.loads(text)


def call(url: str, payload: dict, session_id: str = None) -> dict:
    headers = dict(HEADERS)
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=20)
    resp.raise_for_status()
    resp.encoding = "utf-8"  # 서버가 인코딩을 명시하지 않는 경우 requests가 잘못 추측하는 것을 방지
    return _parse_sse(resp.text)


def run(url: str) -> bool:
    ok = True

    print(f"대상 서버: {url}\n")

    # 1) initialize
    print("[1/4] initialize 핸드셰이크...")
    try:
        result = call(url, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke-test", "version": "1.0"},
            },
        })
        server_info = result.get("result", {}).get("serverInfo", {})
        print(f"    성공 — 서버 이름: {server_info.get('name')}, 버전: {server_info.get('version')}")
    except Exception as e:
        print(f"    실패: {e}")
        return False

    # 2) tools/list
    print("[2/4] tools/list 조회...")
    try:
        result = call(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = result.get("result", {}).get("tools", [])
        names = [t["name"] for t in tools]
        print(f"    성공 — 등록된 도구: {names}")
        if "nts_ruling_search" not in names or "nts_ruling_get_by_doc_no" not in names:
            print("    경고: 예상한 도구 이름이 목록에 없습니다.")
            ok = False
    except Exception as e:
        print(f"    실패: {e}")
        return False

    # 3) tools/call - nts_ruling_get_by_doc_no
    print("[3/4] nts_ruling_get_by_doc_no 호출 (조심-2023-서-9465)...")
    try:
        result = call(url, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {
                "name": "nts_ruling_get_by_doc_no",
                "arguments": {"doc_no": "조심-2023-서-9465"},
            },
        })
        text = result["result"]["content"][0]["text"]
        parsed = json.loads(text)
        if parsed.get("found"):
            print(f"    성공 — 문서 찾음: {parsed['items'][0]['title'][:40]}...")
        else:
            print(f"    경고 — 문서를 못 찾음: {parsed.get('message')}")
            ok = False
    except Exception as e:
        print(f"    실패: {e}")
        ok = False

    # 4) tools/call - nts_ruling_search
    print("[4/4] nts_ruling_search 호출 (키워드: 조정대상지역)...")
    try:
        result = call(url, {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {
                "name": "nts_ruling_search",
                "arguments": {
                    "keyword": "조정대상지역",
                    "collections": ["precedent"],
                    "view_count": 3,
                    "include_full_text": False,
                },
            },
        })
        text = result["result"]["content"][0]["text"]
        parsed = json.loads(text)
        precedent = parsed.get("precedent", {})
        print(f"    성공 — 판례 총 {precedent.get('total_count')}건 중 {len(precedent.get('items', []))}건 반환")
    except Exception as e:
        print(f"    실패: {e}")
        ok = False

    print()
    if ok:
        print("=== 결과: 서버 정상 작동 중입니다. ===")
        print("Claude 쪽에서 도구가 안 보인다면 서버 문제가 아니라 Claude 커넥터 인식/캐싱 쪽 문제입니다.")
    else:
        print("=== 결과: 일부 항목에서 문제가 발견되었습니다. 위 로그를 확인해 주세요. ===")
    return ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="nts-tax-mcp 서버 독립 검증 스크립트")
    parser.add_argument(
        "--url",
        default="https://web-production-10fe2.up.railway.app/mcp",
        help="테스트할 MCP 서버 URL (기본값: 배포된 Railway 서버)",
    )
    args = parser.parse_args()

    success = run(args.url)
    sys.exit(0 if success else 1)
