"""
nts_enforcement.py — 국세청 세목별 집행기준 로컬 보관소 (v5.9)

국세청 검색(statute 컬렉션)은 집행기준의 번호·제목만 주고 본문을 주지 않는다(2026-10-18 실측 — 44건 전부).
본문은 전자도서관(formerLibrary)의 세목별 집행기준 책자(PDF)에만 있다. 책자는 2년에 한 번꼴로 새 판이
나오므로(2014·2017·2018·2020·2022·2024) 가끔 최신판 목록을 확인해 바뀐 책만 받아 항목 단위로 쪼개 둔다.

책자 PDF 구조 (2024.10.31판 실측). 글꼴 이름은 책마다 다르다(법인세 책은 전부 'Haansoft Batang') —
그래서 글꼴 이름이 아니라 글자 크기·위치로 가른다:
    쪽 머리글        '8_ 양도소득세 집행기준'(짝수 쪽) / '제2장 1세대 1주택 비과세 _7'(홀수 쪽)
    절 제목          12pt          '주택과 그에 딸린 토지' — '집행기준' 표시 바로 윗줄
    '집행기준' 표시   12pt
    번호             11.5pt        '89-154-1' (법 조문-시행령 조문-일련번호, 0은 해당 시행령 조문 없음).
                                  글자 조각이 '88-152'·'의'·'3-1'로 갈라져 오기도 해서 번호는 추출 텍스트에서
                                  읽는다 (텍스트에서는 늘 한 줄을 차지한다)
    제목             12pt          번호와 같은 줄. '1세대의 정의'처럼 숫자로 시작하기도 한다
    본문             11.5pt 이하   (표·계산식·사례는 10.6pt 등)
여러 법을 묶은 책(개별소비세·인지세·주세·주류면허법 / 종합부동산세·증권거래세 / 국제조세)은 법마다 번호가
다시 시작하므로 홀수 쪽 머리글('✲ 인지세 _55', 'Ⅱ. 법인세법 _80', '증권거래세 집행기준 _41')로 법을 가른다.
짝수 쪽 머리글은 책 제목('2_ 개별소비세･인지세･주세･주류면허법 집행기준')이라 법 구분에 쓰면 안 된다.
책 끝의 신구조문 대비표·뒤표지는 항목이 아니므로 뺀다.

사용:
    python nts_enforcement.py refresh [--force]   # 최신판 확인 → 바뀐 책만 받아 다시 쪼갬
    python nts_enforcement.py status
    python nts_enforcement.py get 소득세법 89-154-1
"""

import argparse
import hashlib
import io
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nts_enforcement")

DATA_PATH = Path(os.environ.get("NTS_ENFORCEMENT_PATH",
                                Path(__file__).resolve().parent / "data" / "enforcement.json"))
KST = timezone(timedelta(hours=9))

# 번호: 법 조문-시행령 조문-일련번호. 조문은 '152의3'처럼 가지번호가 붙고, 일련번호도 '3의2'가 있으며
# 법 조문이 범위('75∼75의9-0-1' — 법인세법상 가산세 종류)인 것도 있다
_ART = r"\d{1,3}(?:의\d{1,2})?"
NO_RE = rf"{_ART}(?:∼{_ART})?-{_ART}-{_ART}"
_MARKER_RE = re.compile(rf"(?m)^[ \t]*집행기준[ \t]*\n[ \t]*({NO_RE})[ \t]*$")

# 국세청 검색 응답(NM)의 법령명 표기 — 14개 세목 (2026-10-18 실측 1,613건)
LAWS = ("국세기본법", "국세징수법", "소득세법", "법인세법", "상속세 및 증여세법", "부가가치세법",
        "조세특례제한법", "국제조세조정에 관한 법률", "종합부동산세법", "개별소비세법", "주세법",
        "주류 면허 등에 관한 법률", "인지세법", "증권거래세법")
# 책 제목·쪽 머리글·사용자 입력 → 법령명. 앞에서부터 처음 맞는 것 (긴 말을 먼저)
_ALIASES = (
    ("국제조세조정", "국제조세조정에 관한 법률"), ("국조법", "국제조세조정에 관한 법률"),
    ("주류면허", "주류 면허 등에 관한 법률"), ("주류 면허", "주류 면허 등에 관한 법률"),
    ("개별소비세", "개별소비세법"), ("개소세", "개별소비세법"),
    ("종합부동산세", "종합부동산세법"), ("종부세", "종합부동산세법"),
    ("증권거래세", "증권거래세법"), ("인지세", "인지세법"), ("주세", "주세법"),
    ("상속", "상속세 및 증여세법"), ("증여", "상속세 및 증여세법"), ("상증", "상속세 및 증여세법"),
    ("조세특례", "조세특례제한법"), ("조특", "조세특례제한법"),
    ("부가가치세", "부가가치세법"), ("부가세", "부가가치세법"),
    ("국세기본", "국세기본법"), ("국기법", "국세기본법"),
    ("국세징수", "국세징수법"), ("국징법", "국세징수법"),
    ("법인세", "법인세법"), ("소득세", "소득세법"),
)
# 책 제목에 이 글자가 있으면 여러 법을 묶은 책 — 홀수 쪽 머리글로 법을 가른다
_MULTI_LAW_HINTS = ("･", "·", ",", "국제조세")
_EXCLUDED_LABEL_HINTS = ("신구조문", "대비표")
_BACK_COVER = "이 책에 실린 내용을 실무에 적용할 때는"


def normalize_law(name: str) -> Optional[str]:
    """법령명·약칭·세목명 → 국세청 표기 법령명. 모르면 None. '소득세법 시행령'도 소득세법으로."""
    if not name:
        return None
    s = re.sub(r"\s+", "", name)
    for law in LAWS:
        if s.startswith(re.sub(r"\s+", "", law)):
            return law
    for key, law in _ALIASES:
        if re.sub(r"\s+", "", key) in s:
            return law
    return None


def normalize_no(number: str) -> str:
    """'89 - 154 - 1', '제89조-154-1' → '89-154-1'. 앞부분만('89-154', '89')도 그대로 둔다."""
    s = re.sub(r"\s+|제|조(?!의)", "", number or "")
    s = s.replace("‐", "-").replace("–", "-").replace("−", "-").replace("~", "∼").replace("〜", "∼")
    return s.strip("-")


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


# ---------------------------------------------------------------------------
# PDF → 쪽 기록 (pypdf에 기대는 부분은 여기뿐 — 아래 split_items는 순수 함수라 오프라인 테스트가 된다)
# ---------------------------------------------------------------------------

def read_pages(pdf_bytes: bytes) -> list:
    """쪽마다 {'text': extract_text 결과, 'chunks': [(글자, y, 글자크기)]}. y·크기는 pt(쪽 아래가 y=0)."""
    from pypdf import PdfReader  # 지연 import — 검색·조회만 하는 서버 경로에는 필요 없다

    pages = []
    for page in PdfReader(io.BytesIO(pdf_bytes)).pages:
        chunks = []

        def visit(text, cm, tm, font, size, _chunks=chunks):
            if text and text.strip():
                # 실제 위치·크기 = tm × cm. tm만 보면 cm으로 옮긴 글자의 y가 0으로 나온다
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                scale = abs(tm[0] * cm[0] + tm[1] * cm[2])
                _chunks.append((text, round(y, 1), round(size * scale, 1)))

        text = page.extract_text(visitor_text=visit) or ""
        pages.append({"text": text, "chunks": chunks})
    return pages


_SAME_LINE = 2.5    # pt — 같은 줄로 보는 y 차이 ('집행기준' 729.5 · 번호 729.0 · 제목 729.6)


def _page_markers(chunks: list, numbers: list) -> list:
    """한 쪽의 글자 조각에서 번호별 (제목, 절 제목)을 찾는다. numbers는 그 쪽 텍스트에서 읽은 번호 순서.
    '집행기준' 조각과 같은 줄의 나머지 = 번호 + 제목. 바로 윗줄들 중 '집행기준'만큼 큰 글자 = 절 제목."""
    found, pending = {}, list(numbers)
    for i, (text, y, size) in enumerate(chunks):
        if text.strip() != "집행기준" or not pending or i + 1 >= len(chunks):
            continue
        # 본문 속 인용('…(집행기준 15-6-7)의 합계액')은 거른다 — 진짜 표시는 줄 첫머리에 있고
        # 번호보다 글자가 크다(12pt vs 11.5pt). 인용은 본문 글자 크기 그대로다
        if i > 0 and abs(chunks[i - 1][1] - y) <= _SAME_LINE:
            continue    # 줄 첫머리가 아니다
        if size <= chunks[i + 1][2] + 0.2:
            continue    # 번호와 글자 크기가 같다 = 본문 속 인용
        # '집행기준' 뒤로 이어지는 같은 줄 조각 = 번호 + 제목
        line, k = "", i + 1
        while k < len(chunks) and abs(chunks[k][1] - y) <= _SAME_LINE:
            line += chunks[k][0]
            k += 1
        no = next((n for n in pending if _strip_prefix(line, n) is not None), None)
        if no is None:
            continue
        pending.remove(no)
        # 긴 제목은 다음 줄로 넘어간다 — 본문(11.5pt 이하)보다 큰 글자 줄이 이어지면 제목의 계속.
        # 줄바꿈 자리는 낱말 사이라 한 칸 띄운다 ('…지급명세서' + '제출여부')
        prev_y = y
        while k < len(chunks) and chunks[k][2] >= size - 0.2 and chunks[k][1] < y - _SAME_LINE:
            if abs(chunks[k][1] - prev_y) > _SAME_LINE and line and not line[-1].isspace():
                line += " "
            line += chunks[k][0]
            prev_y = chunks[k][1]
            k += 1
        title = re.sub(r"\s+", " ", _strip_prefix(line, no)).strip()
        head, j = [], i - 1
        while j >= 0 and chunks[j][2] >= size - 0.2 and chunks[j][1] > y + _SAME_LINE \
                and chunks[j][0].strip() != "집행기준":
            head.insert(0, chunks[j][0])
            j -= 1
        found[no] = (title, re.sub(r"\s+", " ", "".join(head)).strip() or None)
    return found


def _label(text: str):
    """쪽 첫 줄(머리글) → ('odd'|'even'|None, 이름). '✲ 인지세 _55' → ('odd', '인지세'),
    'Ⅱ. 법인세법 _80' → ('odd', '법인세법'), '2_ 책 제목' → ('even', '책 제목')."""
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if re.search(r"_\s*\d+\s*$", first):
        kind, s = "odd", re.sub(r"\s*_\s*\d+\s*$", "", first)
    elif re.match(r"^\d+\s*_", first):
        kind, s = "even", re.sub(r"^\d+\s*_\s*", "", first)
    else:
        kind, s = None, first
    return kind, _section_name(s)


def _section_name(s: str) -> str:
    """'✲ 인지세' → '인지세', 'Ⅱ. 법인세법' → '법인세법'."""
    return re.sub(r"^[✲*\s]+|^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.\s*", "", s).strip()


def _drop_header(text: str) -> str:
    """쪽 첫 줄이 머리글('8_ 양도소득세 집행기준', '제2장 … _7')이면 뗀다."""
    lines = text.split("\n")
    for idx, ln in enumerate(lines):
        if ln.strip():
            if re.search(r"_\s*\d+\s*$", ln) or re.match(r"^\s*\d+\s*_\s", ln):
                return "\n".join(lines[:idx] + lines[idx + 1:])
            break
    return text


def _strip_prefix(body: str, prefix: str) -> Optional[str]:
    """공백을 무시하고 body가 prefix로 시작하면 그 뒤를, 아니면 None."""
    want = _squash(prefix)
    if not want:
        return body
    got, pos = 0, 0
    while pos < len(body) and got < len(want):
        ch = body[pos]
        if not ch.isspace():
            if ch != want[got]:
                return None
            got += 1
        pos += 1
    return body[pos:] if got == len(want) else None


def _strip_suffix(body: str, suffix: str) -> str:
    """공백을 무시하고 body가 suffix로 끝나면 떼어 낸다."""
    rev = _strip_prefix(body[::-1], suffix[::-1])
    return rev[::-1] if rev is not None else body


def _clean_body(s: str) -> str:
    s = s.replace(" ", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def split_items(pages: list, book_title: str) -> list:
    """쪽 기록 → 항목 [{law, no, title, heading, text, page}]. page는 PDF 쪽 번호(1부터)."""
    # 전자도서관 제목만 보면 안 된다 — 종합부동산세·증권거래세 합본이 '종합부동산세 집행기준'과
    # '증권거래세 집행기준' 두 이름으로 따로 올라 있다(같은 파일). 표지 첫 줄도 본다
    cover = next((ln.strip() for pg in pages for ln in pg["text"].splitlines() if ln.strip()), "")
    multi = any(h in book_title or h in cover for h in _MULTI_LAW_HINTS)
    law = normalize_law(book_title)
    if multi and book_title.startswith("국제조세"):
        law = "국제조세조정에 관한 법률"
    segments = []      # [(law, [(page_no, text)])] — 법이 바뀌거나 대비표가 시작되면 끊는다
    meta = {}          # (law, no) → (title, heading)
    skipping = False
    for page_no, pg in enumerate(pages, 1):
        text = pg["text"]
        kind, label = _label(text)
        if _BACK_COVER in text or any(h in label for h in _EXCLUDED_LABEL_HINTS):
            skipping = True
            continue
        body = _drop_header(text)
        if multi:
            # 법 구분: 부 첫 쪽의 부 제목 줄('✲증권거래세' — 짝수 쪽에서 시작하기도 한다)이나 홀수 쪽 머리글.
            # 짝수 쪽 머리글은 책 제목이라 여러 법 이름이 다 들어 있어 쓰면 안 된다
            names = [ln.strip() for ln in body.splitlines()[:4]
                     if re.match(r"^\s*(?:✲|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.)", ln) and len(ln.strip()) <= 30]
            if kind == "odd" and len(label) <= 30 and not re.match(r"^제\s*\d+\s*[편장절]", label):
                names.append(label)
            switched = next((sw for sw in (normalize_law(_section_name(n)) for n in names) if sw), None)
            if switched and (switched != law or skipping):
                law, skipping = switched, False
                segments.append((law, []))
        if skipping:
            continue
        if not segments or segments[-1][0] != law:
            segments.append((law, []))
        segments[-1][1].append((page_no, body))
        numbers = [m.group(1) for m in _MARKER_RE.finditer(body)]
        for no, (title, heading) in _page_markers(pg["chunks"], numbers).items():
            meta.setdefault((law, no), (title, heading))

    items = []
    for seg_law, seg_pages in segments:
        if not seg_pages:
            continue
        joined, starts = "", []
        for page_no, text in seg_pages:
            starts.append((len(joined), page_no))
            joined += text + "\n"
        marks = list(_MARKER_RE.finditer(joined))
        for n, m in enumerate(marks):
            no = m.group(1)
            title, heading = meta.get((seg_law, no), ("", None))
            end = marks[n + 1].start() if n + 1 < len(marks) else len(joined)
            body = joined[m.end():end]
            rest = _strip_prefix(body.lstrip(), title) if title else None
            if rest is not None:
                body = rest
            if n + 1 < len(marks):
                nxt = meta.get((seg_law, marks[n + 1].group(1)), ("", None))[1]
                if nxt:
                    body = _strip_suffix(body.rstrip(), nxt)
            page = max(p for off, p in starts if off <= m.start())
            items.append({"law": seg_law, "no": no, "title": title, "heading": heading,
                          "text": _clean_body(body), "page": page})
    return items


def parse_book(pdf_bytes: bytes, book_title: str) -> list:
    return split_items(read_pages(pdf_bytes), book_title)


# ---------------------------------------------------------------------------
# 최신판 확인 · 내려받기 · 저장
# ---------------------------------------------------------------------------

def list_latest_books(client) -> list:
    """전자도서관에서 세목별 집행기준 책자의 최신판 [{key, title, edition, file_id}].
    2026-10-18 실측: '집행기준' 검색 147건 = 12종 × 여러 판(2014~2024). 판(PLCN_DT)이 가장 늦은 것만 고른다."""
    from nts_tax_ruling_search import build_search_params, _plain

    latest, start = {}, 1
    while True:
        raw = client._post_search(build_search_params("집행기준", ["formerLibrary"], start_count=start,
                                                      view_count=100, sort="date_desc"))
        colls = raw["data"]["ASEISA001MR01"]["searchResultVO"]["collectionList"]
        coll = colls[0] if colls else {}
        rows = coll.get("resultList", [])
        for it in rows:
            title = _plain(it.get("NTST_PLCN_BK_TTL")) or ""
            if "집행기준" not in title or not it.get("NTST_FLE_ID"):
                continue
            key = _squash(title)
            edition = it.get("PLCN_DT") or ""
            if key not in latest or edition > latest[key]["edition"]:
                latest[key] = {"key": key, "title": title, "edition": edition, "file_id": it["NTST_FLE_ID"]}
        start += len(rows)
        if not rows or start > int(coll.get("totalCount") or 0) or start > 1000:
            break
    return sorted(latest.values(), key=lambda b: b["key"])


def download_book(client, file_id: str) -> bytes:
    metas = client.get_attachment_meta(file_id)
    pdfs = [m for m in metas if (m.get("fleXsnNm") or "").lower() == "pdf" and m.get("fleDwldUri")]
    if not pdfs:
        raise ValueError(f"집행기준 책자에 PDF 붙임이 없습니다 (file_id={file_id}, "
                         f"형식={[m.get('fleXsnNm') for m in metas]})")
    return client.download_attachment(pdfs[0]["fleDwldUri"])


def _now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def load_data(path: Path = DATA_PATH) -> Optional[dict]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def _save(data: dict, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)   # 읽는 쪽이 반쯤 쓴 파일을 보지 않도록


def needs_refresh(days: float, path: Path = DATA_PATH) -> bool:
    data = load_data(path)
    if not data or not data.get("checked_at"):
        return True
    try:
        checked = datetime.fromisoformat(data["checked_at"])
    except ValueError:
        return True
    return datetime.now(KST) - checked > timedelta(days=days)


def refresh(client, path: Path = DATA_PATH, force: bool = False, parse=parse_book) -> dict:
    """최신판 목록을 확인해 판이 바뀐 책만 받아 다시 쪼갠다. 결과 요약을 돌려준다.
    목록이 비거나 새로 쪼갠 항목이 전보다 절반 넘게 줄면(사이트·책자 형식이 바뀐 신호) 그 책은 옛 것을 둔다."""
    books = list_latest_books(client)
    if not books:
        raise ValueError("전자도서관 집행기준 책자 목록이 비었습니다 — 검색 응답이 바뀐 것일 수 있어 "
                         "기존 자료를 그대로 둡니다")
    old = load_data(path) or {"books": [], "items": []}
    old_books = {b["key"]: b for b in old.get("books", [])}
    old_items = {}
    for it in old.get("items", []):
        old_items.setdefault(it["file_id"], []).append(it)

    report = {"checked_at": _now(), "unchanged": [], "updated": [], "kept_old": [], "failed": []}
    new_books, new_items = [], []

    def keep(prev):
        new_books.append(prev)
        new_items.extend(old_items.get(prev["file_id"], []))

    for b in books:
        prev = old_books.get(b["key"])
        if prev and not force and prev["file_id"] == b["file_id"] and prev["edition"] == b["edition"]:
            keep(prev)
            report["unchanged"].append(b["title"])
            continue
        try:
            pdf = download_book(client, b["file_id"])
            items = parse(pdf, b["title"])
        except Exception as e:  # noqa: BLE001 — 한 권이 실패해도 나머지는 갱신한다
            logger.warning("집행기준 책자 처리 실패 (%s): %s", b["title"], e)
            report["failed"].append(f"{b['title']}: {type(e).__name__}: {e}")
            if prev:
                keep(prev)
            continue
        if prev and prev.get("items") and len(items) < prev["items"] * 0.5:
            logger.warning("집행기준 %s: 새로 쪼갠 항목 %d건 < 이전 %d건의 절반 — 이전 것을 둡니다",
                           b["title"], len(items), prev["items"])
            report["kept_old"].append(f"{b['title']}: 새 {len(items)}건 / 이전 {prev['items']}건")
            keep(prev)
            continue
        ed = b["edition"]
        edition = f"{ed[:4]}.{ed[4:6]}.{ed[6:8]}" if len(ed) == 8 else ed
        for it in items:
            it.update(book=b["title"], edition=edition, file_id=b["file_id"])
        new_books.append({**b, "sha1": hashlib.sha1(pdf).hexdigest(), "bytes": len(pdf),
                          "items": len(items), "parsed_at": _now()})
        new_items.extend(items)
        report["updated"].append(f"{b['title']} {edition}판: {len(items)}건")
    _save({"version": 1, "checked_at": report["checked_at"], "books": new_books, "items": new_items}, path)
    report["total_items"] = len(new_items)
    return report


def start_background_refresh(client_factory, days: float, path: Path = DATA_PATH,
                             poll_seconds: float = 6 * 3600) -> Optional[threading.Thread]:
    """서버 안에서 주기적으로 최신판을 확인하는 데몬 스레드. days ≤ 0이면 켜지 않는다.
    마지막 확인(checked_at)에서 days가 지났을 때만 목록을 조회하고, 판이 바뀐 책만 받는다.
    실패하면 checked_at이 그대로라 poll_seconds 뒤 다시 시도한다."""
    if days <= 0:
        return None

    def loop():
        while True:
            try:
                if needs_refresh(days, path):
                    report = refresh(client_factory(), path)
                    logger.info("집행기준 갱신: 변경 %s / 그대로 %d권 / 실패 %s / 총 %d건",
                                report["updated"] or "없음", len(report["unchanged"]),
                                report["failed"] or "없음", report["total_items"])
            except Exception:  # noqa: BLE001 — 스레드가 죽으면 다시는 갱신하지 않는다
                logger.exception("집행기준 갱신 실패 — %d초 뒤 다시 시도", poll_seconds)
            time.sleep(poll_seconds)

    t = threading.Thread(target=loop, name="nts-enforcement-refresh", daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# 조회
# ---------------------------------------------------------------------------

class EnforcementStore:
    """data/enforcement.json을 읽어 번호로 찾는다. 파일이 바뀌면(갱신 스레드) 다음 조회 때 다시 읽는다."""

    def __init__(self, path: Path = DATA_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._stamp = None
        self._data = None
        self._by_no = {}

    def _load(self):
        try:
            st = self.path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            stamp = None
        with self._lock:
            if stamp == self._stamp:
                return
            data = load_data(self.path) if stamp else None
            by_no, seen = {}, set()
            for it in (data or {}).get("items", []):
                # 합본이 두 이름으로 올라 있으면(종합부동산세·증권거래세) 같은 항목이 두 번 들어 있다
                sig = (it["law"], it["no"], _squash(it["text"]))
                if sig in seen:
                    continue
                seen.add(sig)
                by_no.setdefault(it["no"], []).append(it)
            self._data, self._by_no, self._stamp = data, by_no, stamp

    def available(self) -> bool:
        self._load()
        return bool(self._by_no)

    def info(self) -> dict:
        self._load()
        d = self._data or {}
        return {"checked_at": d.get("checked_at"),
                "books": [{"title": b["title"], "edition": b["edition"], "items": b.get("items")}
                          for b in d.get("books", [])],
                "items": sum(len(v) for v in self._by_no.values())}

    def find(self, law: Optional[str], no: str, title: Optional[str] = None) -> Optional[dict]:
        """번호로 한 건. 국세청 검색은 법을 잘못 달아 두기도 해서(주류면허법 항목을 국세기본법으로, 국제조세 책의
        법인세법 항목을 국조법으로) 제목이 있으면 제목이 맞는 쪽을 먼저 고른다."""
        self._load()
        cands = self._by_no.get(normalize_no(no), [])
        if title:
            t = _squash(title)
            exact = [c for c in cands if _squash(c["title"]) == t]
            if exact:
                return next((c for c in exact if c["law"] == law), exact[0])
        same_law = [c for c in cands if c["law"] == law]
        if same_law:
            return same_law[0]
        if title:
            # 번호 표기가 다른 경우('75-75의9-1' ↔ 책 '75∼75의9-0-1') — 같은 법에서 제목으로
            t = _squash(title)
            for items in self._by_no.values():
                for c in items:
                    if c["law"] == law and _squash(c["title"]) == t:
                        return c
        return None

    def browse(self, law: str, prefix: str = "") -> list:
        """그 법의 집행기준을 번호순으로. prefix가 '89'면 89-…·89∼…, '89-154'면 89-154-… 만."""
        self._load()
        prefix = normalize_no(prefix)
        out = []
        for no, items in self._by_no.items():
            if prefix and not (no == prefix or no.startswith(prefix + "-") or no.startswith(prefix + "∼")):
                continue
            out += [c for c in items if c["law"] == law]
        return sorted(out, key=lambda c: _sort_key(c["no"]))


def _sort_key(no: str):
    return [tuple(int(x) for x in re.findall(r"\d+", part)) for part in re.split(r"[-∼]", no)]


_default_store = None


def default_store() -> EnforcementStore:
    global _default_store
    if _default_store is None:
        _default_store = EnforcementStore()
    return _default_store


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="국세청 세목별 집행기준 로컬 보관소")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refresh", help="최신판 확인 → 바뀐 책만 받아 다시 쪼갬")
    r.add_argument("--force", action="store_true", help="판이 같아도 전부 다시 받음")
    sub.add_parser("status", help="보관 중인 책·건수")
    g = sub.add_parser("get", help="번호로 한 건")
    g.add_argument("law")
    g.add_argument("number")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.cmd == "refresh":
        from nts_tax_ruling_search import NtsTaxLawClient
        verify = os.environ.get("NTS_VERIFY_SSL", "true").lower() != "false"
        print(json.dumps(refresh(NtsTaxLawClient(verify_ssl=verify, timeout=180), force=args.force),
                         ensure_ascii=False, indent=1))
    elif args.cmd == "status":
        print(json.dumps(default_store().info(), ensure_ascii=False, indent=1))
    else:
        law = normalize_law(args.law)
        it = default_store().find(law, args.number)
        print(json.dumps(it, ensure_ascii=False, indent=1) if it else f"없음: {law} {args.number}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
