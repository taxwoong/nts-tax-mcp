# -*- coding: utf-8 -*-
"""
hwp_text.py — HWP 5.0(한글) 파일에서 본문 텍스트를 추출한다.

국세법령정보시스템의 심판례·판례·질의회신은 검색 API가 본문을 주지 않고
("결정내용은 붙임과 같습니다."), 결정 이유와 사실관계는 전부 붙임 HWP에 들어 있다.
그 붙임을 읽기 위한 모듈이다. (2026-09-10 확인: 붙임은 전부 HWP 5.0 형식)

의존성은 olefile(순수 파이썬) 하나뿐이다. pyhwp 등 기성 라이브러리는 의존성이
무겁고 관리가 뜸해 쓰지 않는다 — HWP 5.0 포맷은 고정되어 있고, 필요한 것은
BodyText 레코드에서 문단 텍스트만 뽑는 것이라 아래 60여 줄로 충분하다.

포맷 요약 (HWP 5.0 = OLE 복합문서):
    FileHeader          : 37번째 바이트 bit0 = 본문 압축 여부
    BodyText/Section0..N: 레코드 스트림 (압축시 raw deflate, wbits=-15)
    레코드 헤더 4바이트 : tag(10bit) | level(10bit) | size(12bit)
                          size가 0xFFF이면 뒤 4바이트가 실제 크기
    HWPTAG_PARA_TEXT(67): 문단 텍스트 (UTF-16LE + 제어문자)
"""
import struct
import zlib

import olefile

HWPTAG_BEGIN = 0x10
HWPTAG_PARA_TEXT = HWPTAG_BEGIN + 51  # 67

# PARA_TEXT 안의 제어문자 분류 (HWP 5.0 스펙)
#   char control     : 1워드(2바이트)로 끝남
#   inline/extended  : 8워드(16바이트) — 코드워드 + 6워드 + 코드워드 반복
_INLINE_CTRL = {4, 5, 6, 7, 8, 9, 19, 20}
_EXTENDED_CTRL = {1, 2, 3, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23}
_LINE_BREAK = {10, 13}  # 줄 나눔 / 문단 나눔


class HwpExtractError(Exception):
    """HWP 파싱 실패 — 파일이 HWP 5.0이 아니거나 구조가 손상된 경우"""


def _iter_records(buf: bytes):
    """레코드 스트림을 (tag, payload)로 훑는다."""
    i, n = 0, len(buf)
    while i + 4 <= n:
        header = struct.unpack_from("<I", buf, i)[0]
        tag = header & 0x3FF
        size = (header >> 20) & 0xFFF
        i += 4
        if size == 0xFFF:  # 확장 크기: 뒤 4바이트가 실제 크기
            if i + 4 > n:
                break
            size = struct.unpack_from("<I", buf, i)[0]
            i += 4
        yield tag, buf[i:i + size]
        i += size


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
        표 안의 텍스트도 같은 문단 레코드에 들어 있어 함께 추출되지만,
        표의 행·열 구조는 복원되지 않고 셀 텍스트가 순서대로 이어진다.

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
            paras = [
                _para_text(payload)
                for tag, payload in _iter_records(raw)
                if tag == HWPTAG_PARA_TEXT
            ]
            parts.append("\n".join(paras))
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
