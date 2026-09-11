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

import json
import logging
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
        """
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
                include_full_text=include_full_text,
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

        param_data = {
            "schVcb": keyword,
            "startCount": start_count,
            "collection": ",".join(collections),
            "wnKey": "",
            "searchType": "",
            "sortField": SORT_OPTIONS[sort],
            "ntstTlawClCdList": server_tax_codes,
            "icldVcbCtl": [],
            "exclVcbCtl": [],
            "rltnStttCtl": [],
            "schDtBase": date_base,
            "viewCount": str(fetch_view_count),
            "prtsSprcChiefJdgmYn": "",
            "prtsAttrYrCtl": [],
            "prtsPrgrStatCtl": [],
            "mainIdCtl": [],
            "useSynonymYn": "N",
        }
        # 주의: bltnStrtDtm/bltnEndDtm 등 기간 관련 서버 파라미터는 실제 UI에 대응하는
        # 필드를 확인하지 못해 의도적으로 제외했습니다 (잘못된 값 전송시 검색 자체가
        # 0건으로 깨지는 문제가 있었습니다). 기간 필터는 아래에서 클라이언트단으로 처리합니다.

        raw = self._post_search(param_data)
        result = self._parse(raw, include_full_text=include_full_text)

        if client_tax_filter:
            result = self._apply_tax_type_filter(result, client_tax_filter)

        if date_from or date_to:
            result = self._apply_date_filter(result, date_from, date_to)
            # 내부적으로 더 가져온 결과를 다시 요청한 view_count 만큼으로 잘라냅니다.
            for coll_name, coll_data in result.items():
                if coll_name == "_guidance" or not isinstance(coll_data, dict):
                    continue
                coll_data["items"] = coll_data.get("items", [])[:view_count]

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
        for attempt_view_count in (view_count, max(view_count, 50)):
            result = self.search(
                keyword=doc_no,
                collections=["question", "precedent"],
                view_count=attempt_view_count,
                use_cache=True,
            )

            # 표기 차이(하이픈·띄어쓰기)로 실존 문서를 놓치면 status NOT_FOUND가
            # '자료 부존재'로 해석되므로, verify_citations와 같은 정규화 기준으로 비교한다
            want = normalize_doc_no(doc_no)
            matched = []
            for coll_name, coll_data in result.items():
                if not isinstance(coll_data, dict):  # status·_guidance 등 메타 키 스킵
                    continue
                for item in coll_data.get("items", []):
                    if item.get("doc_no") and normalize_doc_no(item["doc_no"]) == want:
                        matched.append(item)

            if matched:
                return {"status": "OK", "found": True, "items": matched}

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
                texts.append(txt)
            except HwpExtractError as e:
                # 붙임이 HWP가 아니면 전문을 못 읽는다 — 조용히 빈 값을 주면
                # '자료 없음'으로 오해되므로 형식을 명시해 남긴다.
                info["오류"] = str(e)
                logger.warning("붙임 추출 실패 (fleId=%s): %s", file_id, e)
            files.append(info)

        if not texts:
            return {"status": "PARSE_ERROR", "text": "", "files": files,
                    "message": "붙임을 받았으나 본문 텍스트를 추출하지 못했습니다."}

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
                    self._clean_item(it, include_full_text=include_full_text)
                    for it in coll.get("resultList", [])
                ],
            }
        return result

    @staticmethod
    def _clean_item(it: dict, include_full_text: bool = True) -> dict:
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
