"""
nts_client.py

Claude MCP 커넥터를 거치지 않고, nts-tax-mcp 서버의 HTTP(MCP JSON-RPC) 엔드포인트를
직접 호출하는 재사용 가능한 클라이언트입니다.

왜 필요한가:
  Claude 채팅에서 커넥터로 연결하면 가끔 도구 인식/디스커버리 단계가 불안정할 때가 있습니다.
  이 클라이언트는 그 계층을 완전히 우회해서, MCP 프로토콜(JSON-RPC 2.0 + SSE)로
  서버에 직접 요청을 보냅니다. 서버가 살아있는 한 항상 동일하게 동작합니다.

정상 동작을 위한 핵심 3가지:
  1) 요청 헤더에 "Accept: application/json, text/event-stream"을 반드시 포함해야
     서버가 정상적으로 응답합니다. 이게 없으면 406 Not Acceptable 에러가 납니다.
  2) 응답 바디는 "event: message\\r\\ndata: {...}\\r\\n\\r\\n" 형태의 SSE이므로,
     "data:" 뒤의 JSON만 뽑아 파싱해야 합니다.
  3) tools/call 결과는 JSON-RPC 결과 안에 다시 텍스트로 인코딩된 JSON이 들어있으므로
     (result.content[0].text), 이걸 한 번 더 json.loads 해야 실제 검색 결과가 나옵니다.

사용 예:
    from nts_client import nts_ruling_search, nts_ruling_get_by_doc_no, ping

    print(ping())
    result = nts_ruling_search("조정대상지역", collections=["precedent"], view_count=5)
    detail = nts_ruling_get_by_doc_no("조심-2023-서-9465")
"""

import json
import time
from typing import Optional

import requests

DEFAULT_ENDPOINT = "https://web-production-10fe2.up.railway.app/mcp"

VALID_COLLECTIONS = {"form", "statute", "ruling", "precedent", "old_ruling", "intl", "hometax"}

_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


class NtsClientError(Exception):
    """nts-tax-mcp 클라이언트 관련 오류"""


def _parse_sse(text: str) -> dict:
    """'data: {...}' 형태의 SSE 응답에서 JSON만 추출합니다."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    # SSE가 아니라 순수 JSON으로 온 경우 (설정에 따라 달라질 수 있음)
    return json.loads(text)


def _rpc_call(
    endpoint: str,
    payload: dict,
    timeout: float = 20.0,
    max_retries: int = 3,
    backoff_base: float = 0.5,
) -> dict:
    """JSON-RPC 요청을 보내고 SSE 응답을 파싱합니다. 실패 시 점증 백오프로 재시도합니다."""
    last_error = None
    for attempt in range(max_retries):
        try:
            resp = requests.post(endpoint, headers=_HEADERS, data=json.dumps(payload), timeout=timeout)
            resp.raise_for_status()
            resp.encoding = "utf-8"  # requests의 인코딩 오판정 방지
            parsed = _parse_sse(resp.text)
            if "error" in parsed:
                raise NtsClientError(f"서버 오류 응답: {parsed['error']}")
            return parsed
        except (requests.exceptions.RequestException, json.JSONDecodeError, NtsClientError) as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(backoff_base * (2 ** attempt))
    raise NtsClientError(f"{max_retries}회 재시도 후에도 실패했습니다: {last_error}")


def ping(endpoint: str = DEFAULT_ENDPOINT, timeout: float = 10.0) -> dict:
    """서버 연결 상태를 점검합니다 (initialize 핸드셰이크만 수행, 재시도 없이 즉시 실패 반환)."""
    result = _rpc_call(
        endpoint,
        {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "nts-tax-client", "version": "1.0"},
            },
        },
        timeout=timeout,
        max_retries=1,
    )
    server_info = result.get("result", {}).get("serverInfo", {})
    return {
        "ok": True,
        "server_name": server_info.get("name"),
        "server_version": server_info.get("version"),
    }


def list_tools(endpoint: str = DEFAULT_ENDPOINT, timeout: float = 10.0) -> list:
    """서버에 등록된 도구 이름 목록을 반환합니다."""
    result = _rpc_call(endpoint, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, timeout=timeout)
    tools = result.get("result", {}).get("tools", [])
    return [t["name"] for t in tools]


def _call_tool(endpoint: str, name: str, arguments: dict, timeout: float) -> dict:
    result = _rpc_call(
        endpoint,
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        timeout=timeout,
    )
    try:
        text = result["result"]["content"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        raise NtsClientError(f"예상치 못한 응답 구조: {e} / {result}")
    return json.loads(text)


def nts_ruling_search(
    keyword: str,
    collections: Optional[list] = None,
    page: int = 1,
    view_count: int = 20,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort: str = "relevance",
    tax_type_filter: Optional[str] = None,
    include_full_text: bool = True,
    endpoint: str = DEFAULT_ENDPOINT,
    timeout: float = 20.0,
) -> dict:
    """국세법령정보시스템 통합검색 (서버의 nts_ruling_search 도구 호출)"""
    if collections:
        invalid = set(collections) - VALID_COLLECTIONS
        if invalid:
            raise NtsClientError(f"알 수 없는 collections 값: {invalid} (허용값: {sorted(VALID_COLLECTIONS)})")

    arguments = {
        "keyword": keyword,
        "collections": collections,
        "page": page,
        "view_count": view_count,
        "date_from": date_from,
        "date_to": date_to,
        "sort": sort,
        "tax_type_filter": tax_type_filter,
        "include_full_text": include_full_text,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return _call_tool(endpoint, "nts_ruling_search", arguments, timeout)


def nts_ruling_get_by_doc_no(
    doc_no: str,
    endpoint: str = DEFAULT_ENDPOINT,
    timeout: float = 20.0,
) -> dict:
    """사건번호로 직접 조회 (서버의 nts_ruling_get_by_doc_no 도구 호출)"""
    return _call_tool(endpoint, "nts_ruling_get_by_doc_no", {"doc_no": doc_no}, timeout)
