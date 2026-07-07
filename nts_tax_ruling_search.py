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

logger = logging.getLogger("nts_tax_ruling_search")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

BASE_URL = "https://taxlaw.nts.go.kr"
SEARCH_ENTRY_URL = f"{BASE_URL}/qt/USEQTA001M.do?ntstDcmClCd=01"
ACTION_URL = f"{BASE_URL}/action.do"

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

DEFAULT_CACHE_TTL = 300
DEFAULT_MIN_INTERVAL = 0.5


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
            date_from / date_to: 검색 기간 (YYYYMMDD)
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

        param_data = {
            "schVcb": keyword,
            "startCount": start_count,
            "collection": ",".join(collections),
            "wnKey": "",
            "searchType": "",
            "sortField": SORT_OPTIONS[sort],
            "ntstTlawClCdList": [],
            "icldVcbCtl": [],
            "exclVcbCtl": [],
            "rltnStttCtl": [],
            "schDtBase": date_base,
            "viewCount": str(view_count),
            "prtsSprcChiefJdgmYn": "",
            "prtsAttrYrCtl": [],
            "prtsPrgrStatCtl": [],
            "mainIdCtl": [],
            "useSynonymYn": "N",
        }
        if date_from:
            param_data["bltnStrtDtm"] = date_from
        if date_to:
            param_data["bltnEndDtm"] = date_to

        raw = self._post_search(param_data)
        result = self._parse(raw, include_full_text=include_full_text)

        if tax_type_filter:
            result = self._apply_tax_type_filter(result, tax_type_filter)

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

            matched = []
            for coll_name, coll_data in result.items():
                if coll_name == "_guidance":
                    continue
                for item in coll_data.get("items", []):
                    if item.get("doc_no") == doc_no:
                        matched.append(item)

            if matched:
                return {"found": True, "items": matched}

        return {
            "found": False,
            "items": [],
            "message": (
                f"'{doc_no}'와 정확히 일치하는 문서를 찾지 못했습니다. "
                "문서번호 표기(띄어쓰기, 하이픈 등)를 다시 확인하시거나, "
                "일반 키워드 검색(search)을 이용해 보세요."
            ),
        }

    def _parse(self, raw: dict, include_full_text: bool = True) -> dict:
        try:
            srv = raw["data"]["ASEISA001MR01"]["searchResultVO"]
        except (KeyError, TypeError) as e:
            logger.error("예상치 못한 응답 구조입니다: %s / raw keys=%s", e, list(raw.keys()))
            return {"error": "예상치 못한 응답 구조", "raw_status": raw.get("status")}

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
        }
        if include_full_text:
            item["content"] = strip_hl(it.get("CNTN"))
            item["detail_content"] = strip_hl(it.get("FILE_CN"))
        return item

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
        total = sum(
            v.get("total_count", 0) for k, v in result.items() if isinstance(v, dict) and "total_count" in v
        )
        if total == 0:
            result["_guidance"] = (
                f"'{keyword}'에 대한 검색 결과가 없습니다. "
                "검색어를 더 짧게(핵심 단어 위주로) 바꾸거나, "
                "동의어·유사 표현으로 다시 시도해 보세요. "
                "특정 컬렉션만 지정했다면 collections를 비워 전체 범위로 검색해 보는 것도 방법입니다."
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
