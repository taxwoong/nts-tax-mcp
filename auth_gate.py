# -*- coding: utf-8 -*-
"""
auth_gate.py — 사무실 직원만 쓰게 하는 접근 토큰 게이트

왜 'URL 경로에 토큰'인가:
  claude.ai 커넥터는 요청이 사용자 PC가 아니라 Anthropic 서버에서 나간다. 그래서
  IP 허용목록이나 Tailscale ACL로는 직원과 외부인을 구분할 수 없다. 커스텀 헤더를
  넣지 못하는 클라이언트도 있어, URL 자체에 비밀을 담는 방식이 가장 호환성이 좋다.
  헤더(Authorization: Bearer)와 쿼리(?k=)도 함께 받는다.

사용 (서버컴퓨터에서):
  python auth_gate.py add 홍길동      직원 추가 + 그 사람 전용 URL 출력
  python auth_gate.py list            발급 현황 (토큰 뒷자리만 표시)
  python auth_gate.py revoke 홍길동   폐기 — 그 URL만 즉시 무효 (서버 재시작 필요)

토큰 저장: local_tokens.json (.gitignore 처리 — 저장소에 올라가지 않는다)

모드: 환경변수 NTS_AUTH_MODE
  off      게이트 없음(종전과 동일). 토큰 파일이 없으면 자동으로 off.
  warn     차단하지 않고 '토큰 없는 요청'만 로그에 남긴다 — 전환 기간용.
  enforce  토큰 없는 요청은 401. 토큰 파일이 있으면 기본값.
"""
import json
import logging
import os
import secrets
import sys
import time
from pathlib import Path

logger = logging.getLogger("nts-tax-mcp.gate")

TOKENS_PATH = Path(__file__).with_name("local_tokens.json")
_ALLOW_LOG_INTERVAL = 3600.0   # 정상 접속은 사람·IP 조합당 1시간에 한 번만 기록 (로그 비대 방지)
_DENY_LOG_INTERVAL = 300.0     # 차단은 IP당 5분에 한 번 (외부 스캐너가 로그를 채우는 것 방지)


def load_people() -> dict:
    """{이름: 토큰} — 파일이 없으면 빈 dict."""
    if not TOKENS_PATH.exists():
        return {}
    try:
        data = json.loads(TOKENS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.error("local_tokens.json 읽기 실패 — 게이트를 열어둔 채 기동한다: %s", e)
        return {}
    return {str(k): str(v) for k, v in data.items() if k and v}


def save_people(people: dict) -> None:
    TOKENS_PATH.write_text(
        json.dumps(people, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def default_mode(people: dict) -> str:
    mode = os.environ.get("NTS_AUTH_MODE", "").strip().lower()
    if mode in ("off", "warn", "enforce"):
        return mode
    return "enforce" if people else "off"


class TokenGate:
    """ASGI 미들웨어 — 토큰이 확인된 요청만 뒤쪽 MCP 앱으로 넘긴다."""

    def __init__(self, app, mcp_path: str = "/mcp", mode: str = ""):
        self.app = app
        self.mcp_path = mcp_path or "/mcp"
        self.people = load_people()
        self.by_token = {v: k for k, v in self.people.items()}
        self.mode = mode or default_mode(self.people)
        self._allow_log: dict = {}
        self._deny_log: dict = {}
        self.denied = 0

    # -- 요청에서 토큰 찾기 ------------------------------------------------
    def _extract(self, scope) -> tuple:
        """(토큰, 재작성된 경로) — 경로 접두사 /t/<토큰> 을 벗겨낸다."""
        path = scope.get("path", "") or ""
        if path.startswith("/t/"):
            rest = path[3:]
            token, _, tail = rest.partition("/")
            if token:
                return token, "/" + tail if tail else self.mcp_path

        for raw_name, raw_value in scope.get("headers", []):
            if raw_name.lower() == b"authorization":
                value = raw_value.decode("latin-1").strip()
                if value.lower().startswith("bearer "):
                    return value[7:].strip(), path

        qs = (scope.get("query_string", b"") or b"").decode("latin-1")
        for part in qs.split("&"):
            key, _, value = part.partition("=")
            if key == "k" and value:
                return value, path

        return "", path

    # -- 로그 (억제 포함) --------------------------------------------------
    def _log_allow(self, name: str, ip: str) -> None:
        key = (name, ip)
        now = time.time()
        if now - self._allow_log.get(key, 0.0) < _ALLOW_LOG_INTERVAL:
            return
        self._allow_log[key] = now
        logger.info("[GATE] 접속 %s (%s)", name, ip)

    def _log_deny(self, ip: str, path: str) -> None:
        self.denied += 1
        now = time.time()
        if now - self._deny_log.get(ip, 0.0) < _DENY_LOG_INTERVAL:
            return
        self._deny_log[ip] = now
        logger.warning("[GATE] 차단 %s → %s (누적 %d건)", ip, path, self.denied)

    async def _reply(self, send, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = [(b"content-type", b"application/json; charset=utf-8")]
        if status == 401:
            headers.append((b"www-authenticate", b'Bearer realm="nts-tax-mcp"'))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        # lifespan 등 http 이외 이벤트는 그대로 흘려보낸다
        if scope["type"] != "http" or self.mode == "off":
            await self.app(scope, receive, send)
            return

        client = scope.get("client") or ("?", 0)
        ip = client[0]
        path = scope.get("path", "") or ""

        # 토큰 없이 서버 생존만 확인하는 용도 (도구·자료는 노출하지 않는다)
        if path == "/healthz":
            await self._reply(send, 200, {"ok": True, "mode": self.mode})
            return

        token, new_path = self._extract(scope)
        name = self.by_token.get(token) if token else None

        if name is None:
            if self.mode == "warn":
                logger.warning("[GATE] (warn) 토큰 없는 요청 통과 %s → %s", ip, path)
                await self.app(scope, receive, send)
                return
            self._log_deny(ip, path)
            await self._reply(send, 401, {
                "status": "AUTH_ERROR",
                "오류": "접근 토큰이 없거나 유효하지 않습니다.",
                "_guidance": "사무실에서 발급받은 전용 주소로 접속하세요. 자료 부존재와 무관합니다.",
            })
            return

        self._log_allow(name, ip)
        if new_path != path:
            scope = dict(scope)
            scope["path"] = new_path
            scope["raw_path"] = new_path.encode("utf-8")
        await self.app(scope, receive, send)


def describe(gate: TokenGate) -> str:
    if gate.mode == "off":
        return "off — 주소만 알면 누구나 접속 가능 (토큰 미발급 상태)"
    return f"{gate.mode} — 발급 {len(gate.people)}명: {', '.join(gate.people) or '없음'}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _url_for(token: str) -> str:
    base = os.environ.get("NTS_PUBLIC_BASE", "").rstrip("/")
    if not base:
        base = "https://<서버주소>"   # 실제 주소는 저장소에 남기지 않는다
    return f"{base}/t/{token}/mcp"


def _cmd_add(name: str) -> int:
    people = load_people()
    if name in people:
        print(f"이미 발급된 사람입니다: {name}\n  {_url_for(people[name])}")
        return 0
    token = secrets.token_urlsafe(24)
    people[name] = token
    save_people(people)
    print(f"발급 완료 — {name}\n  {_url_for(token)}\n\n"
          "이 주소를 본인에게만 전달하고, 커넥터 URL을 이 주소로 바꾸게 하세요.\n"
          "서버를 재시작해야 적용됩니다.")
    return 0


def _cmd_list() -> int:
    people = load_people()
    if not people:
        print("발급된 토큰이 없습니다 (게이트 off 상태).")
        return 0
    print(f"발급 {len(people)}명  —  파일: {TOKENS_PATH.name}")
    for name, token in people.items():
        print(f"  {name:<12} …{token[-6:]}")
    return 0


def _cmd_revoke(name: str) -> int:
    people = load_people()
    if name not in people:
        print(f"그런 이름이 없습니다: {name}")
        return 1
    people.pop(name)
    save_people(people)
    print(f"폐기 완료 — {name}. 서버를 재시작하면 그 주소는 즉시 무효가 됩니다.")
    return 0


def main(argv) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "add" and len(argv) > 2:
        return _cmd_add(" ".join(argv[2:]))
    if cmd == "list":
        return _cmd_list()
    if cmd == "revoke" and len(argv) > 2:
        return _cmd_revoke(" ".join(argv[2:]))
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
