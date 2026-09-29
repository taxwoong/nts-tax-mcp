# -*- coding: utf-8 -*-
"""
hwp_text.py — HWP 5.0(한글) 파일에서 본문 텍스트를 추출한다.

국세법령정보시스템의 심판례·판례·질의회신은 검색 API가 본문을 주지 않고
("결정내용은 붙임과 같습니다."), 결정 이유와 사실관계는 전부 붙임 HWP에 들어 있다.
그 붙임을 읽기 위한 모듈이다. (2026-09-30 표본 197건: 전부 HWP 5.0, 배포용·암호 0건)

의존성은 olefile(순수 파이썬) 하나뿐이다. pyhwp 등 기성 라이브러리는 의존성이
무겁고 관리가 뜸해 쓰지 않는다 — HWP 5.0 포맷은 고정되어 있고, 필요한 것은
BodyText 레코드에서 문단 텍스트와 표 구조만 뽑는 것이라 직접 읽는다.

포맷 요약 (HWP 5.0 = OLE 복합문서):
    FileHeader          : 37번째 바이트 bit0 = 본문 압축 여부
    BodyText/Section0..N: 레코드 스트림 (압축시 raw deflate, wbits=-15)
    레코드 헤더 4바이트 : tag(10bit) | level(10bit) | size(12bit)
                          size가 0xFFF이면 뒤 4바이트가 실제 크기
    HWPTAG_PARA_TEXT(67): 문단 텍스트 (UTF-16LE + 제어문자)

표 (2026-09-30 추가):
    CTRL_HEADER('tbl ') 아래에 TABLE(행·열 수)이 오고, 셀마다 LIST_HEADER(열·행·
    병합 폭) 뒤에 그 셀의 문단들이 같은 level로 이어진다. 심사 결정문의 세액 비교표처럼
    숫자가 많은 표는 칸을 한 줄씩 풀어 쓰면 어느 숫자가 어느 열인지 알 수 없어서,
    2행·2열 이상인 표는 Markdown 표로 복원한다. 본문 전체를 감싼 1칸짜리 틀이나
    한 줄짜리 표는 표가 아니라 글이므로 종전처럼 문단으로 둔다.
    (표본 197건 기준: 데이터 표가 있는 문서 16%, 심사 결정문은 31%)
"""
import struct
import zlib

import olefile

HWPTAG_BEGIN = 0x10
HWPTAG_PARA_TEXT = HWPTAG_BEGIN + 51  # 67
HWPTAG_CTRL_HEADER = HWPTAG_BEGIN + 55  # 71
HWPTAG_LIST_HEADER = HWPTAG_BEGIN + 56  # 72
HWPTAG_TABLE = HWPTAG_BEGIN + 61  # 77
_CTRL_TABLE = b" lbt"  # 컨트롤 ID 'tbl ' — UINT32 LE로 저장돼 바이트가 뒤집혀 있다

_MAX_TABLE_CELLS = 10000  # 행·열 수가 깨진 표에 거대한 격자를 만들지 않도록
_SPAN_REPEAT_MAX = 20  # 병합 칸 글자를 펼친 칸마다 반복할 최대 길이 (본문 행)

# PARA_TEXT 안의 제어문자 분류 (HWP 5.0 스펙)
#   char control     : 1워드(2바이트)로 끝남
#   inline/extended  : 8워드(16바이트) — 코드워드 + 6워드 + 코드워드 반복
_INLINE_CTRL = {4, 5, 6, 7, 8, 9, 19, 20}
_EXTENDED_CTRL = {1, 2, 3, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23}
_LINE_BREAK = {10, 13}  # 줄 나눔 / 문단 나눔


class HwpExtractError(Exception):
    """HWP 파싱 실패 — 파일이 HWP 5.0이 아니거나 구조가 손상된 경우"""


def _iter_records_lv(buf: bytes):
    """레코드 스트림을 (tag, level, payload)로 훑는다."""
    i, n = 0, len(buf)
    while i + 4 <= n:
        header = struct.unpack_from("<I", buf, i)[0]
        tag = header & 0x3FF
        level = (header >> 10) & 0x3FF
        size = (header >> 20) & 0xFFF
        i += 4
        if size == 0xFFF:  # 확장 크기: 뒤 4바이트가 실제 크기
            if i + 4 > n:
                break
            size = struct.unpack_from("<I", buf, i)[0]
            i += 4
        yield tag, level, buf[i:i + size]
        i += size


def _iter_records(buf: bytes):
    """레코드 스트림을 (tag, payload)로 훑는다."""
    for tag, _level, payload in _iter_records_lv(buf):
        yield tag, payload


def _record_tree(buf: bytes) -> list:
    """레코드를 level 기준 트리로 묶는다. 노드 = (tag, payload, children)"""
    root, stack = [], []  # stack: (level, 그 노드의 children)
    for tag, level, payload in _iter_records_lv(buf):
        while stack and stack[-1][0] >= level:
            stack.pop()
        node = (tag, payload, [])
        (stack[-1][1] if stack else root).append(node)
        stack.append((level, node[2]))
    return root


def _render(nodes: list, out: list, flat: bool = False):
    """
    노드를 순서대로 글로 옮겨 out에 줄 단위로 쌓는다.
    문단은 한 줄, 표는 Markdown 표. flat이면 표도 글로 푼다(표 칸 안의 내용용).
    """
    for tag, payload, children in nodes:
        if tag == HWPTAG_PARA_TEXT:
            out.append(_para_text(payload))
        elif tag == HWPTAG_CTRL_HEADER and payload[:4] == _CTRL_TABLE and not flat:
            try:
                out.extend(_table_lines(children))
            except (struct.error, IndexError, ValueError):
                _render(children, out, flat=True)  # 구조가 이상한 표는 종전처럼 글로
        else:
            _render(children, out, flat)


def _cell_text(nodes: list) -> str:
    """셀 내용을 한 줄로 — 칸 안의 줄바꿈 때문에 한 칸이 여러 줄로 쪼개지지 않게."""
    lines = []
    _render(nodes, lines, flat=True)
    return " ".join(" ".join(lines).split()).replace("|", "\\|")


def _parse_table(children: list):
    """표 컨트롤(CTRL_HEADER 'tbl ')의 자식 노드 → (행 수, 열 수, 캡션 노드, 셀 목록)
    셀 = [col, row, colspan, rowspan, 문단 노드들]"""
    rows = cols = 0
    caption, cells = [], []
    seen_table = False
    for node in children:
        tag, payload = node[0], node[1]
        if tag == HWPTAG_TABLE:
            rows, cols = struct.unpack_from("<HH", payload, 4)
            seen_table = True
        elif tag == HWPTAG_LIST_HEADER and seen_table:
            col, row, colspan, rowspan = struct.unpack_from("<HHHH", payload, 8)
            cells.append([col, row, max(colspan, 1), max(rowspan, 1), []])
        elif cells:
            cells[-1][4].append(node)
        else:
            caption.append(node)  # 표 앞의 캡션 문단
    return rows, cols, caption, cells


def _compact(cells: list, n: int, pos: int, span: int) -> int:
    """
    어떤 칸도 가르지 않는 격자선을 지운다. 서식처럼 가는 격자(38열 등)에 칸을
    병합해 배치한 표를 실제로 갈리는 열·행만 남겨 줄인다. 셀 좌표를 고쳐 쓰고 새 크기를 돌려준다.
    """
    edges = {0, n}
    for c in cells:
        edges.add(min(c[pos], n))
        edges.add(min(c[pos] + c[span], n))
    index = {e: i for i, e in enumerate(sorted(edges))}
    for c in cells:
        start, end = index[min(c[pos], n)], index[min(c[pos] + c[span], n)]
        c[pos], c[span] = start, max(end - start, 1)
    return len(edges) - 1


def _is_data_table(cells: list, rows: int, cols: int) -> bool:
    """
    행 절반 이상이 모든 열로 갈려 있어야 데이터 표로 본다. 서식형 표('[제 목] | 내용'
    줄마다 칸 배치가 다른 것)는 Markdown으로 옮기면 빈 칸과 반복만 늘어나므로 글로 둔다.
    """
    owner = [[None] * cols for _ in range(rows)]
    for i, (col, row, colspan, rowspan, _nodes) in enumerate(cells):
        for r in range(row, min(row + rowspan, rows)):
            for c in range(col, min(col + colspan, cols)):
                owner[r][c] = i
    full = sum(1 for line in owner if None not in line and len(set(line)) == cols)
    return full * 2 >= rows


def _table_lines(children: list) -> list:
    """표 컨트롤(CTRL_HEADER 'tbl ')의 자식 노드를 줄 목록으로."""
    rows, cols, caption, cells = _parse_table(children)

    out = []
    _render(caption, out)
    if rows * cols <= _MAX_TABLE_CELLS:
        cols = _compact(cells, cols, 0, 2)
        rows = _compact(cells, rows, 1, 3)
    if (rows < 2 or cols < 2 or rows * cols > _MAX_TABLE_CELLS
            or not _is_data_table(cells, rows, cols)):
        # 1칸짜리 틀·한 줄짜리 표·서식형 표는 글로 둔다 — 안에 든 데이터 표는 다시 표로
        for cell in cells:
            _render(cell[4], out)
        return out

    # 첫 행에서 세로로 병합된 칸이 있으면 그 깊이까지가 머리행 (예: '구분'이 2행 병합)
    head = max((c[3] for c in cells if c[1] == 0), default=1)
    if head >= rows:
        head = 1

    grid = [[""] * cols for _ in range(rows)]
    overflow = []  # 표 범위를 벗어난 칸 — 버리지 않고 표 뒤에 글로 남긴다
    for col, row, colspan, rowspan, nodes in cells:
        text = _cell_text(nodes)
        if row >= rows or col >= cols:
            if text:
                overflow.append(text)
            continue
        for r in range(row, min(row + rowspan, rows)):
            for c in range(col, min(col + colspan, cols)):
                # 병합 칸: 머리행은 늘 반복(열 제목), 본문은 짧은 글만 반복
                if (r, c) == (row, col) or r < head or len(text) <= _SPAN_REPEAT_MAX:
                    grid[r][c] = text

    header = []
    for c in range(cols):  # 여러 줄 머리행은 위에서부터 이어 붙인다 ('금액 당초')
        parts = []
        for r in range(head):
            if grid[r][c] and (not parts or parts[-1] != grid[r][c]):
                parts.append(grid[r][c])
        header.append(" ".join(parts))

    def fmt(cells_in_row):
        return "| " + " | ".join(cells_in_row) + " |"

    out += ["", fmt(header), "|" + "---|" * cols]
    out += [fmt(grid[r]) for r in range(head, rows)]
    out += [""] + overflow
    return out


def _body_text(buf: bytes) -> str:
    """BodyText 섹션 하나(압축 해제된 레코드 스트림)를 글로."""
    lines = []
    _render(_record_tree(buf), lines)
    return "\n".join(lines)


def _para_text(data: bytes) -> str:
    """PARA_TEXT 레코드 하나를 문자열로. 제어문자는 폭만큼 건너뛴다."""
    out = []
    i, n = 0, len(data)
    while i + 2 <= n:
        code = struct.unpack_from("<H", data, i)[0]
        i += 2
        if code in _INLINE_CTRL or code in _EXTENDED_CTRL:
            i += 14  # 코드워드 포함 총 16바이트
            if code == 9:
                out.append("\t")
        elif code in _LINE_BREAK:
            out.append("\n")
        elif code < 32:
            pass  # 나머지 char control은 버린다
        else:
            out.append(chr(code))
    return "".join(out)


def extract_text(path_or_bytes) -> str:
    """
    HWP 5.0 파일에서 본문 텍스트를 뽑는다.

    Args:
        path_or_bytes: 파일 경로(str) 또는 파일 내용(bytes)

    Returns:
        문단을 줄바꿈으로 이은 본문 텍스트.
        2행·2열 이상인 표는 Markdown 표로 복원한다(병합 칸은 펼쳐 채우고, 여러 줄
        머리행은 한 줄로 합친다). 1칸짜리 틀·한 줄짜리 표는 문단으로 둔다.

    Raises:
        HwpExtractError: OLE 복합문서가 아니거나 BodyText가 없는 경우
    """
    if isinstance(path_or_bytes, (bytes, bytearray)):
        data = bytes(path_or_bytes)
        if not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise HwpExtractError(
                f"HWP 5.0(OLE) 파일이 아닙니다 — 선두 바이트: {data[:8]!r} "
                f"(HWPX(zip)·PDF 등 다른 형식일 수 있음)"
            )
        src = __import__("io").BytesIO(data)
    else:
        src = path_or_bytes

    try:
        ole = olefile.OleFileIO(src)
    except Exception as e:  # olefile은 다양한 예외를 던진다
        raise HwpExtractError(f"OLE 복합문서 열기 실패: {type(e).__name__}: {e}") from e

    try:
        if not ole.exists("FileHeader"):
            raise HwpExtractError("FileHeader 스트림이 없습니다 — HWP 파일이 아닙니다")
        header = ole.openstream("FileHeader").read()
        compressed = bool(header[36] & 1)

        sections = sorted(
            s for s in ole.listdir() if len(s) == 2 and s[0] == "BodyText"
        )
        if not sections:
            raise HwpExtractError("BodyText 섹션이 없습니다 — 빈 문서이거나 형식이 다릅니다")

        parts = []
        for sec in sections:
            raw = ole.openstream(sec).read()
            if compressed:
                try:
                    raw = zlib.decompress(raw, -15)
                except zlib.error as e:
                    raise HwpExtractError(
                        f"{'/'.join(sec)} 압축 해제 실패: {e}"
                    ) from e
            parts.append(_body_text(raw))
    finally:
        ole.close()

    return _tidy("\n".join(parts))


def _tidy(text: str) -> str:
    """빈 줄 과다·행 끝 공백 정리. 문단 구분은 남긴다."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    out, blank = [], 0
    for ln in lines:
        if ln.strip():
            out.append(ln)
            blank = 0
        else:
            blank += 1
            if blank <= 1:  # 연속 빈 줄은 하나로
                out.append("")
    return "\n".join(out).strip()
