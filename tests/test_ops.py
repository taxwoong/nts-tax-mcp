# -*- coding: utf-8 -*-
"""
test_ops.py — 운영 보강(2026-11-23) 오프라인 테스트: 로그 파일 회전·형식, 없는 도구 이름 안내.

네트워크에 접속하지 않는다.

실행:
    python tests/test_ops.py
    python -m pytest tests/test_ops.py -q
"""
import asyncio
import logging
import logging.handlers
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import law_go_kr  # noqa: E402
import server_ext  # noqa: E402
from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402


def _flush_root():
    for h in logging.getLogger().handlers:
        h.flush()


def _reset_root():
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    root.addHandler(logging.StreamHandler())


def test_configure_logging_single_rotating_utf8_file_with_timestamps():
    with tempfile.TemporaryDirectory() as d:
        try:
            path = server_ext.configure_logging(Path(d), level="INFO")
            root = logging.getLogger()
            assert len(root.handlers) == 1
            h = root.handlers[0]
            assert isinstance(h, logging.handlers.TimedRotatingFileHandler)
            assert h.backupCount == server_ext.LOG_BACKUP_DAYS
            assert any(isinstance(f, law_go_kr.OcMaskFilter) for f in h.filters)
            # 국세 모듈 로그가 두 번 찍히지 않는다 (자기 핸들러 제거, 루트로만)
            nts = logging.getLogger("nts_tax_ruling_search")
            assert not nts.handlers and nts.propagate
            nts.warning("검색 요청 실패 — 재시도")
            _flush_root()
            text = path.read_text(encoding="utf-8")
            lines = [ln for ln in text.splitlines() if "재시도" in ln]
            assert len(lines) == 1, lines
            assert lines[0][:4].isdigit() and " WARNING nts_tax_ruling_search: " in lines[0]
        finally:
            _reset_root()


def test_configure_logging_masks_oc():
    with tempfile.TemporaryDirectory() as d:
        old = law_go_kr.OC
        try:
            law_go_kr.OC = "DUMMYOC123"
            path = server_ext.configure_logging(Path(d), level="INFO")
            logging.getLogger("x").error("url=http://www.law.go.kr/DRF/lawSearch.do?OC=DUMMYOC123&type=XML")
            _flush_root()
            text = path.read_text(encoding="utf-8")
            assert "DUMMYOC123" not in text and "OC=***" in text
        finally:
            law_go_kr.OC = old
            _reset_root()


KNOWN = ["olta_get_detail", "olta_ruling_search", "olta_collection_search",
         "nts_ruling_get_full_text", "nts_ruling_search", "law_article_as_of"]


def test_unknown_olta_detail_points_to_olta_get_detail():
    for name in ("olta_ruling_get_full_text", "olta_ruling_detail", "olta_ruling_get"):
        msg = server_ext.unknown_tool_message(name, KNOWN)
        assert msg.startswith(f"Unknown tool: {name}")
        assert "olta_get_detail(category, doc_id)" in msg
        # 비슷한 이름 후보는 같은 접두어(olta_) 안에서만 — 국세 전문 도구를 권하지 않는다
        similar = msg.split("비슷한 이름: ")[1].split(".")[0] if "비슷한 이름: " in msg else ""
        assert "nts_" not in similar, msg


def test_unknown_list_and_labor_names_get_guidance():
    assert "tools/list" in server_ext.unknown_tool_message("--list", KNOWN)
    assert "tools/list" in server_ext.unknown_tool_message("list", KNOWN)
    assert "다른 커넥터" in server_ext.unknown_tool_message("labor_resource", KNOWN)
    assert "다른 커넥터" in server_ext.unknown_tool_message("check_minimum_wage", KNOWN)


def test_hint_installed_on_live_tool_manager():
    server_ext.install_unknown_tool_hint()
    server_ext.install_unknown_tool_hint()  # 두 번 불러도 한 겹만
    tm = server_ext.mcp._tool_manager
    assert getattr(tm.call_tool, "_with_hint", False)
    try:
        asyncio.run(tm.call_tool("olta_ruling_get_full_text", {}))
    except ToolError as e:
        assert "olta_get_detail" in str(e) and "전체 도구:" in str(e)
    else:
        raise AssertionError("없는 도구인데 오류가 안 났다")
    # 있는 도구는 그대로 원래 경로로 (입력 오류로 실패하더라도 '없는 도구' 안내가 아니어야 함)
    try:
        asyncio.run(tm.call_tool("olta_get_detail", {}))
    except Exception as e:  # noqa: BLE001
        assert "이 서버에 없는 도구" not in str(e)


def test_result_status_and_size_reads_dict_and_content():
    from mcp.types import TextContent
    assert server_ext.result_status_and_size({"status": "NOT_FOUND", "x": 1})[0] == "NOT_FOUND"
    assert server_ext.result_status_and_size({"precedent": {}})[0] == "OK"  # status 키 없음 = OK
    blocks = [TextContent(type="text", text='{"status": "UPSTREAM_ERROR", "오류": "t"}')]
    st, size = server_ext.result_status_and_size((blocks, {"result": {}}))
    assert st == "UPSTREAM_ERROR" and size == len(blocks[0].text)
    assert server_ext.result_status_and_size([TextContent(type="text", text="plain")])[0] == "OK"
    assert server_ext.result_status_and_size(object()) == ("OK", 0)  # 이상한 값이어도 예외 없이


def test_every_call_writes_one_usage_line_without_arguments():
    server_ext.install_unknown_tool_hint()
    tm = server_ext.mcp._tool_manager
    seen = []

    class _H(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    h = _H()
    server_ext.usage_logger.addHandler(h)
    old_level = server_ext.usage_logger.level
    server_ext.usage_logger.setLevel(logging.INFO)
    try:
        r = asyncio.run(tm.call_tool("tax_tribunal_decision_search", {"keyword": "거래처비밀"}, convert_result=False))
        assert r["status"] == "INVALID_INPUT" or "decisions" in r or r.get("status")
        try:
            asyncio.run(tm.call_tool("olta ruling\nfake", {}))
        except ToolError:
            pass
    finally:
        server_ext.usage_logger.removeHandler(h)
        server_ext.usage_logger.setLevel(old_level)
    assert len(seen) == 2, seen
    assert seen[0].startswith("TOOL name=tax_tribunal_decision_search status=") and " ms=" in seen[0]
    assert "거래처비밀" not in seen[0]  # 검색어·인자는 남기지 않는다
    assert seen[1].startswith("TOOL name=") and "UNKNOWN_TOOL" in seen[1] and " " not in seen[1].split()[1][5:]


def test_usage_stats_counts_named_and_unnamed_calls():
    import usage_stats
    lines = [
        "2026-11-23 10:00:00,000 INFO mcp.server.lowlevel.server: Processing request of type CallToolRequest",
        "2026-11-23 10:00:01,000 INFO mcp.server.lowlevel.server: Processing request of type CallToolRequest",
        "2026-11-24 08:00:00,000 INFO mcp.server.lowlevel.server: Processing request of type CallToolRequest",
        "2026-11-24 09:00:00,000 INFO mcp.server.lowlevel.server: Processing request of type CallToolRequest",
        "2026-11-24 09:00:00,900 INFO nts-tax-mcp.usage: TOOL name=nts_ruling_search status=OK ms=800 chars=4000",
        "2026-11-24 09:01:00,000 INFO mcp.server.lowlevel.server: Processing request of type CallToolRequest",
        "2026-11-24 09:01:00,500 INFO nts-tax-mcp.usage: TOOL name=nts_ruling_search status=UPSTREAM_ERROR ms=200 chars=100",
    ]
    with tempfile.TemporaryDirectory() as d:
        Path(d, "server.log.2026-11-23").write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
        Path(d, "server.log").write_text("\n".join(lines[2:]) + "\n", encoding="utf-8")
        r = usage_stats.collect(d)
        assert r["총호출"] == 5 and r["이름_기록된_호출"] == 2 and r["이름_미기록_호출"] == 3
        assert r["날짜별"] == {"2026-11-23": 2, "2026-11-24": 3}
        t = r["도구별"][0]
        assert t["도구"] == "nts_ruling_search" and t["호출"] == 2 and t["평균_ms"] == 500
        assert t["결과"] == {"OK": 1, "UPSTREAM_ERROR": 1}
        assert usage_stats.collect(d, since="2026-11-24")["총호출"] == 3


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("ok", t.__name__)
