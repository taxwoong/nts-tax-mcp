# -*- coding: utf-8 -*-
"""
usage_stats.py — 도구 호출 집계 (logs/server.log*를 읽는다, 서버에 접속하지 않음)

v5.11.1부터 서버가 호출마다 남기는 한 줄
    2026-11-24 09:12:03,456 INFO nts-tax-mcp.usage: TOOL name=nts_ruling_search status=OK ms=812 chars=4508
을 모아 날짜별·도구별·결과(status)별로 센다. 그 전 로그(도구 이름 없이 'CallToolRequest'만 있는 줄)는
날짜별 호출 수만 셀 수 있어 '이름 미기록'으로 따로 보여 준다.

사용법:
    python usage_stats.py                 # 전체 기간
    python usage_stats.py --days 7        # 최근 7일
    python usage_stats.py --since 2026-11-01 --until 2026-11-30
    python usage_stats.py --json          # 기계가 읽을 JSON
"""
import argparse
import collections
import datetime as dt
import glob
import json
import os
import re
import sys

LOG_DIR = os.environ.get("NTS_LOG_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}")
_TOOL = re.compile(r"TOOL name=(\S+) status=(\S+) ms=(\d+) chars=(\d+)")


def _log_files(log_dir: str) -> list:
    # 회전된 파일(server.log.2026-11-23)과 현재 파일(server.log). 옛 통짜 로그(server_until_*)는 시각이 없어 제외
    files = sorted(glob.glob(os.path.join(log_dir, "server.log.*"))) + [os.path.join(log_dir, "server.log")]
    return [f for f in files if os.path.isfile(f)]


def collect(log_dir: str = LOG_DIR, since: str = "", until: str = "") -> dict:
    per_day = collections.Counter()          # 이름 기록된 호출
    per_day_unnamed = collections.Counter()  # 이름 기록 전 CallToolRequest
    per_tool = collections.Counter()
    per_status = collections.Counter()
    tool_status = collections.defaultdict(collections.Counter)
    ms_sum, chars_sum = collections.Counter(), collections.Counter()
    requests_per_day = collections.Counter()  # 'CallToolRequest' — 이름 기록 전후 모든 호출에 한 줄씩 있다
    for path in _log_files(log_dir):
        with open(path, "rb") as f:
            for raw in f:
                line = raw.decode("utf-8", "replace")
                m = _TS.match(line)
                if not m:
                    continue
                day = m.group(1)
                if (since and day < since) or (until and day > until):
                    continue
                if "CallToolRequest" in line:
                    requests_per_day[day] += 1
                    continue
                t = _TOOL.search(line)
                if not t:
                    continue
                name, status, ms, chars = t.group(1), t.group(2), int(t.group(3)), int(t.group(4))
                per_day[day] += 1
                per_tool[name] += 1
                per_status[status] += 1
                tool_status[name][status] += 1
                ms_sum[name] += ms
                chars_sum[name] += chars
    # 이름 기록이 시작된 날(재시작 전 오전 등)에는 두 종류가 섞인다 — 요청 줄 수에서 이름 기록 수를 뺀다
    for day, n in requests_per_day.items():
        per_day_unnamed[day] = max(0, n - per_day[day])
    tools = []
    for name, n in per_tool.most_common():
        tools.append({"도구": name, "호출": n, "평균_ms": round(ms_sum[name] / n), "평균_응답자수": round(chars_sum[name] / n),
                      "결과": dict(tool_status[name].most_common())})
    days = sorted(set(per_day) | set(per_day_unnamed))
    return {
        "기간": f"{days[0]} ~ {days[-1]}" if days else "",
        "총호출": sum(per_day.values()) + sum(per_day_unnamed.values()),
        "이름_기록된_호출": sum(per_day.values()),
        "이름_미기록_호출": sum(per_day_unnamed.values()),
        "날짜별": {d: per_day[d] + per_day_unnamed[d] for d in days},
        "결과별": dict(per_status.most_common()),
        "도구별": tools,
    }


def _print(r: dict):
    print(f"기간: {r['기간'] or '(기록 없음)'}")
    print(f"총 호출: {r['총호출']:,}회  (도구 이름 기록 {r['이름_기록된_호출']:,} / 이름 기록 전 {r['이름_미기록_호출']:,})")
    if r["날짜별"]:
        print("\n[날짜별]")
        for d, n in r["날짜별"].items():
            print(f"  {d}  {n:>6,}")
    if r["도구별"]:
        print("\n[도구별]  호출 · 평균 ms · 평균 응답 글자 · 결과")
        for t in r["도구별"]:
            res = ", ".join(f"{k} {v}" for k, v in t["결과"].items())
            print(f"  {t['도구']:<32} {t['호출']:>6,}  {t['평균_ms']:>6,}ms  {t['평균_응답자수']:>7,}자  {res}")
    if r["결과별"]:
        print("\n[결과별] " + ", ".join(f"{k} {v:,}" for k, v in r["결과별"].items()))
        bad = {k: v for k, v in r["결과별"].items() if k in ("UPSTREAM_ERROR", "PARSE_ERROR", "AUTH_ERROR", "EXCEPTION")}
        if bad:
            print("  ※ UPSTREAM_ERROR·PARSE_ERROR·AUTH_ERROR·EXCEPTION은 원천 사이트 장애나 파서 문제 신호입니다.")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # 서버컴퓨터 기본 콘솔(cp949)에서도 한글이 깨지지 않게
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="nts-tax-mcp 도구 호출 집계")
    ap.add_argument("--days", type=int, default=0, help="최근 N일만")
    ap.add_argument("--since", default="", help="시작일 YYYY-MM-DD")
    ap.add_argument("--until", default="", help="종료일 YYYY-MM-DD")
    ap.add_argument("--log-dir", default=LOG_DIR)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    since = a.since
    if a.days:
        since = (dt.date.today() - dt.timedelta(days=a.days - 1)).isoformat()
    r = collect(a.log_dir, since, a.until)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        _print(r)


if __name__ == "__main__":
    main()
