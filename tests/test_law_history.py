# -*- coding: utf-8 -*-
"""
test_law_history.py — law_history가 법령ID 없이 이름으로 찾을 때 시행령·시행규칙을 섞지 않는지 (오프라인)

2026-10-09 실측: '노인장기요양보험법' 제9조·제10조를 물으면 시행일이 더 늦은 시행령 조문이 나왔다.
law.go.kr 응답(_get)을 가짜 XML로 바꿔 끼워 확인한다.

실행:
    python tests/test_law_history.py
    python -m pytest tests/test_law_history.py -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import law_go_kr  # noqa: E402

# 법령ID·MST는 시험용 가짜 값
ROWS = [
    ("노인장기요양보험법", "900001", "800001", "20240809", "20240206", "20213"),
    ("노인장기요양보험법", "900001", "800002", "20250221", "20240220", "20323"),
    ("노인장기요양보험법 시행령", "900002", "800003", "20260512", "20260512", "36325"),
    ("노인장기요양보험법 시행규칙", "900003", "800004", "20260601", "20260530", "01111"),
]


def _xml(rows):
    return "<LawSearch>" + "".join(
        f"<law id='{i}'><법령명한글><![CDATA[{n}]]></법령명한글><법령ID>{lid}</법령ID>"
        f"<법령일련번호>{mst}</법령일련번호><시행일자>{eff}</시행일자><공포일자>{pub}</공포일자>"
        f"<공포번호>{no}</공포번호></law>"
        for i, (n, lid, mst, eff, pub, no) in enumerate(rows, 1)) + "</LawSearch>"


def _history(name, law_id=""):
    orig = law_go_kr._get

    def fake(endpoint, **params):
        return _xml(ROWS) if params.get("page", 1) == 1 else "<LawSearch></LawSearch>"

    law_go_kr._get = fake
    try:
        return law_go_kr.LawGoKrClient().law_history(name, law_id=law_id)
    finally:
        law_go_kr._get = orig


def test_exact_name_keeps_only_that_law():
    rows = _history("노인장기요양보험법")
    assert [r["MST"] for r in rows] == ["800001", "800002"]      # 시행령·시행규칙 제외, 시행일 순


def test_subordinate_name_selects_itself():
    assert [r["MST"] for r in _history("노인장기요양보험법 시행령")] == ["800003"]
    assert [r["MST"] for r in _history("노인장기요양보험법  시행규칙")] == ["800004"]   # 공백 차이 무시


def test_partial_name_and_explicit_id_unchanged():
    assert len(_history("장기요양")) == 4                          # 같은 이름이 없으면 그대로
    assert [r["MST"] for r in _history("장기요양", law_id="900002")] == ["800003"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
