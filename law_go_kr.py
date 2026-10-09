# -*- coding: utf-8 -*-
"""
law_go_kr.py — 국가법령정보센터 Open API(DRF) 클라이언트
nts-tax-mcp 확장 모듈: 대법원 판례 · 법령(현행+연혁) · 법령해석례

- 인증: 환경변수 LAW_API_OC(law.go.kr 가입 시 발급받는 기관코드) 필수. 등록된 IP에서만 동작.
- 응답: XML을 경량 파싱 (JSON 지원이 target마다 들쭉날쭉해 XML로 통일).
- 연혁 워크플로우(2026-07-21 확립, 2026-10-09 수정): lawSearch target=eflaw로 시행본 목록
  → 특정 시행본(연혁 행 = MST + 시행일자)의 문안은 lawService target=eflaw&MST=…&efYd=<행 시행일자>
  (법제처 시행일 기준 통합본) → 조문은 <조문번호> 위치 기준 순차 슬라이스.
  통합본 조회가 실패하면 target=law&MST=…(그 MST 공포 당시 원문)로 폴백하고 '주의'로 알린다.
"""
import difflib
import os
import re
import html
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

BASE = "http://www.law.go.kr/DRF"
OC = os.environ.get("LAW_API_OC", "")
TIMEOUT = 20

# 같은 법령의 조문을 연속 조회하면 매번 법 전체 XML(소득세법 61만 자 등)을
# 다시 받게 되므로, 응답을 짧게 캐싱한다. 법령 개정은 실시간이 아니므로 안전.
_CACHE_TTL = 600   # 초
_CACHE_MAX = 16    # 전문 XML이 커서(수십만 자) 항목 수로 메모리 상한
_cache = {}        # key -> (만료시각, 응답텍스트)

_TAG_RE = re.compile(r"<([^/>\s]+)>\s*(.*?)\s*</\1>", re.S)
_NTS_CASE_NO_RE = re.compile(r"^[가-힣]+-\d{4}-[가-힣]+-\d+$")  # 국세청 판례 표기

# 시행본 문안의 원천 — target=law(공포 당시 원문)에서 target=eflaw(시행일 기준 통합본)로 바꿨다.
# target=law&MST=…는 그 MST가 '공포될 당시'의 원문이다. 연혁 목록에서 같은 MST가 더 늦은 시행일자
# 행으로 다시 나오면(조문별 단계 시행, 타법개정의 늦은 시행일) 그 MST를 현행으로 고르게 되고,
# 그 MST보다 늦게 공포됐지만 이미 시행된 개정이 통째로 빠진다. 거꾸로 아직 시행 전인 문안은 미리
# 들어 있다 (2026-10-09 실측: 상속세 및 증여세법 현행 행 MST 276123(2025.10.1 공포, 시행 20260102)
# 원문에 2025.12.23 개정이 없어 10개 조문이 달랐고, 소득세법 현행 행 MST 280405 원문은 2026.4.21
# 개정이 없어 제59조의2가 '8세 이상'(통합본은 '13세 이상')이었으며 2027년 시행 예정 문안을 현행처럼
# 담고 있었다. 근로기준법 MST 285279 원문에는 2026.12.8 시행 예정인 제101조 문안이 들어 있었다).
# target=eflaw&MST=…&efYd=<그 행의 시행일자>는 법제처가 그 시행일 기준으로 만든 통합본이다
# (2026-10-09 실측, 세법·노동법 7개 법령의 1949~2027년 시행본 70개, 통합본 조회 실패 0건):
#   - 같은 시행일자 행은 MST가 달라도 조문 문안이 같다 (소득세법 20260101 행 MST 4개 문안 지문 동일)
#   - 부칙은 그날까지 시행된 개정의 것만 있다 (공포됐지만 아직 시행 전인 개정의 부칙은 없음)
#   - efYd가 그 MST의 연혁 행 시행일자가 아니면 HTTP 200에 '일치하는 법령이 없습니다'를 준다
#     (efYd 없이 부르면 HTML 오류 화면, ID로 부르고 날짜가 어긋나면 HTTP 500)
_NO_MATCH_TEXT = "일치하는 법령이 없습니다"
# 조문 일부(항·호)만 늦게 시행되면 통합본은 그 문안을 미리 싣고 <조문참고자료>에 시행일을 표시한다.
# 표시 형식은 두 가지다 (2026-10-09 실측):
#   ① "[시행일: 2027.1.1] 제116조제1항제2호" (근로기준법 20261008 통합본 제116조)
#   ② "[시행일] 제55조제2항의 개정규정은 다음 각 호의 구분에 따른 날부터 시행한다.
#        1. 상시 300명 이상의 근로자를 사용하는 사업 또는 사업장 …: 2020년 1월 1일 …" — 사업장 규모별 시행일
#      (근로기준법 제55조 20200526 통합본, 산업안전보건법 제175조 20270101 통합본)
_STAGED_HEAD_RE = re.compile(r"\[시행일\s*(?::\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?)?\s*\]")
_STAGED_ITEM_RE = re.compile(r"(?m)^\s*\d+\.\s*(.*)\s*[:：]\s*(\d{4})\s*(?:년|\.)\s*(\d{1,2})\s*(?:월|\.)\s*"
                             r"(\d{1,2})\s*일?\.?\s*$")
_ANY_DATE_RE = re.compile(r"(\d{4})\s*(?:년|\.)\s*(\d{1,2})\s*(?:월|\.)\s*(\d{1,2})")
# 조문 문안 속 개정 이력 주석 — CDATA 안에 '<개정 2015.5.13, 2026.4.21>', '<신설 …>', '삭제 <2017.12.19>'처럼
# 꺾쇠째 들어 있어 태그 제거(_strip_tags)에 함께 지워진다. 통합본에는 공포본의 <항제개정일자문자열> 태그가
# 없어 이 주석이 개정 이력의 유일한 표시다 (2026-10-09 실측: 소득세법 제59조의2 통합본 원문에서
# '2015.5.13'·'2026.4.21'이 모두 사라짐) — 태그로 오인되지 않게 〈…〉로 바꿔 보존한다
_NOTE_KEEP_RE = re.compile(
    r"<((?:개정|신설|삭제|전문개정|본조신설|본조삭제|제목개정|제목신설|타법개정)(?=[\s\d>,])[^<>]*"
    r"|\d{4}\.\s?\d{1,2}\.\s?\d{1,2}\.?(?:\s*,\s*\d{4}\.\s?\d{1,2}\.\s?\d{1,2}\.?)*)>")
# 조문참고자료의 대괄호 개정 주석 — '[본조신설 2019.12.31]', '[전문개정 2025.12.23]', '[종전 제6조는 … 〈2010.12.27〉]'
# ('[시행일: …]'은 시행일 표시라 제외, '[헌법불합치, …]'류 결정 주석도 제외)
_BRACKET_NOTE_RE = re.compile(r"\[((?:본조신설|전문개정|제목개정|제목신설|본조삭제|종전|조번호개정)[^\[\]]*)\]")
_MIDDOT_RE = re.compile(r"[\s·ㆍ‧・]")
_BOX_RE = re.compile(r"[─-╿]")   # 표 괘선 문자
_KST = timezone(timedelta(hours=9))


def _today_kst() -> str:
    """오늘 날짜(한국 시간, YYYYMMDD) — '현행'·'과거 기준일' 판정의 기준."""
    return datetime.now(_KST).strftime("%Y%m%d")


def _keep_notes(text: str) -> str:
    """CDATA를 벗긴 문안에서 개정 주석 꺾쇠 '<…>'를 '〈…〉'로 바꾼다 (태그 제거 전에 호출)."""
    return _NOTE_KEEP_RE.sub(r"〈\1〉", text)


def _norm_name(name: str) -> str:
    """법령명 비교용 — 공백과 가운뎃점(·ㆍ‧・)을 뺀다 ('노동조합및노동관계조정법' = '노동조합 및 노동관계조정법')."""
    return _MIDDOT_RE.sub("", name or "")


def _norm_article_no(article_no) -> str:
    """조번호 입력을 '17' 또는 '76의2' 형식으로 정규화한다. '제17조'·'17조'·'76의 2'·'제76조의2'·'013'을 받는다.
    형식이 아니면 LawGoKrError(INVALID_INPUT) — 예전에는 형식 오류가 '조문 없음'(NOT_FOUND)으로 나가
    '그런 조문은 없다'로 읽혔다 (2026-10-09 실측: '제59조의2'·'59의 2' → NOT_FOUND '제제59조의2조')."""
    t = re.sub(r"\s+", "", str(article_no or ""))
    m = re.fullmatch(r"제?0*(\d+)조?(?:의0*(\d+))?조?", t)
    if not m:
        raise LawGoKrError(f"article_no 형식 오류: '{article_no}' — 조번호만 넣으세요 "
                           f"(예: '17', '76의2', '제76조의2'. 항·호는 빼고, 행정규칙식 '9-5'는 법령에 없음)")
    return str(int(m.group(1))) + (f"의{int(m.group(2))}" if m.group(2) else "")


def _article_label(article_no: str) -> str:
    m = re.match(r"^(\d+)(?:의(\d+))?$", article_no)
    return (f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")) if m else f"제{article_no}조"


def _article_pat(label: str):
    """'제96조'가 '제96조의2'·'제960조'에, '제59조의2'가 '제59조의21'에 걸리지 않는 정규식.
    '제30조의 개정규정'처럼 소유격 '의'는 허용한다 — 예전 (?!의) 가드는 이 흔한 표현까지 걸러 부칙 언급을
    놓쳤다 (2026-10-09 실측: 노인장기요양보험법 부칙 '제30조의 개정규정은 공포한 날부터 시행한다')."""
    return re.compile(re.escape(label) + (r"(?!\d)" if "의" in label else r"(?!의\d|\d)"))


def _staged_notes(text: str):
    """조문 텍스트의 [시행일] 표시를 모두 뽑는다 (기준일 비교 전). 반환 [{"시행일자", "대상"(, "조건")}] —
    형식 ②(사업장 규모별)는 각 호마다 하나씩 '조건'을 붙인다."""
    out = []
    heads = list(_STAGED_HEAD_RE.finditer(text))
    for i, h in enumerate(heads):
        seg = text[h.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        nb = re.search(r"\n\s*\[", seg)   # 뒤이은 '[본조신설 …]' 같은 대괄호 주석 줄에서 끊는다
        if nb:
            seg = seg[:nb.start()]
        lines = [ln.strip() for ln in seg.strip().splitlines() if ln.strip()]
        first = lines[0] if lines else ""
        if h.group(1):
            out.append({"시행일자": f"{h.group(1)}{int(h.group(2)):02d}{int(h.group(3)):02d}",
                        "대상": first.rstrip(",").strip()})
            continue
        what = re.split(r"(?:의\s*개정규정)?\s*(?:은|는)?\s*다음\s*각\s*호의", first)[0].strip() or first
        items = _STAGED_ITEM_RE.findall(seg)
        for cond, y, mo, d in items:
            out.append({"시행일자": f"{y}{int(mo):02d}{int(d):02d}", "대상": what, "조건": cond.strip()})
        if not items:   # 각 호가 없는 다른 형식 — 날짜만이라도 뽑는다
            for y, mo, d in _ANY_DATE_RE.findall(seg):
                out.append({"시행일자": f"{y}{int(mo):02d}{int(d):02d}", "대상": what})
    return out


def _staged_caution(staged) -> str:
    """시행예정_부분 → '주의' 문구."""
    parts = []
    for s in staged:
        cond = f" [{s['조건'][:60]}{'…' if len(s['조건']) > 60 else ''}]" if s.get("조건") else ""
        parts.append(f"{s['대상']}{cond}: {s['시행일자']} 시행")
    msg = ("이 조문 일부(" + ", ".join(parts) + ")는 아직 시행 전인데 법제처 통합본에 [시행일] 표시와 함께 "
           "미리 실려 있음 — 그 부분은 표시된 시행일부터 적용")
    if any(s.get("조건") for s in staged):
        msg += " (사업장 규모 등 조건별로 시행일이 달라 해당 사업장에는 아직 시행 전일 수 있음)"
    return msg


def _norm_key(lines):
    """문안 같음 판정용 키 — 표 괘선 폭·전각/반각 기호·공백·줄바꿈 차이를 무시한다. diff 출력은 원문 줄 그대로.
    (2026-10-09 실측: 소득세법 제35조 20241231→20261009의 실제 차이는 표 안 '(수입금액 - 500억원)'이
    '(수입금액 － 500억원)'으로 바뀐 것뿐인데 '개정'으로 나오고 엉뚱한 개정 공포본(제20615호)이 특정됐다.
    '?'는 공포본 원문 일부의 깨진 기호 — 원본 코드 비교에서 '?'→'－'가 같은 오판을 냈다)"""
    if lines is None:
        return None
    t = unicodedata.normalize("NFKC", "\n".join(lines))
    t = _BOX_RE.sub("", t).replace("?", "-")
    return re.sub(r"\s+", "", t)


def _add_caution(out: dict, msg: str):
    """응답의 '주의'에 문구를 덧붙인다 (이미 있으면 ' / '로 잇는다)."""
    if msg:
        out["주의"] = f"{out['주의']} / {msg}" if out.get("주의") else msg


class LawGoKrError(Exception):
    """입력 오류 등 일반 오류 (호출자가 INVALID_INPUT으로 분류)"""


class LawGoKrAuthError(LawGoKrError):
    """OC 미설정·IP 미등록 등 인증 실패 — 자료 부존재와 무관"""


class LawGoKrNotFound(LawGoKrError):
    """검색·조회는 성공했으나 해당 자료가 원천에 없음"""


class LawGoKrUpstreamError(Exception):
    """원천 응답 이상으로 판정할 수 없음 (통합본 조회 실패 뒤 대체 원문에도 조문이 없는 경우 등) — 자료 부존재와
    무관하므로 UPSTREAM_ERROR로 분류된다. LawGoKrError를 상속하지 않는다 (상속하면 INVALID_INPUT으로 분류됨)."""


def _strip_cdata(s: str) -> str:
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s, flags=re.S)
    return html.unescape(s).strip()


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


def _get(endpoint: str, **params) -> str:
    if not OC:
        raise LawGoKrAuthError("LAW_API_OC 환경변수가 설정되지 않았습니다 — law.go.kr에서 발급받은 기관코드(OC)를 설정하세요.")
    p = {"OC": OC, "type": "XML"}
    p.update({k: v for k, v in params.items() if v not in (None, "", 0)})
    key = (endpoint, tuple(sorted(p.items())))
    hit = _cache.get(key)
    now = time.time()
    if hit and hit[0] > now:
        return hit[1]
    r = requests.get(f"{BASE}/{endpoint}", params=p, timeout=TIMEOUT,
                     headers={"User-Agent": "nts-tax-mcp/1.0"})
    r.raise_for_status()
    text = r.text
    if "인증" in text[:500] and "실패" in text[:500]:
        raise LawGoKrAuthError("law.go.kr 인증 실패 — OC 또는 IP 등록 확인 필요 (open.law.go.kr에서 현재 서버 IP 등록)")
    if len(_cache) >= _CACHE_MAX:
        del _cache[min(_cache, key=lambda k: _cache[k][0])]
    _cache[key] = (now + _CACHE_TTL, text)
    return text


def _parse_items(xml: str, item_tag: str):
    """<item_tag>…</item_tag> 블록마다 자식 태그를 dict로."""
    items = []
    for m in re.finditer(rf"<{item_tag}(?:\s[^>]*)?>(.*?)</{item_tag}>", xml, re.S):
        block = m.group(1)
        d = {}
        for tag, val in _TAG_RE.findall(block):
            d[tag] = _strip_cdata(val)
        if d:
            items.append(d)
    return items


def _same_name_only(rows: list, law_name: str) -> list:
    """법령ID 없이 이름으로 연혁을 찾으면 그 이름으로 시작하는 시행령·시행규칙 시행본까지 섞이고,
    시행일이 더 늦은 하위법령 조문이 반환된다 (2026-10-09 실측: '노인장기요양보험법' 제9조·제10조가
    시행령 조문으로 나옴). 이름이 정확히 같은 법령(공백·가운뎃점 무시)의 법령ID 행만 남긴다 — 같은 ID면
    옛 이름의 시행본도 남는다. 같은 이름이 없으면 그대로 둔다(연혁 검색 도구용 — 조문 조회는
    _require_exact_name으로 거부).
    같은 이름의 법령ID가 여럿이어도 그 ID들로 거른다 — 폐지 후 재제정된 법은 옛 ID와 새 ID가 이름이 같다.
    예전에는 여럿이면 거르지 않아 하위법령 행이 남았고, 기준일 이하 마지막 행이 시행규칙이면 시행규칙 조문이
    법률 조문으로 나갔다 (2026-10-09 실측: '노동조합 및 노동관계조정법'(000142·000143) 제2조가 시행규칙
    제2조로, '근로기준법'(001769·001872) 19960101 제1조가 시행령으로, 증권거래세법·교육세법도 같은 양상)."""
    key = _norm_name(law_name)
    ids = {r["법령ID"] for r in rows if _norm_name(r.get("법령명", "")) == key}
    if not ids:
        return rows
    return [r for r in rows if r["법령ID"] in ids]


def _require_exact_name(rows: list, law_name: str):
    """법령ID 없이 이름으로 조문·부칙을 찾을 때, 이름이 정확히 같은 법령이 연혁에 없으면 거부한다(INVALID_INPUT).
    그대로 두면 이름 검색에 섞인 다른 법령의 조문이 OK로 나간다 (2026-10-09 실측: '고용보험' 제2조가
    '고용보험 및 산업재해보상보험의 보험료징수 등에 관한 법률' 제2조로 나옴)."""
    key = _norm_name(law_name)
    if any(_norm_name(r.get("법령명", "")) == key for r in rows):
        return
    names = []
    for r in rows:
        n = r.get("법령명", "")
        if n and n not in names:
            names.append(n)
    raise LawGoKrError(
        f"'{law_name}'과(와) 이름이 정확히 같은 법령이 연혁 검색 결과에 없음 — 정확한 법령명(약칭 불가)이나 "
        f"law_id로 다시 조회하세요" + (f" (검색된 법령: {', '.join(names[:10])}{' 등' if len(names) > 10 else ''})"
                                    if names else ""))


class LawGoKrClient:
    # ---------- 판례 (법제처 제공 대법원·하급심) ----------
    def search_cases(self, keyword: str, court: str = "", date_from: str = "",
                     date_to: str = "", display: int = 10, page: int = 1):
        """target=prec 판례 검색. court: '대법원' 또는 '하위법원'(빈값=전체).
        date_from/date_to: YYYYMMDD (선고일자 범위)."""
        params = dict(target="prec", query=keyword, display=display, page=page, search=2)
        if court:
            params["curt"] = court
        if date_from or date_to:
            params["prncYd"] = f"{date_from or '19450815'}~{date_to or '20991231'}"
        xml = _get("lawSearch.do", **params)
        items = _parse_items(xml, "prec")
        total = re.search(r"<totalCnt>(\d+)</totalCnt>", xml)
        cases = []
        for it in items:
            row = {
                "판례일련번호": it.get("판례일련번호", ""),
                "사건명": it.get("사건명", ""),
                "사건번호": it.get("사건번호", ""),
                "법원명": it.get("법원명", ""),
                "선고일자": it.get("선고일자", ""),
                "판결유형": it.get("판결유형", ""),
                "사건종류명": it.get("사건종류명", ""),
            }
            # 법제처 DB에는 국세법령정보시스템이 넘겨준 판례가 섞여 있다('서울고등법원-2022-누-45199'
            # 꼴, 법원명 빈칸). 이들은 lawService가 본문을 주지 않는다(2026-10-04 실측).
            if _NTS_CASE_NO_RE.match(row["사건번호"]) and not row["법원명"]:
                row["본문조회"] = (f"국세청 제공 판례라 court_case_detail로는 열리지 않음 — "
                                 f"nts_ruling_get_full_text('{row['사건번호']}')로 전문 조회")
            cases.append(row)
        out = {"total": int(total.group(1)) if total else len(items), "cases": cases}
        if not out["cases"]:
            out["status"] = "NOT_FOUND"
        return out

    def get_case(self, case_serial: str, max_chars: int = 8000, start_char: int = 0):
        """target=prec 판례 본문. case_serial = 판례일련번호.
        판례내용은 max_chars씩 잘라 주고, 잘리면 이어 읽을 start_char를 안내한다."""
        xml = _get("lawService.do", target="prec", ID=case_serial)
        d = {}
        for tag in ("사건명", "사건번호", "법원명", "선고일자", "판시사항", "판결요지", "참조조문", "참조판례", "판례내용"):
            m = re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.S)
            if m:
                d[tag] = _strip_tags(_strip_cdata(m.group(1)))
        if not d:
            raise LawGoKrNotFound(
                f"판례 본문 없음 (일련번호 {case_serial}) — court_case_search 결과의 사건번호가 "
                f"'서울고등법원-2022-누-45199' 꼴(법원명 빈칸)이면 국세청 제공 판례이므로 "
                f"nts_ruling_get_full_text(사건번호)로 조회하세요. 응답 앞부분: {xml[:200]}")
        for tag in ("판시사항", "판결요지", "참조조문", "참조판례"):
            if tag in d and len(d[tag]) > 4000:
                d[tag] = d[tag][:4000] + " …(4000자 이후 생략)"
        body = d.pop("판례내용", None)
        if body is not None:
            total = len(body)
            end = min(start_char + max_chars, total)
            d["판례내용"] = body[start_char:end]
            d["판례내용_전체자수"] = total
            if end < total or start_char:
                d["잘림"] = (f"판례내용 전체 {total}자 중 {start_char}~{end}자만 표시됨 — "
                            f"이어 읽으려면 start_char={end}로 다시 조회 "
                            f"(또는 max_chars를 {total} 이상으로 지정)")
        return d

    # ---------- 법령해석례 (법제처) ----------
    def search_interpretations(self, keyword: str, display: int = 10, page: int = 1):
        """target=expc 법령해석례 검색."""
        xml = _get("lawSearch.do", target="expc", query=keyword, display=display, page=page)
        items = _parse_items(xml, "expc")
        return [{
            "해석례일련번호": it.get("법령해석례일련번호", it.get("일련번호", "")),
            "안건명": it.get("안건명", ""),
            "안건번호": it.get("안건번호", ""),
            "회신기관": it.get("회신기관명", it.get("질의기관명", "")),
            "회신일자": it.get("회신일자", ""),
        } for it in items]

    def get_interpretation(self, serial: str, max_chars: int = 8000):
        xml = _get("lawService.do", target="expc", ID=serial)
        d = {}
        for tag in ("안건명", "안건번호", "회신일자", "질의요지", "회답", "이유"):
            m = re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.S)
            if m:
                d[tag] = _strip_tags(_strip_cdata(m.group(1)))[:max_chars]
        if not d:
            raise LawGoKrNotFound(f"법령해석례 본문 없음 (일련번호 {serial}) — 응답 앞부분: {xml[:200]}")
        return d

    # ---------- 법령: 현행 검색 ----------
    def search_laws(self, law_name: str, display: int = 20):
        """target=law 현행 법령 검색 → 법령ID·MST(법령일련번호)·시행일자."""
        xml = _get("lawSearch.do", target="law", query=law_name, display=display)
        items = _parse_items(xml, "law")
        return [{
            "법령명": it.get("법령명한글", ""),
            "법령ID": it.get("법령ID", ""),
            "MST": it.get("법령일련번호", ""),
            "시행일자": it.get("시행일자", ""),
            "공포일자": it.get("공포일자", ""),
            "공포번호": it.get("공포번호", ""),
            "제개정구분": it.get("제개정구분명", ""),
        } for it in items]

    # ---------- 법령: 연혁(시행본) 목록 ----------
    def law_history(self, law_name: str, law_id: str = "", max_rows: int = 500):
        """target=eflaw 연혁 시행본 전체 목록. law_id를 주면 그 본법만 필터
        (예: 부가가치세법=001571 — 같은 이름 하위법령 혼입 방지).

        max_rows 기본 500: 소득세법처럼 시행본이 많은 법령은 100행에서 끊으면
        2011년 이전 연혁이 통째로 누락되어 law_article_as_of·law_article_diff가
        과거 시점을 조회하지 못한다 (2026-08-28 실측)."""
        rows = []
        page = 1
        while len(rows) < max_rows and page <= 10:
            # 페이지 크기 파라미터는 display가 맞음 — numOfRows는 무시되어 20건씩 5왕복하게 됨 (2026-08-18 실측)
            xml = _get("lawSearch.do", target="eflaw", query=law_name, display=100, page=page)
            items = _parse_items(xml, "law")
            if not items:
                break
            for it in items:
                if law_id and it.get("법령ID", "") != law_id:
                    continue
                rows.append({
                    "법령명": it.get("법령명한글", ""),
                    "법령ID": it.get("법령ID", ""),
                    "MST": it.get("법령일련번호", ""),
                    "시행일자": it.get("시행일자", ""),
                    "공포일자": it.get("공포일자", ""),
                    "공포번호": it.get("공포번호", ""),
                    "제개정구분": it.get("제개정구분명", ""),
                })
            page += 1
        if not law_id:
            rows = _same_name_only(rows, law_name)
        # 같은 시행일자에 여러 공포본이 오는 일이 흔하다 — 조문별 단계 시행 때문에
        # 옛 공포본이 미래 시행일자 행으로 재등장하기 때문(2026-08-28 실측: 소득세법
        # 시행일자 20250101에 MST 6개). 시행일자만으로 정렬하면 '기준일 이하 마지막 행'을
        # 고르는 쪽(law_article_as_of·law_article_diff)이 그 그룹에서 가장 오래된 공포본을
        # 집어 옛 문안을 반환하므로, 공포일자·공포번호까지 정렬 키에 넣는다.
        # 같은 이름의 옛 ID·새 ID 행이 함께 남으면(폐지 후 재제정) 옛 법의 폐지 행과 새 법의 제정 행이 같은
        # 시행일자·공포번호로 겹친다 — 폐지 행을 앞에 두어 '기준일 이하 마지막 행'이 새 법이 되게 한다
        rows.sort(key=lambda r: (r.get("시행일자", ""), r.get("공포일자", ""),
                                 (r.get("공포번호", "") or "").zfill(10),
                                 0 if "폐지" in (r.get("제개정구분") or "") else 1))
        return rows

    # ---------- 법령: 특정 시행본의 조문 원문 ----------
    @staticmethod
    def _find_article_block(xml: str, article_no: str):
        """법령 원문 XML에서 조문 블록을 슬라이스. 반환 (조문 라벨, 블록 or None).
        law_article(원문 반환)와 law_article_diff(구조적 비교)가 공유한다."""
        # 조문 뒤에 <부칙> 섹션(수십만 자)이 이어지므로 슬라이스 범위에서 잘라낸다 —
        # 안 자르면 마지막 조문 조회 시 부칙 전체가 본문에 딸려 나온다. 부칙은 law_addenda로.
        cut = xml.find("<부칙>")
        if cut != -1:
            xml = xml[:cut]
        base_no, branch_no = article_no.strip(), None
        m = re.match(r"^(\d+)의(\d+)$", article_no.strip())
        if m:
            base_no, branch_no = m.group(1), m.group(2)
        label = f"제{base_no}조" + (f"의{branch_no}" if branch_no else "")
        # <조문번호> 위치 순차 슬라이스 (단순 <조문>…</조문> 정규식은 실패 — 2026-07-21 확인)
        positions = [(mm.start(), mm.group(1)) for mm in re.finditer(r"<조문번호>(\d+)</조문번호>", xml)]
        for i, (pos, no) in enumerate(positions):
            if no != base_no:
                continue
            end = positions[i + 1][0] if i + 1 < len(positions) else len(xml)
            block = xml[pos:end]
            bm = re.search(r"<조문가지번호>(\d+)</조문가지번호>", block[:300])
            blk_branch = bm.group(1) if bm else None
            if branch_no != blk_branch:
                continue
            # 절/관/장이 이 조문에서 시작되면 그 표제가 동일 <조문번호>를 단 채
            # 실제 조문보다 먼저 나온다(예: 제104조 앞의 "제6절 …" 노드) — 표제 블록은
            # 조번호 문자열 자체를 포함하지 않으므로 걸러내고 다음 후보를 찾는다.
            if label not in _strip_tags(_strip_cdata(block)):
                continue
            return label, block
        return label, None

    @staticmethod
    def _version_xml(mst: str, ef_yd: str = "", alt_msts=()):
        """시행본(연혁 행 = MST + 시행일자)의 원문 XML. 반환 (xml, 주의 문구 or "").

        ef_yd(그 행의 시행일자)를 주면 시행일 기준 통합본(target=eflaw)을 받는다. 통합본 조회가
        실패하면(HTTP 오류, '일치하는 법령이 없습니다', 조문 없는 오류 화면) alt_msts(같은 시행일자의 다른
        MST — 통합본 문안이 같다)로 한 번씩 더 시도하고, 그래도 안 되면 그 MST 공포 당시 원문
        (target=law)으로 폴백하고 주의 문구를 돌려준다. 접속 실패·인증 실패는 폴백하지 않고 그대로
        올린다 — target=law도 같은 원천이라 결과가 같고, 부존재로 오판되면 안 되기 때문.
        ef_yd가 없으면 target=law (MST만 아는 호출 — 공포 당시 원문)."""
        if not ef_yd:
            return _get("lawService.do", target="law", MST=mst), ""
        reason = ""
        for m in (mst,) + tuple(alt_msts)[:2]:
            try:
                xml = _get("lawService.do", target="eflaw", MST=m, efYd=ef_yd)
                if "<조문단위" in xml:
                    return xml, ""
                reason = "일치하는 법령 없음" if _NO_MATCH_TEXT in xml[:500] else "조문 없는 응답"
            except requests.exceptions.HTTPError as e:
                code = getattr(e.response, "status_code", "")
                reason = f"HTTP {code}".strip()
        xml = _get("lawService.do", target="law", MST=mst)
        return xml, (f"시행일 기준 통합본(MST {mst}, 시행일자 {ef_yd}) 조회 실패({reason}) — 그 MST "
                     f"공포 당시 원문으로 대신 조회함. 이 원문에는 그 뒤에 공포됐지만 이미 시행된 개정이 "
                     f"빠졌거나 아직 시행 전인 문안이 들어 있을 수 있으니 국가법령정보센터(law.go.kr)에서 "
                     f"시행일 기준 문안을 확인할 것")

    @staticmethod
    def _block_text(block: str) -> str:
        """조문 블록 → 사람이 읽는 원문 (개정 주석 〈…〉 보존)."""
        return _strip_tags(_keep_notes(_strip_cdata(block)))

    @classmethod
    def _staged_parts(cls, block: str, base_date: str):
        """조문 블록의 [시행일] 표시 중 base_date보다 늦은 것 (시행예정_부분).
        통합본은 조문 일부(항·호)만 늦게 시행되면 그 문안을 미리 싣고 이렇게 표시한다.
        base_date는 기준일이다 — law_article_as_of는 시행본 시행일자가 아니라 as_of_date를 넘긴다
        (단계 시행일에 연혁 행이 없는 경우가 있어 시행본 날짜로 비교하면 기준일에 이미 시행 중인 부분을
        '아직 시행 전'으로 표시한다 — 2026-10-09 실측: 산업안전보건법 [시행일] 2028.1.1에 연혁 행 없음)."""
        if not base_date:
            return []
        return [s for s in _staged_notes(cls._block_text(block)) if s["시행일자"] > base_date]

    def law_article(self, mst: str, article_no: str, max_chars: int = 6000, ef_yd: str = "",
                    as_of: str = "", alt_msts=()):
        """시행본(연혁 행 = MST + 시행일자)의 조문 원문을 조번호로 슬라이스.
        ef_yd(그 행의 시행일자)를 주면 시행일 기준 통합본(target=eflaw&MST&efYd)에서 자른다 —
        연혁 행에서 고른 시행본이면 반드시 ef_yd를 넘길 것. 없으면 그 MST 공포 당시 원문
        (target=law&MST)이라 늦게 공포된 개정이 빠지거나 시행 전 문안이 섞일 수 있다.
        as_of: '시행예정_부분' 판정 기준일 (생략 시 시행본 시행일자).
        article_no: '17' 또는 '17의2' (패딩 없음)."""
        xml, caution = self._version_xml(mst, ef_yd, alt_msts)
        label, block = self._find_article_block(xml, article_no)
        if block is None:
            if caution:
                # 대체 원문(공포 당시 원문)에 없다고 실제 시행본에 없는 것은 아니다 — 늦게 공포된 개정이 신설한
                # 조문이면 그 MST 원문에는 없다. NOT_FOUND(부존재 단정 가능)로 내보내면 안 된다
                raise LawGoKrUpstreamError(
                    f"MST {mst}(시행일자 {ef_yd})의 시행일 기준 통합본을 받지 못해 공포 당시 원문으로 대신 봤으나 "
                    f"{label} 조문이 없음 — 조문 부존재로 단정할 수 없음 ({caution})")
            where = f"MST {mst}" + (f"(시행일자 {ef_yd} 통합본)" if ef_yd else "")
            raise LawGoKrNotFound(f"{where}에서 {label} 조문을 찾지 못함")
        body = self._block_text(block)
        title = re.search(r"<조문제목>(.*?)</조문제목>", block, re.S)
        out = {
            "조문": label,
            "조문제목": _strip_cdata(title.group(1)) if title else "",
        }
        # <조문시행일자>는 통합본에서는 모든 조문이 그 시행본의 시행일자(efYd)라 조문별 시행일이 아니다
        # (2026-10-09 실측, 시행본 70개 전 조문) — '조문시행일자'로 내보내면 '제59조의2는 2026.7.1 시행'처럼
        # 잘못 인용된다(실제는 2026.4.21). 시행본 날짜는 law_article_as_of의 적용시행본.시행일자에 있고,
        # 조문 문안이 언제부터 시행됐는지는 law_article_diff의 현행_문안_시작으로 본다.
        # 공포 당시 원문(ef_yd 없음·폴백)에서는 원천 태그 그대로 둔다.
        if not ef_yd or caution:
            em = re.search(r"<조문시행일자>(\d+)</조문시행일자>", block)
            if em:
                out["조문시행일자"] = em.group(1)
        out["원문"] = body[:max_chars]
        if len(body) > max_chars:
            out["잘림"] = (f"전체 {len(body)}자 중 앞 {max_chars}자만 표시됨 — "
                         f"max_chars를 {len(body)} 이상으로 지정해 다시 조회하면 전문을 볼 수 있음")
        _add_caution(out, caution)
        hdr = re.search(r"<시행일자>(\d+)</시행일자>", xml)  # 첫 <시행일자> = 기본정보(시행본 시행일자)
        staged = self._staged_parts(block, as_of or ef_yd or (hdr.group(1) if hdr else ""))
        if staged:
            out["시행예정_부분"] = staged
            _add_caution(out, _staged_caution(staged))
        return out

    # ---------- 부칙 (시행일·적용례·경과조치) ----------
    @staticmethod
    def _parse_addenda_units(xml: str):
        """lawService 응답 XML(시행일 기준 통합본 target=eflaw 또는 공포 당시 원문 target=law)에서
        <부칙단위> 목록을 파싱. law_addenda와 law_article_diff(개정 부칙 연결)가 공유한다."""
        units = []
        for m in re.finditer(r"<부칙단위[^>]*>(.*?)</부칙단위>", xml, re.S):
            blk = m.group(1)
            dt = re.search(r"<부칙공포일자>(\d+)</부칙공포일자>", blk)
            no = re.search(r"<부칙공포번호>(\d+)</부칙공포번호>", blk)
            bd = re.search(r"<부칙내용>(.*?)</부칙내용>", blk, re.S)
            body = _strip_cdata(bd.group(1)) if bd else ""
            # 머리말 "부칙 <제21221호,2025.12.23>"의 꺾쇠가 태그 제거에 지워지지 않게 보존
            body = re.sub(r"<(제\d+호[^>]*?)>", r"〈\1〉", body)
            body = _strip_tags(body)
            body = re.sub(r"[ \t]+\n", "\n", body)
            body = re.sub(r"\n{2,}", "\n", body)  # CDATA 조각 사이 빈 줄 압축
            units.append({
                "공포일자": dt.group(1) if dt else "",
                "공포번호": no.group(1).lstrip("0") if no else "",  # "04803" 식 패딩 제거
                "본문": body,
            })
        return units

    @staticmethod
    def _split_addendum(text: str):
        """부칙 본문을 '제N조(…)' 항 단위로 분할. 조 편제 없는 단순 부칙은 통짜 1개.
        구식 '제1조 (시행일)'처럼 조와 괄호 사이 공백이 있는 표기도 같은 앵커로 잡힌다."""
        starts = [m.start() for m in re.finditer(r"(?m)^제\d+조(?:의\d+)?\s*\(", text)]
        if not starts:
            t = text.strip()
            return [t] if t else []
        parts = []
        head = text[:starts[0]].strip()
        if head:
            parts.append(head)  # "부칙 <제21221호,2025.12.23>" 머리말
        for i, s in enumerate(starts):
            e = starts[i + 1] if i + 1 < len(starts) else len(text)
            parts.append(text[s:e].strip())
        return parts

    @staticmethod
    def _effective_summary(body: str, limit: int = 350):
        """부칙에서 시행일 부분만 요약 — 첫 '시행한다' 줄 + '각 호' 단서면 이어지는 호 목록."""
        m = re.search(r"(?m)^\s*(.*?시행한다.*)$", body)
        if not m:
            first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
            return first[:limit]
        lines = [m.group(1).strip()]
        if "각 호" in lines[0]:
            for ln in body[m.end():].splitlines():
                t = ln.strip()
                if not t:
                    continue
                if re.match(r"^\d+\.", t):
                    lines.append(t)
                else:
                    break
        out = " ".join(lines)
        return out[:limit] + ("…" if len(out) > limit else "")

    def law_addenda(self, mst: str = "", law_name: str = "", law_id: str = "",
                    as_of_date: str = "", promul_no: str = "", article_no: str = "",
                    recent: int = 10, max_chars: int = 8000):
        """법령 부칙(附則) 조회 — 개정규정의 시행일·적용례·경과조치.
        시행본 원문의 <부칙단위> 목록을 파싱한다 (2026-08-26 실측: 소득세법 현행본에 부칙
        114건, 각각 공포일자·공포번호·본문 보유).

        - 시행본(연혁 행 = MST + 시행일자)의 시행일 기준 통합본에서 부칙을 읽는다. 통합본에는 그날까지
          시행된 개정의 부칙만 있다 (그 MST보다 늦게 공포됐지만 먼저 시행된 개정의 부칙은 들어 있고,
          공포됐지만 아직 시행 전인 개정의 부칙은 없다 — 2026-10-09 실측)
          · law_name: as_of_date를 주면 그 시점 시행본, 없으면 현행 시행본
          · mst: 그 MST의 연혁 행 중 기준일(as_of_date, 없으면 오늘) 이하 마지막 행. 예전에는 MST 공포 당시
            원문을 써서 늦게 공포·먼저 시행된 개정의 부칙이 빠지고 as_of_date도 무시했다 (2026-10-09 실측:
            mst=280405에 2026.4.21 제21548호 부칙이 없고 시행본_시행일자가 20260101로 나옴)
        - 기준일 현재 공포됐지만 아직 시행 전인 개정은 연혁에서 찾아 따로 알린다 — 목록 모드는 '시행전_개정',
          article_no 모드는 그 개정들의 부칙(공포본 원문)에서 조문 언급을 '시행전_개정_부칙_언급'으로 발췌.
          통합본으로 바꾸면서 생긴 누락을 막는다 (2026-10-09 실측: 소득세법 article_no='21'에서 2027.1.1
          시행 예정인 2023.7.18 타법개정 제19563호 부칙 ⑥이 빠짐)
        - promul_no: 그 공포번호 개정의 부칙 전문 (공포번호는 law_history_search 목록에 있음).
          시행본에 없으면(아직 시행 전 등) 그 공포본 원문에서 찾는다. '아직 시행 전' 판정은 기준일
          (as_of_date, 없으면 오늘) 기준이다
        - article_no: 전체 부칙에서 그 조문이 언급된 항(적용례·경과조치)만 최신순 발췌
        - 둘 다 없으면: 최신순 recent건 목록 (공포일자·공포번호·시행일 요약)
        """
        if as_of_date and not re.match(r"^\d{8}$", str(as_of_date)):
            raise LawGoKrError(f"as_of_date 형식 오류: '{as_of_date}' (YYYYMMDD 8자리)")
        if article_no:
            article_no = _norm_article_no(article_no)
        ref = as_of_date or _today_kst()   # '아직 시행 전' 판정 기준일
        ef_yd, history, pre_caution = "", None, ""
        if not mst:
            if not law_name:
                raise LawGoKrError("mst 또는 law_name 중 하나는 필요합니다")
            if as_of_date:
                history = self.law_history(law_name, law_id=law_id)
                if history and not law_id:
                    _require_exact_name(history, law_name)
                chosen = None
                for row in history:  # 시행일자 오름차순
                    if row["시행일자"] and row["시행일자"] <= as_of_date:
                        chosen = row
                if not chosen:
                    raise LawGoKrNotFound(f"{as_of_date} 이전 시행본 없음: {law_name}")
                mst, ef_yd = chosen["MST"], chosen["시행일자"]
            else:
                rows = self.search_laws(law_name)
                if law_id:
                    rows = [r for r in rows if r.get("법령ID") == law_id]
                exact = [r for r in rows if r.get("법령명") == law_name.strip()]
                pick = exact or rows  # "소득세법" 검색에 시행령·시행규칙이 섞여 나와 정확 일치 우선
                if not pick:
                    raise LawGoKrNotFound(f"법령 검색 결과 없음: {law_name}")
                chosen = pick[0]
                # 현행 검색 결과의 시행일자도 연혁 행의 시행일자다 (2026-10-09 실측: 소득세법 현행
                # MST 280405 시행일자 20260701 = 연혁 '기준일 이하 마지막 행')
                mst, ef_yd = chosen["MST"], chosen.get("시행일자", "")
                if not ef_yd:   # 시행일자가 비면 연혁에서 그 MST의 행을 찾는다 — 주의 없이 공포본으로 가지 않게
                    history = self.law_history(chosen.get("법령명") or law_name,
                                               law_id=chosen.get("법령ID") or law_id)
                    ef_yd = self._row_date_for_mst(history, mst, ref)
                    if not ef_yd:
                        pre_caution = (f"현행 검색 결과(MST {mst})에 시행일자가 없고 연혁 행도 찾지 못해 그 MST "
                                       f"공포 당시 원문의 부칙을 보여 줌 — 그 뒤에 공포·시행된 개정의 부칙은 없음")
        else:
            # MST만 오면 헤더의 법령명·법령ID로 연혁을 받아 그 MST의 연혁 행(기준일 이하 마지막 시행일자)을 쓴다
            head = _get("lawService.do", target="law", MST=mst)
            hname = re.search(r"<법령명_한글>(.*?)</법령명_한글>", head, re.S)
            hname = _strip_cdata(hname.group(1)) if hname else ""
            if law_name or hname:
                history = self.law_history(law_name or hname, law_id=law_id or self._header_law_id(head))
                ef_yd = self._row_date_for_mst(history, mst, ref)
            if not ef_yd:
                pre_caution = (f"MST {mst}의 연혁 행을 찾지 못해 그 MST 공포 당시 원문의 부칙을 보여 줌 — 그 뒤에 "
                               f"공포·시행된 개정의 부칙은 없음. 현행·기준일 기준은 law_name(+as_of_date)으로 조회")
        xml, caution = self._version_xml(mst, ef_yd)
        name = re.search(r"<법령명_한글>(.*?)</법령명_한글>", xml, re.S)
        enf = re.search(r"<시행일자>(\d+)</시행일자>", xml)
        units = self._parse_addenda_units(xml)
        if not units:
            raise LawGoKrNotFound(f"MST {mst} 응답에 부칙 없음 — 응답 앞부분: {xml[:200]}")
        out = {
            "법령명": _strip_cdata(name.group(1)) if name else "",
            "MST": mst,
            "시행본_시행일자": enf.group(1) if enf else "",
            "부칙총수": len(units),
        }
        _add_caution(out, pre_caution)
        _add_caution(out, caution)
        # 조문별 시행일이 다른 개정이면 그 내역이 헤더에 요약돼 있다
        # (예: "20260701:제57조의2, … 20270101:제17조제3항, …") — 통합본에도 그 MST의 공포 당시
        # 일정이 그대로 실린다 (이미 반영된 부분도 남아 있음, 2026-10-09 실측)
        multi = re.search(r"<조문시행일자문자열>(.*?)</조문시행일자문자열>", xml, re.S)
        multi_txt = _strip_cdata(multi.group(1)) if multi else ""
        if multi_txt:
            out["조문별_상이한_시행일"] = multi_txt
        consolidated = bool(ef_yd) and not caution
        base = out["시행본_시행일자"] or ef_yd
        if consolidated:
            scope = (f"{base} 시행일 기준 통합본" + (" (시행 예정 시행본)" if base > _today_kst() else "")
                     + " — 그날까지 시행된 개정의 부칙만 들어 있음. 공포됐지만 아직 시행 전인 개정의 부칙은 "
                       "promul_no(공포번호)로 조회")
        else:
            scope = (f"MST {mst} 공포 당시 원문 — 이 공포본까지 공포된 개정의 부칙이며 그 뒤에 공포된 "
                     f"개정의 부칙은 없음. 현행·기준일 기준은 law_name(+as_of_date)으로 조회")

        if promul_no:
            want = promul_no.strip().lstrip("0") or promul_no.strip()
            hits = [u for u in units if u["공포번호"] == want]
            if not hits:
                found = self._addendum_from_promulgation(
                    law_name or out["법령명"], law_id or self._header_law_id(xml), want, history)
                if not found:
                    raise LawGoKrNotFound(
                        f"공포번호 {promul_no}의 부칙이 이 시행본에 없음 (부칙 {len(units)}건 보유) — "
                        f"law_history_search로 공포번호를 확인하세요")
                unit, rows = found
                hits = [unit]
                dates = sorted({r["시행일자"] for r in rows if r.get("시행일자")})
                # '아직 시행 전'은 기준일(as_of_date, 없으면 오늘)로 판정한다 — 시행본 시행일자로 판정하면 이미
                # 시행 중인 개정을 '시행 전'으로 안내한다 (2026-10-09 실측: mst=280405 + promul_no=21548 →
                # '20260101 현재 아직 시행 전', 실제는 2026.4.21부터 시행 중)
                if dates and all(d > ref for d in dates):
                    tail = f" — 이 개정은 {ref} 현재 아직 시행 전 (연혁상 시행일자 {', '.join(dates)})"
                elif dates and base and all(d > base for d in dates):
                    tail = (f" — 이 개정은 이 시행본({base}) 뒤에 시행됨 (연혁상 시행일자 {', '.join(dates)}); "
                            f"그 뒤 시행본 기준은 as_of_date를 시행일 이후로 지정")
                else:
                    tail = ""
                _add_caution(out, f"공포번호 {want}의 부칙은 이 시행본({base or 'MST ' + mst})에 없어 그 공포본"
                                  f"(MST {rows[0]['MST']}) 원문에서 가져옴" + tail)
            u = hits[-1]
            body = u["본문"]
            out["부칙"] = {"공포일자": u["공포일자"], "공포번호": u["공포번호"],
                          "본문": body[:max_chars]}
            out["부칙범위"] = scope
            if len(body) > max_chars:
                out["잘림"] = (f"이 부칙 전체 {len(body)}자 중 앞 {max_chars}자만 표시됨 — "
                             f"max_chars를 {len(body)} 이상으로 지정해 다시 조회")
            return out

        # 기준일 현재 공포됐지만 아직 시행 전인 개정 — 통합본 부칙에 없으므로 연혁에서 찾는다
        pending = []
        if consolidated:
            if history is None:
                history = self.law_history(law_name or out["법령명"], law_id=law_id or self._header_law_id(xml))
            pending = self._pending_promulgations(history, ref, {u["공포번호"] for u in units})

        if article_no:
            label = _article_label(article_no)
            pat = _article_pat(label)
            found, used = [], 0
            for u in reversed(units):  # 최신 개정부터
                paras = [p[:1500] for p in self._split_addendum(u["본문"]) if pat.search(p)]
                if not paras:
                    continue
                size = sum(len(p) for p in paras)
                if found and used + size > max_chars:
                    out["잘림"] = (f"{label} 언급 부칙이 더 있으나 max_chars({max_chars}) 초과로 "
                                 f"최신 {len(found)}건까지만 표시 — max_chars를 늘려 다시 조회")
                    break
                used += size
                found.append({"공포일자": u["공포일자"], "공포번호": u["공포번호"],
                              "해당항": paras})
            out["조문"] = label
            out["언급된_부칙_최신순"] = found
            out["부칙범위"] = scope
            pfound = []
            if pending:
                pfound, unchecked = self._pending_mentions(history, pending, pat, ref, max_chars)
                if pfound:
                    out["시행전_개정_부칙_언급"] = pfound
                    _add_caution(out, (
                        f"기준일({ref}) 현재 공포됐지만 아직 시행 전인 개정("
                        + ", ".join(f"제{p['공포번호']}호 {'·'.join(p['연혁상_시행일자'])} 시행" for p in pfound)
                        + f")의 부칙에도 {label} 언급이 있음 — 통합본 부칙에는 아직 없어 공포본 원문에서 "
                          f"'시행전_개정_부칙_언급'으로 발췌함"))
                if unchecked:
                    _add_caution(out, f"시행 전 개정 {', '.join('제' + n + '호' for n in unchecked)}의 부칙은 확인 못 함 "
                                      f"— promul_no로 직접 조회")
            if not found and not pfound:
                out["안내"] = (f"{label}이(가) 언급된 부칙 조항 없음"
                             + (f" (시행 전 개정 {len(pending)}건의 부칙 포함)" if pending else "")
                             + " — 그 조문 개정에 별도 적용례·경과조치가 없었다는 뜻일 수 있음 (이때 시행일은 "
                               "각 시행본의 시행일자·부칙 제1조를 따름)")
            return out

        rows = []
        for u in reversed(units):  # 최신순
            rows.append({"공포일자": u["공포일자"], "공포번호": u["공포번호"],
                         "시행일": self._effective_summary(u["본문"])})
            if len(rows) >= max(1, recent):
                break
        out["부칙목록_최신순"] = rows
        out["부칙범위"] = scope
        if pending:
            out["시행전_개정"] = [{k: p[k] for k in ("공포일자", "공포번호", "제개정구분", "연혁상_시행일자")}
                               for p in pending]
        if len(units) > len(rows) or pending:
            out["안내"] = ((f"전체 {len(units)}건 중 최신 {len(rows)}건만 표시 — recent를 늘리거나, "
                          if len(units) > len(rows) else "")
                         + "특정 개정의 부칙 전문은 promul_no로, 특정 조문의 적용시기는 article_no로 조회"
                         + (f". '시행전_개정'은 기준일({ref}) 현재 공포됐지만 아직 시행 전이라 이 통합본 부칙에 없는 "
                            f"개정 — 그 부칙은 promul_no로 조회" if pending else ""))
        return out

    @staticmethod
    def _header_law_id(xml: str) -> str:
        m = re.search(r"<법령ID>(\d+)</법령ID>", xml[:3000])
        return m.group(1) if m else ""

    @staticmethod
    def _row_date_for_mst(history, mst: str, ref: str) -> str:
        """연혁에서 그 MST 행 중 ref 이하 마지막 시행일자 (전부 ref 뒤면 가장 이른 행, 행이 없으면 "")."""
        rows = [r for r in history or [] if r.get("MST") == mst and r.get("시행일자")]
        if not rows:
            return ""
        elig = [r for r in rows if r["시행일자"] <= ref]
        return (elig[-1] if elig else rows[0])["시행일자"]

    @staticmethod
    def _last_promulgation(history, ref: str):
        """공포일자가 ref 이하인 마지막 공포본의 연혁 행 (없으면 None) — 기준일 당시 공포돼 있던 문안의 원천."""
        rows = [r for r in history or [] if r.get("공포일자") and r["공포일자"] <= ref and r.get("MST")]
        if not rows:
            return None
        return max(rows, key=lambda r: (r["공포일자"], (r.get("공포번호") or "").zfill(10)))

    @staticmethod
    def _pending_promulgations(history, ref: str, have):
        """ref 현재 공포됐지만 아직 시행 전인 개정 — 공포번호의 연혁 행이 모두 ref보다 늦게 시행되고 통합본 부칙
        (have: 공포번호 집합)에 없는 것. 최신 공포순."""
        by_no = {}
        for r in history or []:
            no = (r.get("공포번호") or "").lstrip("0")
            if no:
                by_no.setdefault(no, []).append(r)
        out = []
        for no, rows in by_no.items():
            dates = sorted({r["시행일자"] for r in rows if r.get("시행일자")})
            pubs = [r["공포일자"] for r in rows if r.get("공포일자")]
            if no in have or not dates or not pubs:
                continue
            if dates[0] > ref and min(pubs) <= ref:
                out.append({"공포일자": min(pubs), "공포번호": no, "제개정구분": rows[0].get("제개정구분", ""),
                            "연혁상_시행일자": dates, "MST": rows[0]["MST"]})
        out.sort(key=lambda p: (p["공포일자"], p["공포번호"].zfill(10)), reverse=True)
        return out

    def _pending_mentions(self, history, pending, pat, ref: str, max_chars: int):
        """시행 전 개정들의 부칙에서 조문 언급 항을 발췌 — 기준일 이전 마지막 공포본 원문 하나에 그때까지 공포된
        개정의 부칙이 다 들어 있다. 반환 (발췌 목록, 확인 못 한 공포번호 목록)."""
        latest = self._last_promulgation(history, ref)
        units = []
        if latest:
            try:
                units = self._parse_addenda_units(_get("lawService.do", target="law", MST=latest["MST"]))
            except requests.exceptions.HTTPError:
                units = []
        umap = {u["공포번호"]: u for u in units}
        found, unchecked, used = [], [], 0
        for p in pending:
            u = umap.get(p["공포번호"])
            if u is None:
                unchecked.append(p["공포번호"])
                continue
            paras = [x[:1500] for x in self._split_addendum(u["본문"]) if pat.search(x)]
            if not paras:
                continue
            size = sum(len(x) for x in paras)
            if found and used + size > max_chars:
                unchecked.append(p["공포번호"])
                continue
            used += size
            found.append({"공포일자": u["공포일자"], "공포번호": u["공포번호"],
                          "연혁상_시행일자": p["연혁상_시행일자"], "해당항": paras})
        return found, unchecked

    def _addendum_from_promulgation(self, law_name: str, law_id: str, promul_no: str, history=None):
        """공포번호의 부칙을 그 공포본(MST) 공포 당시 원문에서 찾는다 — 자기 부칙은 반드시 들어 있다.
        시행일 기준 통합본에는 그날까지 시행된 개정의 부칙만 있어, 공포됐지만 아직 시행 전인 개정은
        통합본에서 찾을 수 없다(2026-10-09 실측: 소득세법 20260701 통합본 부칙 115건 — 2027년 시행
        예정인 2023.7.18 타법개정 부칙이 없고, 같은 법 MST 285523 원문에는 있음).
        반환 (부칙 단위, 그 공포번호의 연혁 행 목록) 또는 None."""
        if not law_name:
            return None
        if history is None:
            history = self.law_history(law_name, law_id=law_id)
        rows = [r for r in history if (r.get("공포번호") or "").lstrip("0") == promul_no]
        if not rows:
            return None
        units = self._parse_addenda_units(_get("lawService.do", target="law", MST=rows[0]["MST"]))
        unit = next((u for u in reversed(units) if u["공포번호"] == promul_no), None)
        return (unit, rows) if unit else None

    # ---------- 제정·개정이유 ----------
    @staticmethod
    def _cdata_block(xml: str, tag: str) -> str:
        """<tag>의 CDATA 조각들을 이어 글로. 조각 사이 빈 줄은 하나로 줄인다."""
        m = re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.S)
        if not m:
            return ""
        text = "".join(re.findall(r"<!\[CDATA\[(.*?)\]\]>", m.group(1), re.S)) or m.group(1)
        text = html.unescape(text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        return re.sub(r"\n{2,}", "\n", text).strip()

    def _resolve_law_id(self, law_name: str) -> str:
        """법령명과 정확히 일치하는 현행 법령의 법령ID — 같은 이름의 시행령·시행규칙 혼입 방지."""
        rows = self.search_laws(law_name)
        exact = [r for r in rows if r.get("법령명") == law_name.strip()]
        return exact[0]["법령ID"] if exact else ""

    def law_revision_reason(self, law_name: str, law_id: str = "", promul_no: str = "",
                            year: str = "", include_amendment_text: bool = False,
                            max_chars: int = 6000):
        """법령 제정·개정이유 — 각 시행본 원문(lawService target=law&MST)에 딸린
        <제개정이유>(◇개정이유 / ◇주요내용)를 꺼낸다. 국가법령정보센터 화면의
        '제정·개정이유' 탭과 같은 자료다.

        - promul_no: 공포번호 (쉼표로 여러 개, 예: "4981,10410"). "제정"도 받는다.
        - year: 공포연도 (쉼표로 여러 개, 예: "2011,2019") — 그해 공포된 개정 전부
        - 둘 다 없으면: 개정 목록(공포일자·공포번호·제개정구분)만 최신순으로 반환
        """
        if not law_name:
            raise LawGoKrError("law_name은 필수입니다")
        law_id = law_id or self._resolve_law_id(law_name)
        history = self.law_history(law_name, law_id=law_id)
        if not law_id:  # 법령ID를 못 정하면 이름으로라도 본법만 남긴다
            history = [r for r in history
                       if r["법령명"].replace(" ", "") == law_name.replace(" ", "")]
        if not history:
            raise LawGoKrNotFound(f"연혁 시행본 없음: {law_name}")

        # 한 공포본이 조문별 단계 시행 때문에 여러 시행본 행으로 반복된다 — 공포번호당 하나
        by_no = {}
        for r in history:  # 시행일자 오름차순 → 가장 이른 시행본이 남는다
            by_no.setdefault((r["공포번호"] or "").lstrip("0"), r)
        acts = sorted(by_no.values(), key=lambda r: (r["공포일자"], (r["공포번호"] or "").zfill(10)))

        want_nos = [p.strip() for p in str(promul_no).split(",") if p.strip()]
        want_years = [y.strip() for y in str(year).split(",") if y.strip()]
        out = {"법령명": acts[-1]["법령명"], "법령ID": law_id or acts[-1]["법령ID"],
               "개정횟수": len(acts)}

        if not want_nos and not want_years:
            out["개정목록_최신순"] = [
                {"공포일자": r["공포일자"], "공포번호": (r["공포번호"] or "").lstrip("0"),
                 "제개정구분": r["제개정구분"], "법령명": r["법령명"]}
                for r in reversed(acts)]
            out["안내"] = ("개정이유 본문은 promul_no(공포번호, '제정' 가능) 또는 year(공포연도)를 "
                         "지정해 다시 조회하세요. 쉼표로 여러 개를 한 번에 받을 수 있습니다.")
            return out

        chosen, missing = [], []
        for p in want_nos:
            if p == "제정":
                hit = [r for r in acts if r["제개정구분"] == "제정"] or acts[:1]
            else:
                hit = [r for r in acts if (r["공포번호"] or "").lstrip("0") == p.lstrip("0")]
            (chosen.extend(hit) if hit else missing.append(f"공포번호 {p}"))
        for y in want_years:
            if not re.match(r"^\d{4}$", y):
                raise LawGoKrError(f"year 형식 오류: '{y}' (YYYY)")
            hit = [r for r in acts if r["공포일자"].startswith(y)]
            (chosen.extend(hit) if hit else missing.append(f"{y}년"))
        seen, picked = set(), []
        for r in chosen:
            if r["MST"] not in seen:
                seen.add(r["MST"])
                picked.append(r)
        picked.sort(key=lambda r: (r["공포일자"], (r["공포번호"] or "").zfill(10)))
        if len(picked) > 10:
            raise LawGoKrError(f"해당 개정이 {len(picked)}건이라 너무 많습니다 — 10건 이하로 "
                               f"좁혀 주세요 (공포번호로 지정 권장)")

        rows = []
        for r in picked:
            # 개정이유·개정문은 공포본(MST) 자체의 것이므로 시행일 기준 통합본이 아니라 공포 당시 원문에서 읽는다
            xml = _get("lawService.do", target="law", MST=r["MST"])
            reason = self._cdata_block(xml, "제개정이유내용")
            row = {"공포일자": r["공포일자"], "공포번호": (r["공포번호"] or "").lstrip("0"),
                   "제개정구분": r["제개정구분"], "시행일자": r["시행일자"],
                   "법령명": r["법령명"], "MST": r["MST"]}
            amend = self._cdata_block(xml, "개정문내용")
            if r["제개정구분"] == "타법개정":
                other = re.search(r"⊙\S+ 제\d+호(?:\([\d.]+\))?\s+(.+?)\s*(?:\n|$)", amend)
                row["주의"] = ("다른 법령의 제정·개정에 딸려 고쳐진 개정(타법개정)입니다. 아래 "
                             "개정이유는 그 법령" + (f"({other.group(1).strip()})" if other else "")
                             + "의 것이며, 이 법령이 어떻게 바뀌었는지는 개정문에 있습니다.")
            row["개정이유"] = reason[:max_chars] if reason else ""
            if not reason:
                row["비고"] = "원천(법제처)에 이 개정의 제·개정이유가 수록되어 있지 않습니다."
            elif len(reason) > max_chars:
                row["잘림"] = (f"개정이유 전체 {len(reason)}자 중 앞 {max_chars}자만 표시됨 — "
                             f"max_chars를 {len(reason)} 이상으로 지정해 다시 조회")
            if include_amendment_text or r["제개정구분"] == "타법개정":
                # 타법개정은 개정문(이 법의 어느 조문을 어떻게 고쳤는지)이 사실상 유일한 설명
                row["개정문"] = amend[:max_chars]
                if len(amend) > max_chars:
                    row["개정문_잘림"] = f"개정문 전체 {len(amend)}자 중 앞 {max_chars}자만 표시됨"
            rows.append(row)
        out["개정이유"] = rows
        if missing:
            out["찾지못함"] = missing
        if not rows:
            raise LawGoKrNotFound(f"지정한 개정을 연혁에서 찾지 못함: {', '.join(missing)} — "
                                  f"promul_no·year 없이 호출하면 개정 목록을 볼 수 있습니다")
        return out

    # ---------- 행정규칙 (훈령·예규·고시·기본통칙) ----------
    def search_admin_rules(self, keyword: str, display: int = 10, page: int = 1):
        """target=admrul 행정규칙 검색 — 기본통칙·조사사무처리규정·고시 등."""
        xml = _get("lawSearch.do", target="admrul", query=keyword, display=display, page=page)
        items = _parse_items(xml, "admrul")
        return [{
            "일련번호": it.get("행정규칙일련번호", ""),
            "행정규칙명": it.get("행정규칙명", ""),
            "종류": it.get("행정규칙종류", ""),
            "소관부처": it.get("소관부처명", ""),
            "발령일자": it.get("발령일자", ""),
            "발령번호": it.get("발령번호", ""),
            "시행일자": it.get("시행일자", ""),
        } for it in items]

    def get_admin_rule(self, serial: str, max_chars: int = 10000,
                       article: str = "", start_char: int = 0):
        """target=admrul 행정규칙 본문.
        article: 조번호를 주면 해당 조문만 슬라이스 — "9-5"(외국환거래규정식 제9-5조),
        "23", "23의2" 형식. 외국환거래규정(30만 자) 같은 대형 고시는 전문 반환이
        불가능하므로 조번호 지정이 사실상 필수. start_char: 조번호를 모를 때
        해당 위치부터 max_chars만큼 이어 읽는 오프셋."""
        xml = _get("lawService.do", target="admrul", ID=serial)
        name = re.search(r"<행정규칙명>(.*?)</행정규칙명>", xml, re.S)
        body = _strip_tags(_strip_cdata(xml))
        if len(body) < 50:
            return {"오류": "본문 없음", "응답": xml[:200]}
        out = {
            "행정규칙명": _strip_cdata(name.group(1)) if name else "",
            "본문길이": len(body),
        }
        if article:
            a = article.strip()
            am = re.match(r"^([\d-]+)(의\d+)?$", a)
            base, ui = (am.group(1), am.group(2) or "") if am else (a, "")
            # 조문 표제는 행 첫머리 "제9-5조(제목)" 형태 — 본문 중 인용("제7-31조의 규정 …")과
            # 행 앵커로 구분한다. 표제가 행 중간에 오는 규칙이면 못 찾으므로 start_char로 폴백.
            m = re.search(rf"(?m)^제{re.escape(base)}조{ui}(?=\(|\s|$)", body)
            if not m:
                out["오류"] = (f"제{a}조 표제를 찾지 못함 — 조번호 형식(예: '9-5', '23', '23의2')을 "
                             f"확인하거나 start_char 오프셋으로 조회")
                return out
            nm = re.search(r"(?m)^제[\d-]+조(?:의\d+)?\s*(?=\()", body[m.end():])
            seg = body[m.start():m.end() + nm.start()] if nm else body[m.start():]
            out["조문"] = f"제{a}조"
            out["본문"] = seg[:max_chars]
            if len(seg) > max_chars:
                out["잘림"] = (f"이 조문 전체 {len(seg)}자 중 앞 {max_chars}자만 표시됨 — "
                             f"max_chars를 {len(seg)} 이상으로 지정해 다시 조회")
            return out
        window = body[start_char:start_char + max_chars]
        out["본문"] = window
        if start_char + len(window) < len(body):
            out["잘림"] = (f"전체 {len(body)}자 중 {start_char}~{start_char + len(window)}자 구간만 표시됨 — "
                         f"start_char={start_char + len(window)}로 이어서 조회하거나, "
                         f"article에 조번호(예: '9-5')를 지정하면 해당 조문만 반환")
        return out

    # ---------- 조세조약 등 조약 ----------
    def search_treaties(self, keyword: str, display: int = 10, page: int = 1):
        """target=trty 조약 검색 — 조세조약 원문·발효일 확인용."""
        xml = _get("lawSearch.do", target="trty", query=keyword, display=display, page=page)
        items = _parse_items(xml, "Trty") + _parse_items(xml, "trty")
        return [{
            "조약일련번호": it.get("조약일련번호", ""),
            "조약명": it.get("조약명한글", it.get("조약명", "")),
            "조약구분": it.get("조약구분명", ""),
            "서명일자": it.get("서명일자", ""),
            "발효일자": it.get("발효일자", ""),
        } for it in items]

    def get_treaty(self, serial: str, max_chars: int = 12000):
        """target=trty 조약 본문."""
        xml = _get("lawService.do", target="trty", ID=serial)
        name = re.search(r"<조약명한글>(.*?)</조약명한글>", xml, re.S)
        body = _strip_tags(_strip_cdata(xml))
        if len(body) < 50:
            return {"오류": "본문 없음", "응답": xml[:200]}
        return {
            "조약명": _strip_cdata(name.group(1)) if name else "",
            "본문": body[:max_chars],
            "본문길이": len(body),
        }

    # ---------- 자치법규 (조례·규칙) ----------
    def search_ordinances(self, keyword: str, region: str = "", display: int = 20, page: int = 1):
        """target=ordin 자치법규 검색. region으로 지자체명 필터 (예: '서울', '용산구')."""
        xml = _get("lawSearch.do", target="ordin", query=keyword, display=display, page=page)
        items = _parse_items(xml, "law") + _parse_items(xml, "ordin")
        rows = []
        for it in items:
            org = it.get("지자체기관명", it.get("소관부처명", ""))
            if region and region not in org:
                continue
            rows.append({
                "일련번호": it.get("자치법규일련번호", it.get("법령일련번호", "")),
                "자치법규명": it.get("자치법규명", it.get("법령명한글", "")),
                "지자체": org,
                "공포일자": it.get("공포일자", ""),
                "시행일자": it.get("시행일자", ""),
                "제개정구분": it.get("제개정구분명", ""),
            })
        return rows

    def get_ordinance(self, serial: str, max_chars: int = 10000):
        """target=ordin 자치법규 본문."""
        xml = _get("lawService.do", target="ordin", ID=serial)
        name = re.search(r"<자치법규명>(.*?)</자치법규명>", xml, re.S)
        body = _strip_tags(_strip_cdata(xml))
        if len(body) < 50:
            return {"오류": "본문 없음", "응답": xml[:200]}
        return {
            "자치법규명": _strip_cdata(name.group(1)) if name else "",
            "본문": body[:max_chars],
            "본문길이": len(body),
        }

    # ---------- 편의: 특정 날짜 시행본의 조문 (예규 당시 조문 확인용) ----------
    _STAGED_PAST_CAUTION = (
        "과거 기준일 조회 — 법제처 과거 통합본의 [시행일] 표시에는 그 뒤 부칙 개정으로 바뀐 시행 일정이 실려 있을 수 "
        "있음 (2026-10-09 실측: 소득세법 제81조의11 20230101 통합본 표시는 2026.1.1, 기준일 당시 부칙 제19196호는 "
        "2024.1.1). 기준일 당시 일정은 그 개정의 부칙(law_addenda_search promul_no)으로 확인")

    def law_article_as_of(self, law_name: str, as_of_date: str, article_no: str, law_id: str = "",
                          max_chars: int = 6000):
        """as_of_date(YYYYMMDD) 당시 시행 중이던 시행본을 골라 해당 조문 원문 반환.
        예규·판례가 인용한 '당시 조문' 검증용 — 회신일을 넣으면 그 시점 법을 준다.

        시행본 = 시행일자가 기준일 이하인 마지막 연혁 행, 문안 = 그 행의 시행일 기준 통합본.
        과거 기준일(오늘 이전)이면 통합본 시대착오를 점검해 그 시점 공포 문안을 '공포본_참고'로 함께 준다
        (_check_promulgated). 현행 조회(오늘 이후)는 점검하지 않는다 — 추가 왕복 없음."""
        if not re.match(r"^\d{8}$", str(as_of_date or "")):
            raise LawGoKrError(f"as_of_date 형식 오류: '{as_of_date}' (YYYYMMDD 8자리)")
        article_no = _norm_article_no(article_no)
        history = self.law_history(law_name, law_id=law_id)
        if not history:
            raise LawGoKrNotFound(f"연혁 없음: {law_name}")
        if not law_id:
            _require_exact_name(history, law_name)
        cands = [r for r in history if r.get("시행일자") and r["시행일자"] <= as_of_date]
        if not cands:
            first = history[0]
            kind = first.get("제개정구분") or ""
            hint = ("" if kind == "제정" else
                    f" — 연혁 첫 행이 제정이 아니라 '{kind or '개정'}'이라 그 전 시행본이 옛 법령명·옛 법령ID로 따로 "
                    f"있을 수 있음 (이름 검색 연혁에는 개명 전 시행본이 빠짐 — 옛 법령명으로 다시 조회하거나 "
                    f"law_history_search로 확인)")
            raise LawGoKrNotFound(f"{as_of_date} 이전 시행본 없음 (연혁의 최초 시행 {first['시행일자']}"
                                  f"{' ' + kind if kind else ''})" + hint)
        chosen = cands[-1]
        # 같은 시행일자의 다른 MST — 통합본 문안이 같아 통합본 조회가 실패하면 대신 시도한다
        alt = [r["MST"] for r in reversed(cands) if r["시행일자"] == chosen["시행일자"] and r["MST"] != chosen["MST"]]
        past = as_of_date < _today_kst()
        try:
            art = self.law_article(chosen["MST"], article_no, max_chars, ef_yd=chosen["시행일자"],
                                   as_of=as_of_date, alt_msts=alt)
        except LawGoKrNotFound:
            if not past:
                raise
            art = None   # 과거 통합본에 없는 조문 — 공포본 대조 전에는 부존재로 단정하지 않는다
        if past:
            art = self._check_promulgated(art, history, chosen, as_of_date, article_no, max_chars)
            if art.get("시행예정_부분"):
                _add_caution(art, self._STAGED_PAST_CAUTION)
        art["적용시행본"] = {k: chosen[k] for k in ("법령명", "시행일자", "공포일자", "공포번호", "제개정구분", "MST")}
        art_date = art.get("조문시행일자", "")
        if art_date and art_date > as_of_date:  # 공포 당시 원문으로 폴백했을 때만 이 태그가 남는다
            _add_caution(art, f"이 시행본에 수록된 문안의 조문시행일자({art_date})가 기준일"
                              f"({as_of_date})보다 미래입니다 — 기준일 당시 시행 중이던 문안을 "
                              f"찾지 못했을 수 있으니 law_article_diff로 개정 시점을 확인하세요")
        return art

    def _check_promulgated(self, art, history, chosen: dict, as_of: str, article_no: str, max_chars: int):
        """과거 기준일 조회의 통합본 시대착오 점검. 법제처 과거 통합본은 시행 전 규정이 뒤에 다시 개정되거나
        폐지되면 그 결과를 소급해 싣는다 (2026-10-09 실측: 소득세법 제81조의11 20240101·20250101 통합본과
        법인세법 제75조의7 20250101 통합본에 2025.12.23 개정 문안 '2027년 1월 1일부터 …'(〈신설 …, 2025.12.23〉)이
        들어 있고, 그 시점 공포본(MST 258015·267581·267551)은 '2026년 1월 1일부터 …'). 그래서 기준일 이전 마지막
        공포본(target=law)의 같은 조문을 '공포본_참고'로 함께 준다:
          - 통합본 조문의 개정 주석에 기준일 뒤 날짜가 있으면 → '주의' + (문안이 다르면) 공포본_참고
          - 통합본에 조문이 없거나 '삭제'인데 공포본에는 있으면 → 공포본_참고. 기준일 당시 공포됐지만 시행 전이던
            규정이다 (소득세법 제87조의18(금융투자소득 기본공제) 20240101: 통합본에 없고 2023.12.31 공포본에 있음,
            제87조의2: 통합본 '삭제 〈2010.12.27〉', 공포본은 금융투자소득 정의 조문 — 2024.12.31 제20615호로 폐지)
        과거 통합본만 보고 NOT_FOUND(부존재 단정 가능)를 내지 않는다. art가 None이면 통합본에 조문이 없던 경우.
        반환: 응답 dict (공포본에도 없으면 LawGoKrNotFound)."""
        xml, caution = self._version_xml(chosen["MST"], chosen["시행일자"])
        if caution:   # 이미 공포 당시 원문으로 폴백 — 대조할 다른 원천이 없다
            return art
        label, block = self._find_article_block(xml, article_no)
        late, deleted = [], False
        if block is not None:
            late = sorted(d for d in self._block_note_dates(block) if d > as_of)
            deleted = self._is_deleted(block, label)
            if not late and not deleted:
                return art
        prom = self._last_promulgation(history, as_of)
        pblock, pxml, fetch_err = None, "", ""
        if prom:
            try:
                pxml = _get("lawService.do", target="law", MST=prom["MST"])
                _l, pblock = self._find_article_block(pxml, article_no)
            except requests.exceptions.HTTPError as e:   # 접속 실패·인증 실패는 그대로 올린다
                fetch_err = f"HTTP {getattr(e.response, 'status_code', '')}".strip()
        where = (f"기준일 이전 마지막 공포본(MST {prom['MST']}, {prom['공포일자']} 공포 "
                 f"제{(prom.get('공포번호') or '').lstrip('0')}호)" if prom else "기준일 이전 공포본")
        if block is None:
            if pblock is None:
                if fetch_err:
                    raise LawGoKrUpstreamError(
                        f"{as_of} 당시 시행본({chosen['시행일자']} 시행 통합본)에 {label} 조문이 없고 {where} 조회도 "
                        f"실패({fetch_err}) — 조문 부존재로 단정할 수 없음")
                raise LawGoKrNotFound(f"{as_of} 당시 시행본({chosen['시행일자']} 시행 통합본)에도 {where}에도 "
                                      f"{label} 조문이 없음 — 그때 없던 조문이거나 조번호 오류")
            if self._is_deleted(pblock, label):
                plines = self._article_content_lines(pblock)
                raise LawGoKrNotFound(f"{label} 조문은 기준일({as_of}) 당시 삭제된 조문 — 시행일 기준 통합본에 없고 {where}에는 "
                                      f"'{plines[0][:80]}'")
            ptitle = re.search(r"<조문제목>(.*?)</조문제목>", pblock, re.S)
            out = {"조문": label, "조문제목": _strip_cdata(ptitle.group(1)) if ptitle else "", "원문": "",
                   "통합본_문안_없음": (f"{chosen['시행일자']} 시행일 기준 통합본에 {label} 조문이 없음 — 기준일({as_of}) "
                                    f"당시 시행 중인 문안이 아니었음")}
            out["공포본_참고"] = self._prom_ref(prom, pblock, as_of, max_chars)
            _add_caution(out, (f"기준일({as_of}) 당시 시행 중인 {label} 조문은 없음(시행일 기준 통합본). 다만 {where} 원문에는 "
                               f"이 조문이 있어 '공포본_참고'로 함께 줌 — 기준일 당시 공포됐지만 아직 시행 전이었거나, 그 뒤 "
                               f"시행 전에 폐지·개정되어 과거 통합본에서 빠진 규정일 수 있음. 시행일·경과는 "
                               f"law_addenda_search(promul_no·article_no)로 확인"))
            return out
        differs = pblock is not None and (_norm_key(self._article_content_lines(pblock))
                                          != _norm_key(self._article_content_lines(block)))
        if differs:
            art["공포본_참고"] = self._prom_ref(prom, pblock, as_of, max_chars)
        if late:
            msg = (f"이 조문 통합본 문안에 기준일({as_of}) 뒤 날짜의 개정 주석({', '.join(late)})이 있음 — 법제처 과거 "
                   f"통합본은 시행 전 규정이 뒤에 다시 개정되면 그 결과를 소급해 싣기도 함. ")
            if differs:
                msg += (f"{where} 문안을 '공포본_참고'로 함께 줌 — 기준일 당시 공포돼 있던 문안은 그쪽이며, 차이 나는 "
                        f"부분은 기준일 뒤 개정분일 수 있음 (시행예정_부분의 시행일도 그 뒤 바뀐 일정일 수 있음)")
            elif pblock is not None:
                msg += f"다만 {where}의 이 조문 문안과는 같음"
            elif fetch_err:
                msg += f"{where} 대조는 확인 못 함 (조회 실패 {fetch_err})"
            else:
                msg += f"{where}에는 이 조문이 없음"
            _add_caution(art, msg)
        elif deleted and differs:
            _add_caution(art, (f"시행일 기준 통합본(기준일 당시 시행 문안)은 '{label} 삭제'이나 {where}에는 다른 문안이 "
                               f"있어 '공포본_참고'로 함께 줌 — 기준일 당시 공포됐지만 아직 시행 전이던 규정일 수 있음 (그 뒤 "
                               f"시행 전에 폐지·개정되면 과거 통합본에 실리지 않음)"))
        elif deleted and fetch_err:
            _add_caution(art, f"이 조문은 통합본에서 '삭제' — {where} 대조는 확인 못 함 (조회 실패 {fetch_err})")
        return art

    def _prom_ref(self, prom: dict, pblock: str, as_of: str, max_chars: int) -> dict:
        """'공포본_참고' — 기준일 이전 마지막 공포본(공포 당시 원문)의 같은 조문."""
        body = self._block_text(pblock)
        title = re.search(r"<조문제목>(.*?)</조문제목>", pblock, re.S)
        ref = {"MST": prom["MST"], "공포일자": prom["공포일자"],
               "공포번호": (prom.get("공포번호") or "").lstrip("0"), "제개정구분": prom.get("제개정구분", ""),
               "조문제목": _strip_cdata(title.group(1)) if title else "", "원문": body[:max_chars]}
        if len(body) > max_chars:
            ref["잘림"] = f"공포본 조문 전체 {len(body)}자 중 앞 {max_chars}자만 표시됨 — max_chars를 늘려 다시 조회"
        staged = [s for s in _staged_notes(body) if s["시행일자"] > as_of]
        if staged:
            ref["시행예정_부분"] = staged
        return ref

    @classmethod
    def _block_note_dates(cls, block: str):
        """조문 블록의 개정 주석 날짜(YYYYMMDD) 집합 — 〈개정 …〉·〈신설 …〉·삭제 〈…〉와 [본조신설 …]·[전문개정 …]
        ·[종전 …] 등 (시행일 표시·결정 주석 제외)."""
        t = cls._block_text(block)
        out = set()
        for note in re.findall(r"〈[^〉]*〉", t) + _BRACKET_NOTE_RE.findall(t):
            for y, m, d in cls._NOTE_DATE_RE.findall(note):
                out.add(f"{y}{int(m):02d}{int(d):02d}")
        return out

    @classmethod
    def _is_deleted(cls, block: str, label: str) -> bool:
        """조문이 '제N조 삭제 〈…〉' 꼴인지."""
        lines = cls._article_content_lines(block)
        return bool(lines) and bool(re.match(re.escape(label) + r"\s*(?:\([^)]*\))?\s*삭제", lines[0]))

    # ---------- 조문 개정 diff (신구조문 대비) ----------
    _CONTENT_TAG_RE = re.compile(r"<(조문내용|항내용|호내용|목내용)>(.*?)</\1>", re.S)
    _NOTE_DATE_RE = re.compile(r"(\d{4})\.\s?(\d{1,2})\.\s?(\d{1,2})")
    _ATTRIBUTE_MAX = 4  # 같은 시행일자 공포본 중 개정문을 대조할 상한 (시행본 XML 추가 왕복 제한)

    def _article_state(self, mst: str, article_no: str, ef_yd: str = ""):
        """(문안 줄 목록 or None, 조문시행일자, 주의 문구, [시행일] 표시 목록) — 조문이 없으면
        (None, "", 주의, []). 시행본 행(MST + 시행일자)의 시행일 기준 통합본을 본다."""
        xml, caution = self._version_xml(mst, ef_yd)
        _label, block = self._find_article_block(xml, article_no)
        if block is None:
            return None, "", caution, []
        em = re.search(r"<조문시행일자>(\d+)</조문시행일자>", block)
        return (self._article_content_lines(block), (em.group(1) if em else ""), caution,
                _staged_notes(self._block_text(block)))

    def _pick_effective_version(self, history, date: str, article_no: str, state_fn=None):
        """기준일에 시행 중이던 시행본(연혁 행 = MST + 시행일자)을 고르고 그 조문 상태를 함께 준다.

        시행본 문안이 시행일 기준 통합본이므로 '시행일자가 기준일 이하인 마지막 행'의 문안이
        곧 기준일 당시 시행 중이던 문안이다. 같은 MST라도 시행일자 행마다 문안이 다르므로
        상태는 (MST, 시행일자)로 구분한다 — state_fn(행) → (문안 줄 or None, 조문시행일자[, 주의]).

        예전에는 MST 공포 당시 원문(target=law)을 써서, 조문시행일자가 기준일보다 미래면 이전
        시행본으로 소급(_BACKTRACK, 최대 8행)했다. 통합본에서는 필요 없어 뺐다 (2026-10-09 실측):
          - 통합본 조문의 <조문시행일자>는 그 시행본의 시행일자(efYd)다 — 세법·노동법 7개 법령의
            1949~2027년 시행본 70개 전 조문에서 efYd보다 늦은 조문 0개(69개는 전 조문이 efYd, 조특법
            20060101 통합본만 3개 조문이 더 이른 날짜)라 소급 조건이 생기지 않는다
          - 통합본은 단계 시행을 날짜대로 반영한다 (근로기준법 MST 285279: 20261008 통합본의
            제101조는 시행 전 문안이 아니라 종전 문안, 20261208 통합본부터 개정 문안,
            2027.1.1 신설 제44조의4는 20270101 통합본에만 있음)
          - 헤더 <조문시행일자문자열>은 그 MST의 공포 당시 일정이 통합본에도 그대로 실려 기준으로
            쓸 수 없다 — 이것으로 소급하면 같은 조문의 이미 시행된 개정까지 잃는다
            (근로기준법 제116조: 헤더상 20270101이지만 20261008 시행분 개정이 들어 있음)
          - target=law 원문도 조문별 <조문시행일자>가 모두 헤더 시행일자라 옛 소급은 실제로
            발동하지 않았다 (소득세법 MST 280405 326개 조문 전부 20260101)
        조문 일부(항·호)만 늦게 시행되는 경우는 통합본이 '[시행일: …]' 표시로 알려 주므로
        law_article이 '시행예정_부분'·'주의'로 전달한다.

        반환: (선택된 연혁 행, 문안 줄 목록 or None, 조문시행일자). 후보가 없으면
        (None, None, ""). 조문 자체가 없는 시점이면 문안이 None (= 신설 전)."""
        get_state = state_fn or (lambda row: self._article_state(row["MST"], article_no, row["시행일자"]))
        cands = [r for r in history if r.get("시행일자") and r["시행일자"] <= date]
        if not cands:
            return None, None, ""
        row = cands[-1]
        st = get_state(row)
        return row, st[0], st[1]

    @classmethod
    def _article_content_lines(cls, block: str):
        """diff 비교용: 조문 블록에서 실제 문안(조문내용·항·호·목)만 줄 단위로 추출.
        <조문키>·<조문시행일자> 같은 메타데이터와 번호 태그 중복(①/① 본문)을 제외해야
        문안이 같은데 메타데이터만 다른 시행본이 '변경'으로 오판되지 않는다."""
        lines = []
        for _tag, val in cls._CONTENT_TAG_RE.findall(block):
            # "<개정 2024.12.31>"·"삭제 <2017.12.19>" 류 주석이 태그 제거에 지워지지 않게 보존 — 개정 이력 자체가 유용한 정보
            t = _strip_tags(_keep_notes(_strip_cdata(val)))
            for ln in t.splitlines():
                ln = ln.strip()
                if ln:
                    lines.append(ln)
        return lines

    def law_article_diff(self, law_name: str, article_no: str, date_from: str,
                         date_to: str = "", law_id: str = "", max_chars: int = 8000,
                         find_change: bool = True):
        """두 시점의 조문 문안을 비교(신구조문 대비)하고, 현행(B) 문안이 언제부터
        시행됐는지와 그 개정의 부칙(시행일·적용례)까지 연결해 반환한다.

        - date_from(A)·date_to(B, 생략 시 오늘) 각각의 시행본을 자동 선택해 조문을 대비
        - find_change=True면 A~B 사이 시행본을 이진탐색해 B 문안이 최초로 등장한
          시행본을 특정하고, 그 개정 부칙에서 이 조문이 언급된 적용례를 발췌
        - 같음 판정은 표 괘선·전각 기호·공백을 무시한 키로 한다 (서식만 다르면 '변경 없음(서식 차이)')
        - A·B 문안의 [시행일] 표시는 각 기준일보다 늦은 것만 비교기준.A/B.시행예정_부분으로 알린다
        """
        if not date_from:
            raise LawGoKrError("date_from은 필수입니다 (YYYYMMDD 8자리)")
        for d, nm in ((date_from, "date_from"), (date_to, "date_to")):
            if d and not re.match(r"^\d{8}$", str(d)):
                raise LawGoKrError(f"{nm} 형식 오류: '{d}' (YYYYMMDD 8자리)")
        article_no = _norm_article_no(article_no)
        if not date_to:
            date_to = _today_kst()
        if date_from >= date_to:
            raise LawGoKrError(f"date_from({date_from})은 date_to({date_to})보다 앞선 날짜여야 합니다")

        history = self.law_history(law_name, law_id=law_id)
        if not history:
            raise LawGoKrNotFound(f"연혁 없음: {law_name}")
        if not law_id:
            _require_exact_name(history, law_name)

        # (MST, 시행일자) -> (문안 줄 목록 or None, 조문시행일자, [시행일] 표시) — 통합본은 같은 MST라도
        # 시행일자 행마다 문안이 다르므로 MST만으로 캐싱하면 안 된다
        state_cache = {}
        fallbacks = []  # 통합본 조회에 실패해 공포 당시 원문으로 대신 본 시행본

        def state(row):
            key = (row["MST"], row["시행일자"])
            if key not in state_cache:
                lines, art_date, caution, marks = self._article_state(row["MST"], article_no, row["시행일자"])
                if caution:
                    fallbacks.append(f"{row['시행일자']}(MST {row['MST']})")
                state_cache[key] = (lines, art_date, marks)
            return state_cache[key]

        va, lines_a, _ = self._pick_effective_version(history, date_from, article_no, state)
        if not va:
            raise LawGoKrNotFound(f"{date_from} 이전 시행본 없음 (최초 시행 {history[0]['시행일자']})")
        vb, lines_b, _ = self._pick_effective_version(history, date_to, article_no, state)
        if not vb:
            raise LawGoKrNotFound(f"{date_to} 이전 시행본 없음: {law_name}")

        def meta(v):
            return {k: v[k] for k in ("법령명", "시행일자", "공포일자", "공포번호", "제개정구분", "MST")}

        label = _article_label(article_no)
        out = {
            "법령명": va["법령명"],
            "조문": label,
            "비교기준": {
                "A": {"기준일": date_from, "시행본": meta(va)},
                "B": {"기준일": date_to, "시행본": meta(vb)},
            },
        }
        # 통합본은 조문 일부만 늦게 시행되면 그 문안을 미리 싣는다 — diff에도 표시해야 B의 미시행 부분이
        # 현행 문안처럼 읽히지 않는다 (2026-10-09 실측: 소득세법 제14조 20250101→20261009 '변경 없음'만 나오고
        # 제14조제3항제8호다목(2027.1.1)을 언급하지 않음, 제81조의11 B 문안 ③1호는 [시행일: 2027.1.1])
        staged_a = [s for s in state(va)[2] if s["시행일자"] > date_from]
        staged_b = [s for s in state(vb)[2] if s["시행일자"] > date_to]
        if staged_a:
            out["비교기준"]["A"]["시행예정_부분"] = staged_a
        if staged_b:
            out["비교기준"]["B"]["시행예정_부분"] = staged_b
        today = _today_kst()

        def done():
            if staged_b:
                _add_caution(out, "B 시점 " + _staged_caution(staged_b))
            late_a = sorted(d for d in self._note_dates(lines_a) if d > date_from) if date_from < today else []
            if late_a:
                _add_caution(out, (f"A 시점 문안에 기준일({date_from}) 뒤 날짜의 개정 주석({', '.join(late_a)})이 있음 — "
                                   f"법제처 과거 통합본은 시행 전 규정이 뒤에 다시 개정되면 그 결과를 소급해 싣기도 하므로 "
                                   f"A 당시 공포 문안은 law_article_as_of(기준일 {date_from})의 '공포본_참고'로 확인"))
            if fallbacks:
                _add_caution(out, self._fallback_caution(fallbacks))
            return out

        if lines_a is None and lines_b is None:
            if fallbacks:   # 대체 원문에 없다고 실제 시행본에 없는 것은 아니다 — 부존재로 단정하지 않는다
                raise LawGoKrUpstreamError(f"{label}: A·B 두 시점 모두 조문을 찾지 못했으나 "
                                           + self._fallback_caution(fallbacks) + " — 조문 부존재로 단정할 수 없음")
            raise LawGoKrNotFound(f"{label}: A·B 두 시점 모두 조문이 존재하지 않음 (조번호 확인)")
        if (va["MST"], va["시행일자"]) == (vb["MST"], vb["시행일자"]):
            out["변경여부"] = False
            out["안내"] = ("두 기준일에 같은 조문 문안이 시행 중이었음 — "
                          "이 구간에 이 조문의 개정 시행이 없었음")
            return done()
        key_b = _norm_key(lines_b)
        if lines_a == lines_b or _norm_key(lines_a) == key_b:
            out["변경여부"] = False
            if lines_a == lines_b:
                out["안내"] = ("시행본은 다르지만 이 조문의 문안은 동일 — "
                              "이 구간의 개정은 이 조문을 건드리지 않았음")
            else:
                out["서식차이"] = True
                out["안내"] = ("문안은 같고 표 서식(괘선 폭·전각/반각 기호·공백)만 다름 — 개정으로 보지 않음. "
                              "법제처 통합본은 시행본마다 표 서식이 달라지기도 함")
            return done()

        out["변경여부"] = True
        if lines_a is None:
            out["변경유형"] = "신설 (A 시점에는 없던 조문)"
        elif lines_b is None:
            out["변경유형"] = "삭제 (B 시점에는 없는 조문)"
        else:
            out["변경유형"] = "개정"
        diff_text = "\n".join(difflib.unified_diff(
            lines_a or [], lines_b or [],
            fromfile=f"A ({va['시행일자']} 시행본)", tofile=f"B ({vb['시행일자']} 시행본)",
            lineterm="", n=1))
        out["diff"] = diff_text[:max_chars]
        if len(diff_text) > max_chars:
            out["잘림"] = f"diff 전체 {len(diff_text)}자 중 앞 {max_chars}자만 표시 — max_chars를 늘려 재조회"

        if find_change:
            between = [r for r in history
                       if va["시행일자"] < (r["시행일자"] or "") <= vb["시행일자"]]
            if between:
                # B 문안은 어느 시점부터 계속 유지됐다고 보고(중간 되돌림 없음 가정),
                # B 문안과 같은 상태가 시작되는 최초 시행본을 이진탐색 — 왕복 log2(N)회
                lo, hi = 0, len(between) - 1
                while lo < hi:
                    mid = (lo + hi) // 2
                    if _norm_key(state(between[mid])[0]) == key_b:   # 서식만 다른 시행본은 같은 문안으로 본다
                        hi = mid
                    else:
                        lo = mid + 1
                first = between[lo]
                # 통합본은 시행일자 기준이라 같은 시행일자 행은 MST가 달라도 문안이 같다 — 이진탐색은
                # 그 그룹의 첫 행(가장 먼저 공포된 MST)에 멈추므로, 이 조문을 실제로 고친 공포본을 따로 고른다.
                # (조문 문안이 그 시행본부터 바뀌었으므로 '현행_문안_시작.시행일자'가 곧 이 문안의 시행일이다 —
                # 통합본의 조문시행일자 태그는 늘 시행본 날짜라 따로 내보내지 않는다)
                group = [r for r in between if r["시행일자"] == first["시행일자"]]
                prev_lines = state(between[lo - 1])[0] if lo > 0 else lines_a
                change, how = self._attribute_change(group, label, prev_lines, lines_b)
                out["현행_문안_시작"] = meta(change)
                # 과거 통합본 시대착오: B 문안에 그 시행본 시행일자보다 늦은 개정 주석이 있으면, 뒤에 공포된 개정이
                # 과거 통합본에 소급돼 실린 것이라 이진탐색이 그 개정 공포 전 시행본에 멈췄을 수 있다
                # (2026-10-09 실측: 소득세법 제81조의11 20230101→20261009 → 20240101 제19933호로 특정됐으나 B 문안
                # ③1호의 '2027년 …'은 2025.12.23 제21221호 개정문이 '법률 제19196호 … 제81조의11제3항제1호 중
                # "2026년 …"을 "2027년 …"으로' 고친 것, B 문안 주석 〈신설 2022.12.31, 2023.12.31, 2025.12.23〉)
                late = sorted(d for d in self._note_dates(lines_b) if d > first["시행일자"])
                if late:
                    cands = self._later_candidates(history, late[-1], label)
                    how = (f"불확실 — B 문안에 이 시행본 시행일자({first['시행일자']})보다 늦은 개정 주석({', '.join(late)})이 "
                           f"있음. 법제처 과거 통합본이 뒤에 공포된 개정을 소급해 실어 이진탐색이 그 개정 공포 전 시행본에 "
                           f"멈췄을 수 있음 — 실제로 이 문안(적어도 그 부분)을 만든 개정은 '뒤_개정_후보'일 수 있음 "
                           f"(원래 판정: {how})")
                    out["현행_문안_시작"]["뒤_개정_후보"] = cands
                    _add_caution(out, (f"'현행_문안_시작'({first['시행일자']}) 특정이 불확실 — B 문안에 그보다 늦은 개정 주석"
                                       f"({', '.join(late)})이 있음. 후보 개정: "
                                       + (", ".join(f"제{c['공포번호']}호({c['공포일자']})" for c in cands) or "연혁에서 못 찾음")
                                       + " — law_addenda_search(promul_no, article_no)로 확인"))
                out["현행_문안_시작"]["특정근거"] = how
                out["개정_부칙"] = self._addendum_for_change(change, label, via=first)
                out["안내"] = ("'현행_문안_시작'은 B 문안이 처음 시행된 시행본과 그 개정 공포본입니다. "
                              "A~B 사이에 개정이 여러 번 있었는지 확인하려면 date_to를 "
                              f"{change['시행일자']} 이전으로 좁혀 다시 비교하세요.")
        return done()

    def _later_candidates(self, history, pub_date: str, label: str):
        """공포일자가 pub_date인 연혁 행(공포번호별 하나) — 그 개정문이 이 조문을 언급하는지 함께."""
        by_no = {}
        for r in history:
            if r.get("공포일자") == pub_date:
                by_no.setdefault((r.get("공포번호") or "").lstrip("0"), []).append(r)
        out = []
        for no, rows in list(by_no.items())[:self._ATTRIBUTE_MAX]:
            r = rows[0]
            try:
                xml, _c = self._version_xml(r["MST"], r["시행일자"])
                mention = self._amend_mentions(self._amend_text(xml), r, label)
            except requests.exceptions.HTTPError:
                mention = None
            out.append({"공포일자": r["공포일자"], "공포번호": no, "제개정구분": r.get("제개정구분", ""),
                        "MST": r["MST"], "연혁상_시행일자": sorted({x["시행일자"] for x in rows if x.get("시행일자")}),
                        "개정문_언급": mention})
        out.sort(key=lambda c: (c["개정문_언급"] is not True, c["공포번호"]))
        return out

    @staticmethod
    def _fallback_caution(rows):
        return (f"시행본 {len(rows)}개({', '.join(rows[:5])})는 시행일 기준 통합본 조회에 실패해 그 MST "
                f"공포 당시 원문으로 비교함 — 그 시점 문안이 정확하지 않을 수 있으니 국가법령정보센터에서 확인할 것")

    @classmethod
    def _note_dates(cls, lines):
        """문안 줄의 〈개정 2024.12.31, 2025.12.23〉·〈신설 …〉 주석에 나온 날짜(YYYYMMDD) 집합."""
        out = set()
        for note in re.findall(r"〈[^〉]*〉", "\n".join(lines or [])):
            for y, m, d in cls._NOTE_DATE_RE.findall(note):
                out.add(f"{y}{int(m):02d}{int(d):02d}")
        return out

    def _attribute_change(self, group, label: str, prev_lines, new_lines):
        """B 문안이 처음 시행된 시행일자 그룹(같은 시행일자 연혁 행들)에서 이 조문을 실제로 고친
        공포본을 고른다. 반환 (연혁 행, 특정근거).

        통합본은 같은 시행일자면 MST가 달라도 문안이 같아(2026-10-09 실측: 소득세법 20260101 행
        MST 4개) 문안만으로는 공포본을 가를 수 없다. ① 새 문안에 새로 붙은 개정 주석 날짜
        (〈개정 …, 2026.4.21〉)와 공포일자가 같은 행, ② 그래도 여럿이면 그 공포본의 개정문 중 이 법령을
        고치는 부분(_amend_scope)이 이 조문을 언급하는 행을 고른다. 타법개정은 개정문 본문이 생략된 경우가
        있어(소득세법 MST 280411 '[본문 생략]') 못 가리면 가장 늦게 공포된 행을 '추정'으로 돌려준다."""
        if len(group) == 1:
            return group[0], "그 시행일자에 시행된 공포본이 하나뿐"
        new_dates = self._note_dates(new_lines) - self._note_dates(prev_lines)
        cands = [r for r in group if r.get("공포일자") in new_dates]
        if len(cands) == 1:
            return cands[0], f"새 문안의 개정 주석 날짜({cands[0]['공포일자']})와 공포일자가 일치"
        pool = cands or group
        if len(pool) <= self._ATTRIBUTE_MAX:
            hits = [r for r in pool
                    if self._amend_mentions(self._amend_text(self._version_xml(r["MST"], r["시행일자"])[0]), r, label)]
            if len(hits) == 1:
                return hits[0], "그 공포본의 개정문이 이 조문을 언급"
            pool = hits or pool
        pick = pool[-1]  # 연혁은 (시행일자, 공포일자, 공포번호) 오름차순 — 가장 늦게 공포된 행
        return pick, (f"추정 — 같은 시행일자({pick['시행일자']})에 시행된 공포본 {len(group)}개 중 "
                      f"가장 늦게 공포된 것. 다른 공포본일 수 있으니 law_addenda_search(article_no)로 확인")

    @staticmethod
    def _amend_text(xml: str) -> str:
        """개정문내용의 CDATA 조각을 줄바꿈으로 이어 붙인다 — 통합본은 조각 사이에 줄바꿈이 없어 그대로 이으면
        '⊙법률 제21221호소득세법 일부개정법률소득세법 일부를 …'처럼 법령명 경계가 사라진다 (2026-10-09 실측)."""
        m = re.search(r"<개정문내용>(.*?)</개정문내용>", xml, re.S)
        if not m:
            return ""
        pieces = re.findall(r"<!\[CDATA\[(.*?)\]\]>", m.group(1), re.S) or [m.group(1)]
        return html.unescape("\n".join(pieces))

    @staticmethod
    def _amend_scope(text: str, law_name: str):
        """개정문에서 이 법령을 고치는 부분만 잘라 낸다 (없으면 None).
        타법개정 개정문에는 다른 법의 부칙 조번호('부칙 제1조(시행일)', '제2조부터 제48조까지 생략',
        '제49조(다른 법률의 개정)')와 「다른 법」 인용 조문이 섞여 있어 통째로 대조하면 이 법의 조문으로 오인한다
        (2026-10-09 실측: 소득세법 MST 280411(조세특례제한법 타법개정 제21223호) 개정문이 제1조·제2조·제48조·
        제49조·제95조의2에 매치). '<법령명> 일부를 다음과 같이 개정한다'(옛 표기 '<법령명>중 다음과 같이 개정한다',
        '<법령명> 일부개정법률 일부를 …' 포함) 뒤부터 다음 중 가장 먼저 나오는 곳까지:
        '② 및 ③ 생략', 다른 법령의 '… 일부를 다음과 같이 개정한다', 이 개정의 부칙('부칙 제1조(' / '부칙 이 법은'),
        '제N조(다른 법률의 개정)'. 그 안에서도 고치는 문구("…")와 「…」 인용 바로 뒤 조번호는 뺀다."""
        key = r"[\s·ㆍ‧・]*".join(map(re.escape, _norm_name(law_name)))
        if not key:
            return None
        # 앞 글자가 한글이면 다른 법령명의 꼬리다 ('지방교육세법 일부를 …'의 '교육세법')
        start_re = re.compile(r"(?<![가-힣])" + key
                              + r"\s*(?:중\s*)?(?:일부개정법률\s*)?(?:일부를\s*)?다음과\s*같이\s*개정한다\.?")
        end_re = re.compile(r"[①-⑳]\s*(?:및\s*[①-⑳]\s*)?생략|[①-⑳][^①-⑳]{0,80}?일부를\s*다음과\s*같이\s*개정한다"
                            r"|부칙\s*(?:제1조\s*\(|이\s*법은)|제\d+조(?:의\d+)?\s*\(다른\s*법(?:률|령)")
        parts = []
        for m in start_re.finditer(text):
            rest = text[m.end():]
            e = end_re.search(rest)
            parts.append(rest[:e.start()] if e else rest)
        if not parts:
            return None
        scope = " ".join(parts)
        scope = re.sub(r"[\"“][^\"”]*[\"”]", " ", scope)
        scope = re.sub(r"「[^」]*」\s*(?:제\d+조(?:의\d+)?(?:제\d+항)?(?:제\d+호)?)?", " ", scope)
        return scope

    @classmethod
    def _amend_mentions(cls, amend: str, row: dict, label: str):
        """그 공포본 개정문이 이 조문을 고치는지 — 이 법령을 고치는 부분(_amend_scope)에서만 찾는다.
        '제87조의2부터 제87조의27까지'처럼 범위로 쓴 것도 펼쳐 맞춘다. 전부개정·제정은 모든 조문을 고친 것으로 본다."""
        if (row.get("제개정구분") or "") in ("전부개정", "제정") or re.search(r"전부를\s*다음과\s*같이\s*개정한다", amend):
            return True
        scope = cls._amend_scope(amend, row.get("법령명", ""))
        if not scope:
            return False
        if _article_pat(label).search(scope):
            return True
        lm = re.match(r"제(\d+)조(?:의(\d+))?", label)
        me = (int(lm.group(1)), int(lm.group(2) or 0))
        for a in re.finditer(r"제(\d+)조(?:의(\d+))?(?:제\d+항)?(?:제\d+호)?\s*부터\s*제(\d+)조(?:의(\d+))?", scope):
            if (int(a.group(1)), int(a.group(2) or 0)) <= me <= (int(a.group(3)), int(a.group(4) or 0)):
                return True
        return False

    def _addendum_for_change(self, change_row: dict, label: str, via: dict = None):
        """변경이 일어난 시행본의 부칙에서 시행일 요약 + 해당 조문 언급 항을 발췌.
        그 시행본 통합본의 부칙에는 그날까지 시행된 개정이 다 있으므로 그 공포본의 부칙도 있다
        (via: 같은 시행일자의 다른 행 — 이미 받은 통합본을 재사용). 없으면 그 공포본 원문에서 찾는다."""
        src = via or change_row
        xml, _caution = self._version_xml(src["MST"], src.get("시행일자", ""))
        units = self._parse_addenda_units(xml)
        want = (change_row.get("공포번호") or "").lstrip("0")
        unit = next((u for u in units if u["공포번호"] == want), None)
        if unit is None and change_row.get("시행일자"):
            units = self._parse_addenda_units(_get("lawService.do", target="law", MST=change_row["MST"]))
            unit = next((u for u in units if u["공포번호"] == want), None)
        if unit is None:
            return {"안내": (f"공포번호 {change_row.get('공포번호')}의 부칙을 찾지 못함 "
                            f"(타법개정 등) — law_addenda_search로 직접 조회")}
        adx = {
            "공포일자": unit["공포일자"],
            "공포번호": unit["공포번호"],
            "시행일_요약": self._effective_summary(unit["본문"]),
        }
        pat = _article_pat(label)   # '제96조의2'·'제59조의21' 오매치는 막고 '제96조의 개정규정'은 잡는다
        paras = [p[:1500] for p in self._split_addendum(unit["본문"]) if pat.search(p)]
        if paras:
            adx["이_조문_언급_항"] = paras[:5]
        else:
            adx["안내"] = ("이 개정 부칙에 이 조문을 명시한 적용례·경과조치 없음 — "
                          "시행일_요약의 일반 시행일을 따름")
        return adx
