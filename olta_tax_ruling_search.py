"""
olta_tax_ruling_search.py
지방세법령정보시스템(olta.re.kr) 통합검색 API 클라이언트

취득세·재산세·자동차세·지방소득세·등록면허세 등 지방세 관련:
- 조세심판원 결정례(지방세), 감사원 심사결정례, 헌법재판소 결정례
- 법원판례(대법원/하급심), 법제처 유권해석, 행정안전부 유권해석, 자치단체 질의회신
를 한 번의 검색으로 동시에 가져옵니다.

브라우저 없이 requests + BeautifulSoup으로 동작합니다 (서버가 HTML을 반환하는 구조).
"""

import logging
import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("olta_tax_ruling_search")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

BASE_URL = "https://olta.re.kr"
SEARCH_ENTRY_URL = f"{BASE_URL}/explainInfo/decisionList.do?menuNo=90010100&upperMenuId=90010000"
SEARCH_URL = f"{BASE_URL}/search/PU_0003_search.jsp"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 화면상 문서구분 코드 (JS의 alarmTypeText 함수에서 확인)
CATEGORY_NAMES = {
    "10000": "법원판례",
    "20000": "행정안전부 유권해석",
    "30000": "법제처 유권해석",
    "40000": "조세심판원 결정례",
    "60000": "감사원 심사결정례",
    "70000": "헌법재판소 결정례",
    "80000": "자치단체 질의회신",
}

# 카테고리 한글명 -> 영문 키 매핑 (nts_tax_ruling_search.py의 COLLECTIONS와 이름 체계를 맞춤)
# 주의: 아래 한글명은 실제 검색결과 화면(p.se_title)에 표시되는 문구와 정확히 일치해야 합니다.
CATEGORY_KEY_BY_KR = {
    "법원판례": "court",
    "행정안전부 유권해석": "moi_ruling",
    "법제처해석": "mole_ruling",
    "조세심판원 결정례": "tax_tribunal",
    "감사원 결정례": "audit",
    "헌법재판소 결정례": "constitutional",
    "자치단체 질의회신": "local_gov_ruling",
}
ALL_CATEGORY_KEYS = list(CATEGORY_KEY_BY_KR.values())

# 컬렉션별 전체 목록 검색용 서버 collection 코드 (doCollection 함수 실측)
COLLECTION_CODES = {
    "constitutional": "ordinance",   # 헌법재판소 결정례
    "court": "sentencing",           # 법원판례
    "tax_tribunal": "screen",        # 조세심판원 결정례
    "audit": "evaluation",           # 감사원 결정례
    "mole_ruling": "legal",          # 법제처해석
    "moi_ruling": "authoritative",   # 행정안전부 유권해석
}

# 카테고리별 상세(본문) 페이지 URL 패턴 (search.js 팝업 함수 실측)
DETAIL_URL_PATTERNS = {
    "tax_tribunal": "/explainInfo/judgeDecisionDetail.do?num={doc_id}",
    "constitutional": "/explainInfo/constitutionDcnDetail.do?num={doc_id}",
    # 법원판례는 decisionDtlView.do?num={a}&relationshipNum={b} 형태 (인자 2개 필요)
}

SORT_OPTIONS = {
    "relevance": "RANK",
    "date_desc": "DATE",
}

DEFAULT_CACHE_TTL = 300
DEFAULT_MIN_INTERVAL = 0.5

_META_PATTERN = re.compile(r"^(?P<case_no>.*?)\((?P<date>\d{4}[.\-]?\d{2}[.\-]?\d{2}|\d{8})\)\s*$")


class OltaTaxLawClient:
    """지방세법령정보시스템 통합검색 클라이언트 (세션 재사용 + 캐싱 + 재접속)"""

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
        import json
        return json.dumps(kwargs, sort_keys=True, ensure_ascii=False)

    def _post_search(self, keyword: str) -> str:
        if not self._bootstrapped:
            self._bootstrap()

        self._throttle()

        def _do_request():
            resp = self.session.post(
                SEARCH_URL,
                data={"csrfToken": "null", "query": keyword, "querySub": keyword},
                headers={"Referer": SEARCH_ENTRY_URL},
                verify=self.verify_ssl,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            resp.encoding = "utf-8"
            return resp.text

        try:
            text = _do_request()
            if "se_title" not in text and "검색" not in text:
                raise ValueError("예상한 검색결과 마커가 응답에 없습니다 (세션 만료 가능성)")
            return text
        except (ValueError, requests.exceptions.RequestException) as e:
            logger.warning("검색 요청 실패, 세션 재접속 후 재시도합니다: %s", e)
            self._bootstrap(force=True)
            self._throttle()
            return _do_request()

    def search(
        self,
        keyword: str,
        categories: Optional[list] = None,
        view_count: int = 20,
        tax_type_filter: Optional[str] = None,
        exclude_doc_nos: Optional[set] = None,
        use_cache: bool = True,
    ) -> dict:
        """
        키워드로 지방세법령정보시스템 통합검색을 수행합니다.

        Args:
            keyword: 검색어
            categories: ALL_CATEGORY_KEYS 중 원하는 것만 선택 (기본값: 전체)
                "court"(법원판례), "moi_ruling"(행안부 유권해석), "mole_ruling"(법제처 유권해석),
                "tax_tribunal"(조세심판원 결정례), "audit"(감사원 심사결정례),
                "constitutional"(헌법재판소 결정례), "local_gov_ruling"(자치단체 질의회신)
            view_count: 카테고리별 최대 결과 개수 (페이지에 표시되는 미리보기 개수만큼만 확보 가능,
                서버가 카테고리당 3~5건의 미리보기만 내려주므로 그 이상은 잘릴 수 있습니다)
            tax_type_filter: 세목명에 이 문자열이 포함된 것만 남김 (예: "취득세")
            exclude_doc_nos: 이미 다른 소스(예: 국세법령정보시스템)에서 확인된 문서번호 집합.
                정규화 후 일치하는 항목은 결과에서 제외합니다 (중복 방지).
            use_cache: 캐시 사용 여부
        """
        cache_key = None
        if use_cache:
            cache_key = self._cache_key(keyword=keyword, categories=categories, view_count=view_count)
            cached = self._cache.get(cache_key)
            if cached and (time.time() - cached[0]) < self._cache_ttl:
                logger.info("캐시된 결과 반환: %s", keyword)
                result = cached[1]
            else:
                result = self._search_uncached(keyword)
                self._cache[cache_key] = (time.time(), result)
        else:
            result = self._search_uncached(keyword)

        # 카테고리 필터
        if categories:
            result = {k: v for k, v in result.items() if k in categories}

        # 세목 필터 / view_count 제한 / 중복 제외는 매번 새로 적용 (캐시된 원본은 건드리지 않음)
        result = {k: dict(v, items=list(v["items"])) for k, v in result.items()}

        if tax_type_filter:
            for v in result.values():
                v["items"] = [it for it in v["items"] if it.get("tax_type") and tax_type_filter in it["tax_type"]]

        if exclude_doc_nos:
            normalized_exclude = {normalize_doc_no(d) for d in exclude_doc_nos}
            for v in result.values():
                v["items"] = [
                    it for it in v["items"]
                    if normalize_doc_no(it.get("doc_no", "")) not in normalized_exclude
                ]

        for v in result.values():
            v["items"] = v["items"][:view_count]

        total = sum(v.get("total_count", 0) for v in result.values())
        if total == 0:
            result["_guidance"] = (
                f"'{keyword}'에 대한 검색 결과가 없습니다. 검색어를 더 짧게 바꾸거나 "
                "동의어로 다시 시도해 보세요."
            )

        return result

    def search_collection(
        self,
        keyword: str,
        category: str,
        page: int = 1,
        view_count: int = 10,
        date_from: Optional[str] = None,   # YYYYMMDD
        date_to: Optional[str] = None,     # YYYYMMDD
        sort: str = "relevance",           # relevance | date_desc
    ) -> dict:
        """
        특정 카테고리의 전체 목록을 페이지네이션·기간·정렬과 함께 검색합니다.
        (통합검색 미리보기 3건 한계를 넘어 서버측에서 직접 필터링)

        Args:
            keyword: 검색어
            category: COLLECTION_CODES의 키 중 하나
            page: 페이지 번호 (1부터, 페이지당 10건 서버 고정)
            view_count: 반환할 결과 개수
            date_from / date_to: 검색 기간 YYYYMMDD (서버측 필터, 실측 검증됨)
            sort: "relevance"(정확도순) | "date_desc"(최신순)
        """
        if category not in COLLECTION_CODES:
            raise ValueError(
                f"category는 {list(COLLECTION_CODES.keys())} 중 하나여야 합니다 "
                f"(자치단체 질의회신은 통합검색에 노출되지 않아 지원 불가)"
            )
        if sort not in SORT_OPTIONS:
            raise ValueError(f"sort는 {list(SORT_OPTIONS.keys())} 중 하나여야 합니다")

        if not self._bootstrapped:
            self._bootstrap()
        self._throttle()

        def fmt_date(d, default):
            if not d:
                return default
            return f"{d[:4]}.{d[4:6]}.{d[6:8]}"

        start_count = (page - 1) * 10  # 서버 페이지 단위 10건 고정 (doPaging 실측)

        resp = self.session.post(
            SEARCH_URL,
            data={
                "searchType": "1",
                "detailSearchIsOnOff": "on",
                "query": keyword,
                "startCount": str(start_count),
                "sort": SORT_OPTIONS[sort],
                "collection": COLLECTION_CODES[category],
                "startDate": fmt_date(date_from, "1970.01.01"),
                "endDate": fmt_date(date_to, time.strftime("%Y.%m.%d")),
                "searchField": "ALL",
                "reQuery": "",
                "taxTitleStr": "",
                "range": "ALL",
                "brdNm": "",
            },
            headers={"Referer": SEARCH_ENTRY_URL},
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        resp.encoding = "utf-8"

        parsed = self._parse(resp.text)
        # 컬렉션 지정 검색이므로 해당 카테고리만 반환됨 (혹은 이름이 매칭되는 것)
        data = parsed.get(category)
        if data is None:
            non_meta = {k: v for k, v in parsed.items() if k != "_guidance"}
            data = next(iter(non_meta.values()), {"name_kr": category, "total_count": 0, "items": []})

        data["items"] = data.get("items", [])[:view_count]
        data["page"] = page
        return data

    def get_detail(self, category: str, doc_id: str) -> dict:
        """
        문서 본문 전문을 조회합니다. (조세심판원/헌법재판소 지원)

        Args:
            category: "tax_tribunal" 또는 "constitutional"
            doc_id: 검색 결과의 doc_id 필드 값
        """
        pattern = DETAIL_URL_PATTERNS.get(category)
        if pattern is None:
            return {
                "found": False,
                "message": (
                    f"'{category}' 카테고리는 본문 조회를 지원하지 않습니다 "
                    f"(지원: {list(DETAIL_URL_PATTERNS.keys())}). "
                    "법원판례 등은 검색 결과의 요지(summary)를 활용해 주세요."
                ),
            }

        if not self._bootstrapped:
            self._bootstrap()
        self._throttle()

        url = BASE_URL + pattern.format(doc_id=doc_id)
        resp = self.session.get(url, verify=self.verify_ssl, timeout=self.timeout)
        resp.raise_for_status()
        resp.encoding = "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")
        content_div = soup.find("div", class_="cont") or soup.find("div", id="content") or soup.body
        text = content_div.get_text("\n", strip=True) if content_div else ""

        if len(text) < 100:
            return {"found": False, "message": "본문을 추출하지 못했습니다.", "url": url}

        return {"found": True, "url": url, "content": text}

    def _search_uncached(self, keyword: str) -> dict:
        html = self._post_search(keyword)
        return self._parse(html)

    @staticmethod
    def _parse(html: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        result = {}

        for title_tag in soup.select("p.se_title"):
            header_text = title_tag.get_text(strip=True)
            m = re.match(r"^(.*?)\(총([\d,]+)건\)$", header_text)
            if not m:
                continue
            name_kr, count_str = m.group(1), m.group(2)
            key = CATEGORY_KEY_BY_KR.get(name_kr)
            if key is None:
                logger.warning("알 수 없는 카테고리 헤더: %s", name_kr)
                continue

            total_count = int(count_str.replace(",", ""))
            ul = title_tag.find_next("ul", class_="search_out")
            items = []
            if ul:
                for li in ul.find_all("li", recursive=False):
                    item = OltaTaxLawClient._clean_item(li)
                    if item:
                        items.append(item)

            result[key] = {"name_kr": name_kr, "total_count": total_count, "items": items}

        return result

    @staticmethod
    def _clean_item(li) -> Optional[dict]:
        # 메타 정보 컨테이너: 법원판례는 <div class="top">, 나머지는 첫 번째 <p>
        meta = li.find("div", class_="top") or li.find("p")
        tt_p = li.find("p", class_="tt")
        txt_p = li.find("p", class_="txt")
        if meta is None or tt_p is None:
            return None

        part_span = meta.find("span", class_="part")
        label_span = meta.find("span", class_="label")
        court_span = meta.find("span", class_="part_r01")  # 법원판례에서 법원 급(대법원/하급심)
        tax_type = part_span.get_text(strip=True) if part_span else None
        result_label = label_span.get_text(strip=True) if label_span else None
        court_level = court_span.get_text(strip=True) if court_span else None

        # part/label/court span을 제외한 나머지 텍스트에서 "사건번호(날짜)" 패턴 추출
        excluded = {id(part_span), id(label_span), id(court_span)}
        middle_parts = []
        for child in meta.children:
            if id(child) in excluded:
                continue
            text = child.get_text(strip=True) if hasattr(child, "get_text") else str(child).strip()
            if text:
                middle_parts.append(text)
        middle_text = " ".join(middle_parts).strip()

        doc_no, date = None, None
        m = _META_PATTERN.match(middle_text)
        if m:
            doc_no = m.group("case_no").strip()
            date = re.sub(r"[.\-]", "", m.group("date"))  # 2025.08.14 -> 20250814로 통일
        else:
            doc_no = middle_text or None

        title_link = tt_p.find("a")
        title = title_link.get_text(strip=True) if title_link else tt_p.get_text(strip=True)
        onclick = title_link.get("onclick") if title_link else None
        # 팝업 함수의 마지막 숫자 인자가 문서 ID (decisionDtlpopUp(a, b, null) 형태도 있음)
        doc_id = None
        if onclick:
            nums = re.findall(r"\d{6,}", onclick)
            doc_id = nums[-1] if nums else None

        summary = None
        if txt_p:
            summary_link = txt_p.find("a")
            summary = summary_link.get_text(strip=True) if summary_link else txt_p.get_text(strip=True)

        item = {
            "title": title,
            "doc_no": doc_no,
            "date": date,
            "tax_type": tax_type,
            "result": result_label,
            "summary": summary or None,
            "doc_id": doc_id,
        }
        if court_level:
            item["court_level"] = court_level
        return item


def normalize_doc_no(doc_no: str) -> str:
    """문서번호 비교용 정규화 — 공백/하이픈 등 구분자를 제거해 표기 차이를 흡수합니다.
    예: '조심-2023-서-9465' -> '조심2023서9465' """
    if not doc_no:
        return ""
    return re.sub(r"[\s\-–—,()]", "", doc_no)


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.DEBUG)
    client = OltaTaxLawClient(verify_ssl=False)

    print("=== 지방세법령정보시스템 검색: '취득세 주택' ===")
    result = client.search("주택", categories=["tax_tribunal", "constitutional", "audit"], view_count=3)
    for key, data in result.items():
        if key == "_guidance":
            print("안내:", data)
            continue
        print(f"\n--- {data['name_kr']} (총 {data['total_count']}건) ---")
        for item in data["items"]:
            print(f"- [{item['tax_type']}] {item['title']} ({item['doc_no']}, {item['date']}) {item['result']}")
