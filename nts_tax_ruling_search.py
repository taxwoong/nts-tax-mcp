"""
nts_tax_ruling_search.py (v2)
국세법령정보시스템(taxlaw.nts.go.kr) 통합검색 API 클라이언트

한 번의 호출로 다음을 동시에 검색합니다:
- 사전답변 / 서면질의 / 질의회신 (국세청·기획재정부·법제처)
- 조세심판원 심판결정례 + 국세청 심사청구 결정례 + 법원 판례
- 법령, 별표서식, 구 법령해석자료, 홈택스 상담사례

v2 개선사항:
  ① 페이지네이션(page) 지원
  ② 사건번호(doc_no)로 직접 조회
  ③ 결과 0건일 때 안내 메시지 자동 첨부
  ④ 세목 필터(클라이언트단 후처리) 지원
  ⑤ 정렬 옵션(정확도순/최신순) 지원
  ⑥ 응답 크기 관리 — 필요시 본문 전문을 생략하고 요약만 반환
  ⑦ 세션 만료 자동 감지 및 재접속
  ⑧ 캐싱 + 최소 요청 간격 (정중한 크롤링)
  ⑨ 예상치 못한 응답 구조에 대한 로깅

브라우저 없이 requests만으로 동작 (세션 쿠키 확보 후 재사용).
"""

import html
import json
import logging
import re
import time
from typing import Optional

import requests

# 문서번호 정규화는 두 시스템 공통 규칙 — 한 곳에서만 정의한다
# (olta 모듈은 이 모듈을 import하지 않으므로 순환 없음)
from olta_tax_ruling_search import normalize_doc_no

logger = logging.getLogger("nts_tax_ruling_search")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

BASE_URL = "https://taxlaw.nts.go.kr"
SEARCH_ENTRY_URL = f"{BASE_URL}/qt/USEQTA001M.do?ntstDcmClCd=01"
ACTION_URL = f"{BASE_URL}/action.do"

# 붙임 파일 조회 액션 — 검색 응답의 NTST_FLE_ID를 넘기면 다운로드 URI를 돌려준다.
# (사이트 /js/common/common.js의 파일 컴포넌트가 쓰는 액션. 다운로드에 필요한
#  fleSn(파일 일련번호)은 이 호출로만 얻을 수 있어 2회 요청이 불가피하다.)
FILE_META_ACTION = "ACMCMA001MR02"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

COLLECTIONS = {
    "form": "appendForm",
    "statute": "statute",
    "ruling": "question",
    "precedent": "precedent",
    "old_ruling": "formerLibrary",
    "intl": "intEpn",
    "hometax": "hometaxCnslThan",
}
ALL_COLLECTIONS = list(COLLECTIONS.values())

# 법령(statute) 컬렉션에는 법령 조문·기본통칙·집행기준·훈령·고시·조세조약이 한데 섞여 온다.
# 검색 요청의 mainIdCtl에 종류별 MAIN_ID를 주면 서버가 그 종류만 돌려준다 (2026-10-18 실측 —
# '부당행위계산' 58건 중 BM001_04 → 통칙 14건, BM001_05 → 집행기준 15건). 단 mainIdCtl은 요청 전체에
# 걸려 함께 요청한 다른 컬렉션이 0건이 되므로, 종류를 거를 때는 statute만 따로 요청한다.
STATUTE_TYPES = {
    "법령": "LBM001_999",
    "기본통칙": "BM001_04",
    "집행기준": "BM001_05",
    "훈령": "BM021_03",
    "고시": "BM021_01",
    "조세조약": "BL027_00",
}
_STATUTE_TYPE_BY_MAIN_ID = {v: k for k, v in STATUTE_TYPES.items()}
# 집행기준은 검색 응답에 본문(TEXT_KRN_CNTN)이 없다 — 번호·제목만 온다 (2026-10-18 실측 44건 전부).
# 본문은 서버가 받아 둔 세목별 집행기준 책자(nts_enforcement)에서 채운다
_ENFORCEMENT_NO_BODY_NOTE = (
    "일부 집행기준은 본문을 채우지 못해 번호·제목만 나옵니다(국세청 검색 응답에 본문이 없고, 서버의 집행기준 "
    "책자 보관소에서도 찾지 못함). 제목만으로 내용을 단정하지 말고, nts_enforcement_standard로 같은 조문의 "
    "집행기준을 보거나 법령·기본통칙·질의회신으로 근거를 확인하세요."
)
_ENFORCEMENT_FILLED_NOTE = (
    "집행기준 본문(summary)은 국세청 전자도서관의 세목별 집행기준 책자(source_book 판)에서 가져왔습니다 — "
    "책자 발간 뒤의 법령 개정은 반영돼 있지 않을 수 있습니다. 1,000자가 넘으면 잘리니 전문은 "
    "nts_enforcement_standard(law_name, 번호)로 보세요."
)

ORG_CODES = {
    "01": "국세청",
    "02": "기획재정부",
    "03": "법제처",
    "04": "조세심판원",
}

SORT_OPTIONS = {
    "relevance": "SCORE/DESC",
    "date_desc": "DCM_RGT_DTM/DESC",
    "date_asc": "DCM_RGT_DTM/ASC",
}

# 세목 코드표 (검색화면 select[name=tlawClCd] 실측)
TAX_TYPE_CODES = {
    "국세기본": "301",
    "국세징수": "302",
    "법인세": "303",
    "종합소득세": "305",
    "부가가치세": "306",
    "양도소득세": "307",
    "상속증여세": "308",
    "조세특례": "309",
    "국제조세": "310",
    "종합부동산세": "311",
    "원천세": "312",
    "소비세": "313",
    "주세": "314",
    "교육세": "315",
}

DEFAULT_CACHE_TTL = 300
DEFAULT_MIN_INTERVAL = 0.5

# 법원 사건번호 표기 — 국세청은 '서울고등법원-2022-누-45199'로 싣지만 인용은 보통
# '2022누45199', '서울고법 2022누45199'처럼 한다. 정확 일치만 보면 실존 판결을 못 찾는다.
_COURT_ABBR = (("고법", "고등법원"), ("지법", "지방법원"), ("행법", "행정법원"), ("가법", "가정법원"))
_COURT_CASE_KINDS = {"누", "구합", "구단", "구", "두", "다", "나", "가합", "가단", "도", "노",
                     "고합", "고단", "재누", "재두", "재구합", "후", "허", "카합"}
_COURT_CASE_RE = re.compile(r"^(.*?)(\d{4})([가-힣]{1,3})(\d+)$")


def _split_court_case(doc_no: str):
    """법원 사건번호면 (법원명, '2022누45199')을, 아니면 None. 법원명 축약은 펼친다."""
    m = _COURT_CASE_RE.match(normalize_doc_no(doc_no))
    if not m or m.group(3) not in _COURT_CASE_KINDS:
        return None
    court = m.group(1)
    if court and not re.search(r"(법원|지원|고법|지법|행법|가법|대법)$", court):
        return None  # '조심', '서면' 등 — 법원 사건이 아님
    for short, full in _COURT_ABBR:
        if court.endswith(short):
            court = court[: -len(short)] + full
    if court == "대법":
        court = "대법원"
    return court, m.group(2) + m.group(3) + m.group(4)


# ---- 질의회신·판례 밖의 컬렉션 필드 매핑 ----
# 질의회신(question)·판례(precedent)는 TTL·NTST_DCM_DSCM_CNTN·GIST_CNTN 등을 쓰지만, 법령(statute)·별표서식(appendForm)·
# 구 법령해석자료(formerLibrary)·홈택스 상담사례(hometaxCnslThan)는 필드 이름이 달라 그대로 두면 제목·본문 없이
# doc_id와 색인 시각만 나갔다 (2026-10-10 실측: statute 결과 17건이 전부 title·summary 빈 값).
_HL_RE = re.compile(r"<!H[SE]>")
_HTML_TAG_RE = re.compile(
    r"</?(?:p|br|div|span|table|thead|tbody|tr|td|th|ul|ol|li|strong|b|u|i|em|font)\b[^>]*>", re.I)
_TAX_NAME_BY_CODE = {code: name for name, code in TAX_TYPE_CODES.items()}
_SNIPPET_MAX = 1000
_OTHER_COLLECTIONS = ("statute", "appendForm", "formerLibrary", "hometaxCnslThan")


def _plain(text, limit: Optional[int] = None):
    """검색 응답의 강조 표시(<!HS>…<!HE>)와 HTML 태그를 걷고 엔티티를 풀어 평문으로. 빈 값은 None.
    통칙 본문은 HTML이 이스케이프된 채(&lt;p&gt;…) 오지만 법령 조문은 '<개정 2020.1.1.>' 같은 꺾쇠가 글자 그대로
    있으므로, 이스케이프돼 있을 때만 알려진 HTML 태그를 지운다."""
    if not text:
        return None
    t = _HL_RE.sub("", text)
    if "&lt;" in t or "&amp;" in t or "&#" in t:
        t = html.unescape(t)
        t = re.sub(r"</p>|<br\s*/?>", "\n", t, flags=re.I)
        t = _HTML_TAG_RE.sub("", t)
    t = re.sub(r"[ \t ]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t).strip()
    if limit and len(t) > limit:
        t = t[:limit].rstrip() + " …(이하 생략)"
    return t or None


def _fill_non_ruling_fields(item: dict, it: dict, coll: Optional[str]) -> None:
    """법령·별표서식·구 법령해석자료·홈택스 상담사례 항목의 빈 필드를 컬렉션별 원본 필드로 채운다.
    질의회신·판례 항목은 건드리지 않는다. coll을 모르면 필드로 컬렉션을 추정한다."""
    kind = coll if coll in _OTHER_COLLECTIONS else None
    if kind is None and coll is None:
        kind = ("statute" if it.get("TEXT_KRN_NM") else "appendForm" if it.get("FRML_NM")
                else "hometaxCnslThan" if it.get("STD_TITLE")
                else "formerLibrary" if it.get("NTST_PLCN_BK_TTL") else None)
    if kind is None:
        return
    name = _plain(it.get("NM"))
    extra = {}
    if kind == "statute":
        item["title"] = _plain(" ".join(x for x in (it.get("NM"), it.get("TEXT_KRN_NM")) if x), 300) or item["title"]
        # 종류는 statute_types 값과 같은 이름으로 (LBL1_NM은 '통칙'·'집행'·'조약'처럼 줄여 온다)
        item["doc_type"] = (_STATUTE_TYPE_BY_MAIN_ID.get(it.get("MAIN_ID")) or it.get("LBL1_NM")
                            or it.get("STTT_CL_NM") or item["doc_type"])
        item["date"] = it.get("PMG_DT") or item["date"]
        item["tax_type"] = _TAX_NAME_BY_CODE.get(it.get("TLAW_CL_CD_CMN")) or item["tax_type"]
        item["summary"] = _plain(it.get("TEXT_KRN_CNTN"), _SNIPPET_MAX) or item["summary"]
        extra = {"law_name": name, "promulgation_no": it.get("PMG_NO"), "enforce_date": it.get("ENFR_DT")}
    elif kind == "appendForm":
        item["title"] = _plain(it.get("FRML_NM"), 300) or item["title"]
        item["doc_type"] = it.get("STTT_CL_NM") or it.get("LBL1_NM") or item["doc_type"]
        item["date"] = it.get("PMG_DT") or item["date"]
        item["tax_type"] = _TAX_NAME_BY_CODE.get(it.get("TLAW_CL_CD_CMN")) or item["tax_type"]
        item["summary"] = _plain(it.get("FILE_CN"), _SNIPPET_MAX) or item["summary"]
        extra = {"law_name": name, "promulgation_no": it.get("PMG_NO"), "enforce_date": it.get("ENFR_DT")}
    elif kind == "formerLibrary":
        item["title"] = name or _plain(it.get("NTST_PLCN_BK_TTL")) or item["title"]
        item["doc_type"] = it.get("LBL1_NM") or item["doc_type"]
        item["source_org"] = it.get("NTST_JRSD_DNO_NM") or item["source_org"]
        item["date"] = it.get("PLCN_DT") or item["date"]
        item["summary"] = _plain(it.get("FILE_CN"), _SNIPPET_MAX) or item["summary"]
    elif kind == "hometaxCnslThan":
        item["title"] = _plain(it.get("STD_TITLE"), 300) or item["title"]
        item["doc_type"] = it.get("LBL1_NM") or item["doc_type"]
        item["date"] = it.get("REGST_DT") or item["date"]
        item["tax_type"] = (it.get("REQ_TP_NM") or "").split("-")[0] or item["tax_type"]
        item["summary"] = _plain(it.get("ANSWER_STD_CONTENT"), _SNIPPET_MAX) or item["summary"]
    item.update({k: v for k, v in extra.items() if v})


def build_search_params(keyword: str, collections: list, start_count: int = 1, view_count: int = 20,
                        sort: str = "relevance", tax_codes=(), date_base: str = "DCM_RGT_DTM",
                        main_ids=()) -> dict:
    """통합검색(ASEISA001MR01) paramData. collections는 원본 컬렉션 코드(statute·formerLibrary 등)."""
    # 주의: bltnStrtDtm/bltnEndDtm 등 기간 관련 서버 파라미터는 실제 UI에 대응하는
    # 필드를 확인하지 못해 의도적으로 제외했습니다 (잘못된 값 전송시 검색 자체가
    # 0건으로 깨지는 문제가 있었습니다). 기간 필터는 search()에서 클라이언트단으로 처리합니다.
    return {
        "schVcb": keyword,
        "startCount": start_count,
        "collection": ",".join(collections),
        "wnKey": "",
        "searchType": "",
        "sortField": SORT_OPTIONS[sort],
        "ntstTlawClCdList": list(tax_codes),
        "icldVcbCtl": [],
        "exclVcbCtl": [],
        "rltnStttCtl": [],
        "schDtBase": date_base,
        "viewCount": str(view_count),
        "prtsSprcChiefJdgmYn": "",
        "prtsAttrYrCtl": [],
        "prtsPrgrStatCtl": [],
        "mainIdCtl": list(main_ids),
        "useSynonymYn": "N",
    }


class NtsParseError(Exception):
    """응답은 받았으나 예상한 구조가 아님 — 사이트 개편 가능성.
    '검색 결과 0건'과 반드시 구분해야 한다 (0건으로 오인하면 LLM이 자료 부존재로 단정)."""


class NtsTaxLawClient:
    """국세법령정보시스템 통합검색 클라이언트 (세션 재사용 + 캐싱 + 재접속)"""

    def __init__(
        self,
        verify_ssl: bool = True,
        timeout: int = 20,
        cache_ttl: int = DEFAULT_CACHE_TTL,
        min_request_interval: float = DEFAULT_MIN_INTERVAL,
    ):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._bootstrapped = False

        self._cache_ttl = cache_ttl
        self._cache: dict = {}

        self._min_request_interval = min_request_interval
        self._last_request_ts = 0.0

    def _bootstrap(self, force: bool = False):
        if force:
            self.session = requests.Session()
            self.session.headers.update({"User-Agent": USER_AGENT})
            self._bootstrapped = False

        resp = self.session.get(SEARCH_ENTRY_URL, verify=self.verify_ssl, timeout=self.timeout)
        resp.raise_for_status()
        self._bootstrapped = True
        logger.info("세션 부트스트랩 완료 (force=%s)", force)

    def _throttle(self):
        elapsed = time.time() - self._last_request_ts
        if elapsed < self._min_request_interval:
            time.sleep(self._min_request_interval - elapsed)
        self._last_request_ts = time.time()

    def _cache_key(self, **kwargs) -> str:
        return json.dumps(kwargs, sort_keys=True, ensure_ascii=False)

    def _post_search(self, param_data: dict) -> dict:
        if not self._bootstrapped:
            self._bootstrap()

        self._throttle()

        def _do_request():
            resp = self.session.post(
                ACTION_URL,
                data={
                    "actionId": "ASEISA001MR01",
                    "paramData": json.dumps(param_data, ensure_ascii=False),
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": SEARCH_ENTRY_URL,
                },
                verify=self.verify_ssl,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()

        try:
            raw = _do_request()
            if "data" not in raw:
                raise ValueError("응답에 'data' 필드가 없습니다 (세션 만료 가능성)")
            return raw
        except (ValueError, json.JSONDecodeError, requests.exceptions.RequestException) as e:
            logger.warning("검색 요청 실패, 세션 재접속 후 재시도합니다: %s", e)
            self._bootstrap(force=True)
            self._throttle()
            raw = _do_request()
            return raw

    def search(
        self,
        keyword: str,
        collections: Optional[list] = None,
        page: int = 1,
        view_count: int = 20,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        date_base: str = "DCM_RGT_DTM",
        sort: str = "relevance",
        tax_type_filter: Optional[str] = None,
        include_full_text: bool = True,
        use_cache: bool = True,
        statute_types: Optional[list] = None,
    ) -> dict:
        """
        키워드로 통합검색을 수행하고, 컬렉션별로 정리된 결과를 반환합니다.

        Args:
            keyword: 검색어
            collections: COLLECTIONS 값 리스트 중 원하는 것만 선택 (기본값: 전체)
            page: 페이지 번호 (1부터 시작)
            view_count: 컬렉션별로 가져올 결과 개수
            date_from / date_to: 검색 기간 (YYYYMMDD).
                주의: taxlaw.nts.go.kr 통합검색 화면에는 기간 필터 UI 자체가 없어,
                서버 API에 기간 조건을 직접 전달하는 공식 파라미터를 확인하지 못했습니다.
                따라서 이 값은 서버에 전달되지 않고, 검색 결과를 받아온 뒤
                각 항목의 date(YYYYMMDD) 필드를 기준으로 클라이언트단에서 걸러냅니다.
                (참고: 이 방식은 결과 목록은 정확히 필터링하지만, total_count는
                전체 건수를 그대로 보여줍니다 — 필터링 전 서버 응답 기준입니다.)
            sort: "relevance"(정확도순, 기본) | "date_desc"(최신순) | "date_asc"(오래된순)
            tax_type_filter: 결과의 세목명(tax_type)에 이 문자열이 포함된 것만 남김
            include_full_text: False면 본문 전문(content, detail_content)을 생략하고 요약만 반환
            use_cache: True면 동일 조건 검색을 짧은 시간 내 재호출시 캐시된 결과를 반환
            statute_types: 법령(statute) 컬렉션을 STATUTE_TYPES 종류로 좁힌다 (서버측 필터라 total_count도
                그 종류 기준). 주면 statute를 반드시 검색한다 — collections를 생략하면 statute만,
                다른 컬렉션과 함께 주면 statute는 따로 한 번 더 요청한다.
        """
        main_ids = []
        if statute_types:
            unknown = [t for t in statute_types if t not in STATUTE_TYPES]
            if unknown:
                raise ValueError(f"statute_types는 {list(STATUTE_TYPES)} 중에서 고르세요 (받은 값: {unknown})")
            main_ids = [STATUTE_TYPES[t] for t in statute_types]
            if collections is None:
                collections = ["statute"]
            elif "statute" not in collections:
                collections = list(collections) + ["statute"]
        if collections is None:
            collections = ALL_COLLECTIONS

        if sort not in SORT_OPTIONS:
            raise ValueError(f"sort는 {list(SORT_OPTIONS.keys())} 중 하나여야 합니다")

        start_count = (page - 1) * view_count + 1

        # 날짜 필터가 있으면 필터링 후 개수가 너무 적어지지 않도록 내부적으로 더 많이 가져옵니다.
        fetch_view_count = view_count
        if date_from or date_to:
            fetch_view_count = min(max(view_count * 5, 50), 200)

        cache_key = None
        if use_cache:
            cache_key = self._cache_key(
                keyword=keyword, collections=collections, page=page,
                view_count=view_count, date_from=date_from, date_to=date_to,
                sort=sort, tax_type_filter=tax_type_filter,
                include_full_text=include_full_text, statute_types=statute_types,
            )
            cached = self._cache.get(cache_key)
            if cached and (time.time() - cached[0]) < self._cache_ttl:
                logger.info("캐시된 결과 반환: %s", keyword)
                return cached[1]

        # 세목 필터: 코드표에 정확히 일치하는 세목명이면 서버측 필터(정확), 아니면 클라이언트단 후처리
        server_tax_codes = []
        client_tax_filter = None
        if tax_type_filter:
            if tax_type_filter in TAX_TYPE_CODES:
                server_tax_codes = [TAX_TYPE_CODES[tax_type_filter]]
            else:
                client_tax_filter = tax_type_filter

        def _param_data(colls, mids):
            return build_search_params(keyword, colls, start_count=start_count, view_count=fetch_view_count,
                                       sort=sort, tax_codes=server_tax_codes, date_base=date_base, main_ids=mids)

        if main_ids:
            # mainIdCtl은 요청 전체에 걸린다 — 다른 컬렉션은 필터 없이 따로 요청해 합친다
            parsed = self._parse(self._post_search(_param_data(["statute"], main_ids)),
                                 include_full_text=include_full_text)
            others = [c for c in collections if c != "statute"]
            if others:
                parsed.update(self._parse(self._post_search(_param_data(others, [])),
                                          include_full_text=include_full_text))
            if "statute" in parsed:
                parsed["statute"]["statute_types"] = list(statute_types)
            # 요청한 컬렉션 순서대로 (서버가 모르는 이름을 돌려주면 뒤에 붙인다)
            result = {c: parsed.pop(c) for c in collections if c in parsed}
            result.update(parsed)
        else:
            result = self._parse(self._post_search(_param_data(collections, [])),
                                 include_full_text=include_full_text)

        if client_tax_filter:
            result = self._apply_tax_type_filter(result, client_tax_filter)

        if date_from or date_to:
            result = self._apply_date_filter(result, date_from, date_to)
            # 내부적으로 더 가져온 결과를 다시 요청한 view_count 만큼으로 잘라냅니다.
            for coll_name, coll_data in result.items():
                if coll_name == "_guidance" or not isinstance(coll_data, dict):
                    continue
                coll_data["items"] = coll_data.get("items", [])[:view_count]

        self._fill_enforcement(result)
        result = self._attach_guidance(result, keyword)

        if use_cache and cache_key:
            self._cache[cache_key] = (time.time(), result)

        return result

    def get_by_doc_no(self, doc_no: str, view_count: int = 20) -> dict:
        """
        사건번호/문서번호로 직접 조회합니다.
        예: "조심-2023-서-9465", "서면-2019-법규재산-4276", "기획재정부 재산세제과-73"

        내부적으로는 문서번호를 검색어로 통합검색을 수행한 뒤,
        결과 중 doc_no가 정확히 일치하는 항목만 추려서 반환합니다.
        (별도 상세조회 전용 API 엔드포인트는 확인되지 않아 검색 기반으로 구현했습니다.)
        1차 시도에서 못 찾으면 결과 수를 늘려 한 번 더 시도합니다.
        """
        # 표기 차이(하이픈·띄어쓰기)로 실존 문서를 놓치면 status NOT_FOUND가
        # '자료 부존재'로 해석되므로, verify_citations와 같은 정규화 기준으로 비교한다
        want = normalize_doc_no(doc_no)
        case = _split_court_case(doc_no)
        keywords = [doc_no]
        if case and case[1] != doc_no.strip():
            keywords.append(case[1])  # '서울고법 2022누45199'는 사건번호만으로 검색해야 걸린다
        loose = {}
        for keyword in keywords:
            for attempt_view_count in (view_count, max(view_count, 50)):
                result = self.search(
                    keyword=keyword,
                    collections=["question", "precedent"],
                    view_count=attempt_view_count,
                    use_cache=True,
                )
                matched = []
                for coll_name, coll_data in result.items():
                    if not isinstance(coll_data, dict):  # status·_guidance 등 메타 키 스킵
                        continue
                    for item in coll_data.get("items", []):
                        if not item.get("doc_no"):
                            continue
                        norm = normalize_doc_no(item["doc_no"])
                        if norm == want:
                            matched.append(item)
                        elif case and self._court_case_match(norm, case):
                            loose.setdefault(item["doc_no"], item)
                if matched:
                    return {"status": "OK", "found": True, "items": matched}
                if loose:
                    break
            if loose:
                break

        if len(loose) == 1:
            return {"status": "OK", "found": True, "items": list(loose.values()),
                    "일치방식": f"사건번호 일치 — 국세청 표기는 '{next(iter(loose))}'"}
        if loose:
            # 같은 사건번호가 여러 법원에 있다 (예: 2019구합5926 = 제주지법·울산지법)
            return {"status": "OK", "found": True, "ambiguous": True,
                    "items": list(loose.values()),
                    "message": (f"사건번호 '{case[1]}'가 여러 법원에 있습니다: "
                                f"{', '.join(loose)} — 법원명을 포함한 문서번호로 다시 조회하세요.")}

        return {
            "status": "NOT_FOUND",
            "found": False,
            "items": [],
            "message": (
                f"'{doc_no}'와 정확히 일치하는 문서를 찾지 못했습니다. "
                "문서번호 표기(띄어쓰기, 하이픈 등)를 다시 확인하시거나, "
                "일반 키워드 검색(search)을 이용해 보세요."
            ),
        }

    @staticmethod
    def _court_case_match(norm: str, case) -> bool:
        """정규화된 국세청 문서번호가 (법원명, 사건번호)와 같은 사건인지.
        사건번호는 접미 일치(앞자리 숫자 경계 확인), 법원명은 입력에 있을 때만 비교."""
        court, number = case
        if not norm.endswith(number):
            return False
        head = norm[: -len(number)]
        if head and head[-1].isdigit():  # '2022누45199'가 '…12022누45199'에 걸리지 않게
            return False
        return not court or head == court

    # -----------------------------------------------------------------------
    # 붙임 전문 조회
    #
    # 검색 API는 본문을 주지 않는다 — 심판·심사의 CNTN은 '결정내용은 붙임과
    # 같습니다.' 상수이고, FILE_CN은 검색어 주변 500~900자 스니펫이다.
    # 결정 이유·처분개요·청구주장·사실관계는 전부 붙임 HWP 안에 있다.
    # (2026-09-10 검증: 판례·심판·사전답변·질의회신 12건 전건 수집·추출 성공)
    # -----------------------------------------------------------------------

    def get_attachment_meta(self, file_id: str) -> list:
        """붙임 파일 ID로 파일 메타(다운로드 URI·확장자·크기)를 조회한다."""
        if not self._bootstrapped:
            self._bootstrap()
        self._throttle()

        resp = self.session.post(
            ACTION_URL,
            data={"actionId": FILE_META_ACTION,
                  "paramData": json.dumps({"fleId": file_id}, ensure_ascii=False)},
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": SEARCH_ENTRY_URL,
            },
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        try:
            return resp.json()["data"][FILE_META_ACTION] or []
        except (KeyError, TypeError, ValueError) as e:
            raise NtsParseError(
                f"붙임 메타 응답 구조가 예상과 다름 (fleId={file_id}, {e}) — 사이트 개편 가능성"
            ) from e

    def download_attachment(self, download_uri: str) -> bytes:
        """fleDwldUri로 붙임 파일 원본을 받는다."""
        self._throttle()
        resp = self.session.get(
            BASE_URL + download_uri,
            headers={"Referer": SEARCH_ENTRY_URL},
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.content

    def get_attachment_text(self, file_id: str) -> dict:
        """
        붙임 파일 ID로 전문 텍스트를 가져온다.

        Returns:
            status / text / files(파일별 확장자·바이트·추출 자수)
            HWP가 아닌 붙임(PDF 등)은 텍스트를 못 뽑고 files에 형식만 기록한다.
        """
        from hwp_text import extract_text, HwpExtractError  # 지연 import (파서 교체 용이)

        metas = self.get_attachment_meta(file_id)
        if not metas:
            return {"status": "NOT_FOUND", "text": "", "files": [],
                    "message": f"붙임 파일 정보가 없습니다 (file_id={file_id})."}

        texts, files = [], []
        for meta in metas:
            uri = meta.get("fleDwldUri")
            info = {"ext": meta.get("fleXsnNm"), "bytes": meta.get("fleSz")}
            if not uri:
                info["오류"] = "다운로드 URI 없음"
                files.append(info)
                continue
            content = self.download_attachment(uri)
            info["bytes"] = len(content)
            try:
                txt = extract_text(content)
                info["추출자수"] = len(txt)
                if txt.strip():
                    texts.append(txt)
                else:
                    # 국세청이 글자 없는 HWP를 붙여 둔 문서가 있다(표본 197건 중 12건,
                    # 주로 질의). 빈 문자열을 OK로 넘기면 '본문 없음'으로 오해된다.
                    info["비고"] = "빈 붙임 — 본문 글자가 없는 HWP"
            except HwpExtractError as e:
                # 붙임이 HWP가 아니면 전문을 못 읽는다 — 조용히 빈 값을 주면
                # '자료 없음'으로 오해되므로 형식을 명시해 남긴다.
                info["오류"] = str(e)
                logger.warning("붙임 추출 실패 (fleId=%s): %s", file_id, e)
            files.append(info)

        if not texts:
            if any("오류" in f for f in files):
                return {"status": "PARSE_ERROR", "text": "", "files": files,
                        "message": "붙임을 받았으나 본문 텍스트를 추출하지 못했습니다."}
            return {"status": "EMPTY", "text": "", "files": files,
                    "message": "붙임 파일에 본문 글자가 없습니다(빈 HWP)."}

        return {"status": "OK", "text": "\n\n".join(texts), "files": files}

    def get_full_text(self, doc_no: str, max_chars: int = 30000,
                      start_char: int = 0) -> dict:
        """
        문서번호로 붙임 전문을 조회한다 (검색 → 붙임 다운로드 → 텍스트 추출).

        Args:
            doc_no: 사건번호/문서번호 (예: "조심-2026-서-1112")
            max_chars: 본문 최대 길이
            start_char: 본문 시작 오프셋 — 긴 문서를 이어 읽을 때
        """
        found = self.get_by_doc_no(doc_no)
        if not found.get("found"):
            return found
        if found.get("ambiguous"):
            return {"status": "INVALID_INPUT", "오류": found["message"],
                    "후보": [it.get("doc_no") for it in found["items"]]}

        item = found["items"][0]
        file_id = item.get("file_id")
        if not file_id:
            return {
                "status": "NOT_FOUND",
                "문서번호": item.get("doc_no"),
                "제목": item.get("title"),
                "message": (
                    "이 문서에는 붙임 파일이 없어 전문을 가져올 수 없습니다. "
                    "요지(summary)와 검색 스니펫만 확인 가능합니다."
                ),
                "요지": item.get("summary"),
            }

        got = self.get_attachment_text(file_id)
        note = None
        if got["status"] == "EMPTY":
            # 빈 붙임 문서는 검색 결과 본문(CNTN)에 회신이 들어 있다 — 그걸 대신 준다.
            # 단 심판·심사의 CNTN은 '결정내용은 붙임과 같습니다.' 상수라 쓸 수 없다.
            body = (item.get("content") or "").strip()
            if body and "붙임과 같습니다" not in body:
                got = {"status": "OK", "text": body, "files": got["files"]}
                note = ("붙임 파일이 비어 있어(글자 없는 HWP) 검색 결과의 본문을 대신 "
                        "반환합니다. 짧은 회신은 이것이 전부일 수 있으나 원문 전체인지는 "
                        "확인할 수 없습니다.")
            else:
                got = {"status": "NOT_FOUND", "files": got["files"],
                       "message": ("붙임 파일이 비어 있고(글자 없는 HWP) 검색 결과에도 "
                                   "본문이 없어 전문을 가져올 수 없습니다. 요지만 확인 가능합니다.")}
        out = {
            "status": got["status"],
            "문서번호": item.get("doc_no"),
            "제목": item.get("title"),
            "종류": item.get("doc_type"),
            "일자": item.get("date"),
            "세목": item.get("tax_type"),
            "출처기관": item.get("source_org"),
            "요지": item.get("summary"),
            "붙임": got.get("files"),
        }
        if got["status"] != "OK":
            out["message"] = got.get("message")
            return out

        text = got["text"]
        total = len(text)
        unknown = text.count("�")
        if unknown:
            sym_note = (f"본문의 '�' {unknown}곳은 한컴 전용 기호(글머리표·원문자 등)라 표준 "
                        f"문자로 바꾸지 못한 자리입니다. 기호 하나가 한 글자로 표시된 것이며 "
                        f"본문 글자가 빠진 것은 아닙니다.")
            note = f"{note} / {sym_note}" if note else sym_note
        if note:
            out["비고"] = note
        out["전문자수"] = total
        out["전문"] = text[start_char:start_char + max_chars]
        end = min(start_char + max_chars, total)
        if end < total or start_char:
            out["잘림"] = (
                f"전체 {total}자 중 {start_char}~{end}자만 표시됨 — "
                f"이어 읽으려면 start_char={end}로 다시 조회 "
                f"(또는 max_chars를 {total} 이상으로 지정)"
            )
        return out

    def _parse(self, raw: dict, include_full_text: bool = True) -> dict:
        try:
            srv = raw["data"]["ASEISA001MR01"]["searchResultVO"]
        except (KeyError, TypeError) as e:
            logger.error("예상치 못한 응답 구조입니다: %s / raw keys=%s", e, list(raw.keys()))
            raise NtsParseError(
                f"국세법령정보시스템 응답 구조가 예상과 다름 (누락 경로: {e}, "
                f"raw keys={list(raw.keys())}, status={raw.get('status')}) — 사이트 개편 가능성"
            ) from e

        result = {}
        for coll in srv.get("collectionList", []):
            name_en = coll.get("nameEn")
            result[name_en] = {
                "name_kr": coll.get("nameKr"),
                "total_count": coll.get("totalCount", 0),
                "items": [
                    self._clean_item(it, include_full_text=include_full_text, coll=name_en)
                    for it in coll.get("resultList", [])
                ],
            }
        return result

    @staticmethod
    def _clean_item(it: dict, include_full_text: bool = True, coll: Optional[str] = None) -> dict:
        def strip_hl(text):
            if not text:
                return text
            return text.replace("<!HS>", "").replace("<!HE>", "")

        org_code = it.get("NTST_DCM_SRCS_ORGN_CL_CD")
        item = {
            "title": strip_hl(it.get("TTL")),
            "doc_type": it.get("NTST_DCM_CL_NM"),
            "doc_no": strip_hl(it.get("NTST_DCM_DSCM_CNTN")),
            "source_org": ORG_CODES.get(org_code, org_code),
            "date": it.get("DCM_RGT_DTM_S") or it.get("DATE"),
            "tax_type": it.get("NTST_TLAW_CL_NM"),
            "summary": strip_hl(it.get("GIST_CNTN")),
            "doc_id": it.get("DOC_ID"),
            "related_doc_ids": it.get("RFRN_QUT_NTST_DCM_ID"),
            # 붙임 파일 ID — 결정 이유·사실관계 전문은 여기 달린 HWP에만 있다.
            # get_full_text()/nts_ruling_get_full_text 도구가 이 값을 쓴다.
            "file_id": it.get("NTST_FLE_ID") or None,
        }
        _fill_non_ruling_fields(item, it, coll)
        if include_full_text:
            # 주의: 이름과 달리 '전문'이 아니다 —
            #   CNTN    = 심판·심사는 전건 '결정내용은 붙임과 같습니다.' 상수
            #   FILE_CN = 검색어 주변 500~900자 발췌(검색엔진이 잘라주는 스니펫)
            # 전문이 필요하면 file_id로 get_full_text()를 호출해야 한다.
            item["content"] = strip_hl(it.get("CNTN"))
            item["detail_content"] = strip_hl(it.get("FILE_CN"))
        return item

    @staticmethod
    def _apply_date_filter(result: dict, date_from: Optional[str], date_to: Optional[str]) -> dict:
        """클라이언트단 날짜 필터. item['date']는 'YYYYMMDD...' 형식 문자열입니다."""
        def in_range(date_str: Optional[str]) -> bool:
            if not date_str:
                return False
            d = date_str[:8]  # YYYYMMDD 부분만
            if date_from and d < date_from:
                return False
            if date_to and d > date_to:
                return False
            return True

        filtered = {}
        for coll_name, coll_data in result.items():
            if coll_name == "_guidance":
                filtered[coll_name] = coll_data
                continue
            items = [it for it in coll_data.get("items", []) if in_range(it.get("date"))]
            filtered[coll_name] = {
                **coll_data,
                "items": items,
                "filtered_by_date": {"from": date_from, "to": date_to},
            }
        return filtered

    @staticmethod
    def _apply_tax_type_filter(result: dict, tax_type_filter: str) -> dict:
        filtered = {}
        for coll_name, coll_data in result.items():
            if coll_name == "_guidance":
                filtered[coll_name] = coll_data
                continue
            items = [
                it for it in coll_data.get("items", [])
                if it.get("tax_type") and tax_type_filter in it["tax_type"]
            ]
            filtered[coll_name] = {
                **coll_data,
                "items": items,
                "filtered_by_tax_type": tax_type_filter,
            }
        return filtered

    @staticmethod
    def _fill_enforcement(result: dict, store=None) -> None:
        """본문 없는 집행기준을 집행기준 책자 보관소에서 채운다. 못 채운 것이 남으면 statute 컬렉션에 안내를
        단다 (빈 요약을 '내용 없음'으로 읽지 않도록). 국세청 검색이 법을 잘못 달아 둔 항목(주류면허법 항목을
        국세기본법으로 등)은 책자 쪽 법령명으로 바로잡는다 — 그래야 번호로 다시 찾을 때 엉뚱한 항목이 안 나온다."""
        statute = result.get("statute")
        if not isinstance(statute, dict):
            return
        targets = [it for it in statute.get("items", []) if it.get("doc_type") == "집행기준" and not it.get("summary")]
        if not targets:
            return
        import nts_enforcement as ne
        store = store or ne.default_store()
        filled = missing = 0
        for it in targets:
            law, title = it.get("law_name") or "", it.get("title") or ""
            rest = title[len(law):].strip() if law and title.startswith(law) else title
            m = re.match(r"(\S+)\s*(.*)$", rest)
            hit = store.find(law, m.group(1), m.group(2)) if m else None
            if not hit:
                missing += 1
                continue
            it["summary"] = _plain(hit["text"], _SNIPPET_MAX)
            it["source_book"] = f"{hit['book']} {hit['edition']}판"
            if hit["law"] != law:
                it["law_name"] = hit["law"]
                it["title"] = f"{hit['law']} {hit['no']} {hit['title'] or m.group(2)}".strip()
            filled += 1
        notes = ([_ENFORCEMENT_FILLED_NOTE] if filled else []) + ([_ENFORCEMENT_NO_BODY_NOTE] if missing else [])
        statute["안내"] = " / ".join(notes)

    @staticmethod
    def _attach_guidance(result: dict, keyword: str) -> dict:
        """검색 완료 후 status 계약을 붙인다.
        OK = 결과 있음 / NOT_FOUND = 검색은 성공했고 결과가 0건 (자료 부존재로 판단 가능).
        원천 접근·파싱 실패는 여기 오지 않고 예외로 전파된다 (UPSTREAM/PARSE_ERROR)."""
        colls = [v for v in result.values() if isinstance(v, dict) and "total_count" in v]
        total = sum(v.get("total_count", 0) for v in colls)
        if total == 0:
            result["status"] = "NOT_FOUND"
            result["_guidance"] = (
                f"'{keyword}'에 대한 검색 결과가 없습니다 (검색 자체는 정상 수행됨). "
                "검색어를 더 짧게(핵심 단어 위주로) 바꾸거나, "
                "동의어·유사 표현으로 다시 시도해 보세요. "
                "특정 컬렉션만 지정했다면 collections를 비워 전체 범위로 검색해 보는 것도 방법입니다."
            )
        else:
            result["status"] = "OK"
            # 기간·세목 필터는 클라이언트단 후처리라, 서버가 준 상위 N건이 전부 걸러지면
            # items가 비어도 total_count는 그대로다 — '그 조건의 자료가 없다'는 뜻이 아님을 명시
            if not any(v.get("items") for v in colls):
                applied = [k for k in ("filtered_by_date", "filtered_by_tax_type")
                           if any(k in v for v in colls)]
                if applied:
                    result["_guidance"] = (
                        f"서버 검색 결과는 총 {total}건이지만 클라이언트단 필터({', '.join(applied)})로 "
                        "가져온 상위 건이 모두 걸러졌습니다 — 해당 조건의 자료가 없다는 뜻이 "
                        "아닙니다. sort='date_desc'로 바꾸거나 view_count를 늘려 다시 조회하세요."
                    )
        return result


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.DEBUG)
    client = NtsTaxLawClient(verify_ssl=False)

    print("=== 1) 기본 검색 (최신순, 세목필터 적용) ===")
    result = client.search(
        "조정대상지역",
        collections=["question", "precedent"],
        view_count=5,
        sort="date_desc",
        tax_type_filter="양도소득세",
    )
    for coll_name, coll_data in result.items():
        if coll_name == "_guidance":
            print("안내:", coll_data)
            continue
        print(f"\n--- {coll_data['name_kr']} (총 {coll_data['total_count']}건, 필터 후 {len(coll_data['items'])}건) ---")
        for item in coll_data["items"]:
            print(f"- [{item['doc_type']}][{item['source_org']}] {item['title']} ({item['doc_no']}, {item['date']})")

    print("\n=== 2) 사건번호로 직접 조회 ===")
    detail = client.get_by_doc_no("조심-2023-서-9465")
    print(json.dumps(detail, ensure_ascii=False, indent=2)[:800])

    print("\n=== 3) 존재하지 않는 검색어 (0건 안내 확인) ===")
    empty = client.search("asdkfjalksdjflaksjdflkj0000없는검색어", view_count=3)
    print(empty.get("_guidance"))
