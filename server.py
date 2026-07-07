"""
server.py (v3)
국세법령정보시스템(taxlaw.nts.go.kr) + 지방세법령정보시스템(olta.re.kr) 통합검색 MCP 서버

[국세] 사전답변 / 서면질의 / 질의회신 (국세청·기획재정부·법제처)
       조세심판원 심판결정례(국세) + 국세청 심사청구 결정례 + 법원 판례 + 법령
[지방세] 조세심판원 결정례(지방세) + 감사원 심사결정례 + 헌법재판소 결정례
         + 법원판례 + 법제처/행정안전부 유권해석 + 자치단체 질의회신

두 시스템은 서로 다른 사건번호 체계를 씁니다 (국세: 조심-YYYY-[지역청코드]-NNNN,
지방세: 조심YYYY지NNNN). 실제 겹치는 문서는 거의 없지만, 만일을 대비해
nts_and_olta_precedent_search 도구는 문서번호 정규화 기반으로 중복을 제거합니다.

로컬 실행:
    python server.py
    (기본: http://0.0.0.0:8000/mcp 로 streamable-http 서비스)

배포(Railway 등):
    환경변수 PORT 를 자동으로 읽어 바인딩합니다.
"""

import logging
import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from nts_tax_ruling_search import NtsTaxLawClient, COLLECTIONS
from olta_tax_ruling_search import OltaTaxLawClient, ALL_CATEGORY_KEYS, normalize_doc_no

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

PORT = int(os.environ.get("PORT", 8000))

mcp = FastMCP(
    "nts-tax-ruling",
    instructions=(
        "대한민국 국세법령정보시스템(taxlaw.nts.go.kr) + 지방세법령정보시스템(olta.re.kr) "
        "통합검색 도구입니다. 국세(사전답변·질의회신·조세심판원·법원판례)는 nts_ruling_search, "
        "지방세(취득세·재산세 등 조세심판원·감사원·헌재·자치단체 질의회신)는 olta_ruling_search를 "
        "사용하세요. 국세/지방세를 모두 아우르는 질문이면 nts_and_olta_precedent_search로 "
        "한 번에 검색하고 중복 없이 결과를 받을 수 있습니다."
    ),
    host="0.0.0.0",
    port=PORT,
    stateless_http=False,
)

# 클라이언트는 서버 프로세스 전역에서 재사용 (세션 쿠키·캐시 재사용을 위함)
# 사내망/프록시 환경에서 인증서 오류가 나면 환경변수 *_VERIFY_SSL=false 로 임시 우회 가능
_nts_verify_ssl = os.environ.get("NTS_VERIFY_SSL", "true").lower() != "false"
_olta_verify_ssl = os.environ.get("OLTA_VERIFY_SSL", "true").lower() != "false"
_cache_ttl = int(os.environ.get("NTS_CACHE_TTL", "300"))
_min_interval = float(os.environ.get("NTS_MIN_REQUEST_INTERVAL", "0.5"))

_client = NtsTaxLawClient(
    verify_ssl=_nts_verify_ssl,
    cache_ttl=_cache_ttl,
    min_request_interval=_min_interval,
)

_olta_client = OltaTaxLawClient(
    verify_ssl=_olta_verify_ssl,
    cache_ttl=_cache_ttl,
    min_request_interval=_min_interval,
)


@mcp.tool()
def nts_ruling_search(
    keyword: str,
    collections: Optional[list] = None,
    page: int = 1,
    view_count: int = 20,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort: str = "relevance",
    tax_type_filter: Optional[str] = None,
    include_full_text: bool = True,
) -> dict:
    """
    국세법령정보시스템 통합검색.

    사전답변·서면질의·질의회신(국세청/기획재정부/법제처), 조세심판원 심판청구,
    국세청 심사청구, 법원 판례, 법령을 키워드로 검색합니다.

    Args:
        keyword: 검색어 (예: "조정대상지역", "부당행위계산 부인")
        collections: 검색할 범위. 생략시 전체 검색.
            선택 가능 값: "form"(별표서식), "statute"(법령),
            "ruling"(사전답변·서면질의·질의회신), "precedent"(심판·심사·판례),
            "old_ruling"(구 법령해석자료), "intl"(국제조세 해설), "hometax"(홈택스 상담사례)
        page: 페이지 번호 (1부터 시작). "더 보여줘" 같은 후속 요청시 2, 3...으로 증가
        view_count: 컬렉션별로 가져올 결과 개수 (기본 20)
        date_from: 검색 시작일 YYYYMMDD (선택)
        date_to: 검색 종료일 YYYYMMDD (선택)
        sort: "relevance"(정확도순, 기본) | "date_desc"(최신순) | "date_asc"(오래된순)
        tax_type_filter: 특정 세목만 보고 싶을 때 (예: "양도소득세", "부가가치세").
            결과의 세목명에 이 문자열이 포함된 것만 남깁니다.
        include_full_text: False로 주면 본문 전문을 생략하고 요약만 반환해 응답을 가볍게 만듭니다.
            대략적인 목록만 먼저 훑어보고 싶을 때 False로 호출한 뒤,
            필요한 문서만 nts_ruling_get_by_doc_no로 본문을 확인하는 방식을 권장합니다.

    Returns:
        컬렉션별 총 건수와 결과 목록(제목, 문서번호, 출처기관, 날짜, 세목, 요지, 본문 등).
        검색 결과가 전혀 없으면 "_guidance" 키에 안내 메시지가 포함됩니다.
    """
    collection_codes = None
    if collections:
        collection_codes = [COLLECTIONS[c] for c in collections if c in COLLECTIONS]

    return _client.search(
        keyword=keyword,
        collections=collection_codes,
        page=page,
        view_count=view_count,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        tax_type_filter=tax_type_filter,
        include_full_text=include_full_text,
    )


@mcp.tool()
def nts_ruling_get_by_doc_no(doc_no: str) -> dict:
    """
    사건번호/문서번호로 특정 문서를 바로 조회합니다.

    이미 사건번호를 알고 있을 때(예: 검색 결과에서 봤거나, 다른 자료에서 인용된 경우)
    다시 키워드 검색을 거치지 않고 바로 본문을 확인할 때 사용합니다.

    Args:
        doc_no: 사건번호/문서번호. 예:
            "조심-2023-서-9465" (조세심판원 심판결정례)
            "서면-2019-법규재산-4276" (국세청 서면질의)
            "기획재정부 재산세제과-73" (기획재정부 유권해석)

    Returns:
        found: 일치하는 문서를 찾았는지 여부
        items: 일치하는 문서 목록 (본문 포함)
        message: 못 찾은 경우 안내 메시지
    """
    return _client.get_by_doc_no(doc_no)


@mcp.tool()
def olta_ruling_search(
    keyword: str,
    categories: Optional[list] = None,
    view_count: int = 20,
    tax_type_filter: Optional[str] = None,
) -> dict:
    """
    지방세법령정보시스템(olta.re.kr) 통합검색.

    취득세·재산세·자동차세·지방소득세·등록면허세 등 지방세 관련 조세심판원 결정례,
    감사원 심사결정례, 헌법재판소 결정례, 법원판례, 법제처/행정안전부 유권해석,
    자치단체 질의회신을 키워드로 검색합니다.

    국세(양도소득세·법인세·부가가치세 등)는 이 도구가 아니라 nts_ruling_search를 사용하세요.

    Args:
        keyword: 검색어 (예: "취득세 주택", "재산세 과세기준일")
        categories: 검색할 범위. 생략시 전체 검색.
            선택 가능 값: "court"(법원판례), "moi_ruling"(행정안전부 유권해석),
            "mole_ruling"(법제처해석), "tax_tribunal"(조세심판원 결정례),
            "audit"(감사원 결정례), "constitutional"(헌법재판소 결정례),
            "local_gov_ruling"(자치단체 질의회신)
        view_count: 카테고리별 최대 결과 개수 (기본 20). 사이트가 카테고리당
            미리보기 몇 건만 내려주는 구조라 그 이상은 확보되지 않을 수 있습니다.
        tax_type_filter: 세목명에 이 문자열이 포함된 것만 남김 (예: "취득세", "재산세")

    Returns:
        카테고리별 총 건수와 결과 목록(제목, 사건번호, 날짜, 세목, 처리결과, 요지).
        검색 결과가 전혀 없으면 "_guidance" 키에 안내 메시지가 포함됩니다.
    """
    category_codes = None
    if categories:
        category_codes = [c for c in categories if c in ALL_CATEGORY_KEYS]

    return _olta_client.search(
        keyword=keyword,
        categories=category_codes,
        view_count=view_count,
        tax_type_filter=tax_type_filter,
    )


@mcp.tool()
def nts_and_olta_precedent_search(
    keyword: str,
    view_count: int = 20,
    tax_type_filter: Optional[str] = None,
) -> dict:
    """
    국세(nts) + 지방세(olta) 조세심판원 관련 결정례를 한 번에 검색하고,
    문서번호 기준으로 중복을 제거해 하나로 합쳐서 반환합니다.

    국세와 지방세 조세심판원은 사건번호 체계 자체가 달라서(국세: 조심-YYYY-지역청코드-NNNN,
    지방세: 조심YYYY지NNNN) 실제로 겹치는 경우는 거의 없지만, 두 시스템을 동시에 확인하고
    싶을 때 이 도구 하나로 편리하게 조회할 수 있습니다. 세목이 국세인지 지방세인지
    애매하거나, 세목을 특정하지 않고 폭넓게 찾고 싶을 때 사용하세요.

    Args:
        keyword: 검색어
        view_count: 각 소스에서 가져올 결과 개수 (기본 20)
        tax_type_filter: 세목 필터 (예: "양도소득세" 또는 "취득세")

    Returns:
        nts_precedent: 국세법령정보시스템의 심판·심사·판례 결과
        olta_precedent: 지방세법령정보시스템의 조세심판원·감사원·헌재·법원 결과
            (nts_precedent와 문서번호가 겹치는 항목은 제외됨)
        duplicates_removed: 실제로 제외된 중복 건수
    """
    nts_result = _client.search(
        keyword=keyword,
        collections=["precedent"],
        view_count=view_count,
        tax_type_filter=tax_type_filter,
        include_full_text=False,
    )
    nts_items = nts_result.get("precedent", {}).get("items", [])
    nts_doc_nos = {normalize_doc_no(it.get("doc_no", "")) for it in nts_items if it.get("doc_no")}

    olta_result = _olta_client.search(
        keyword=keyword,
        categories=["tax_tribunal", "audit", "constitutional", "court"],
        view_count=view_count,
        tax_type_filter=tax_type_filter,
        exclude_doc_nos=nts_doc_nos,
    )

    # 실제 제외된 건수 계산을 위해 필터 전 개수와 비교
    olta_result_unfiltered = _olta_client.search(
        keyword=keyword,
        categories=["tax_tribunal", "audit", "constitutional", "court"],
        view_count=view_count,
        tax_type_filter=tax_type_filter,
    )
    before = sum(len(v.get("items", [])) for k, v in olta_result_unfiltered.items() if k != "_guidance")
    after = sum(len(v.get("items", [])) for k, v in olta_result.items() if k != "_guidance")

    return {
        "nts_precedent": nts_result.get("precedent"),
        "olta_precedent": {k: v for k, v in olta_result.items()},
        "duplicates_removed": before - after,
    }


@mcp.tool()
def olta_collection_search(
    keyword: str,
    category: str,
    page: int = 1,
    view_count: int = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort: str = "relevance",
) -> dict:
    """
    지방세법령정보시스템 특정 카테고리의 전체 목록 검색 (페이지네이션·기간·정렬 지원).

    olta_ruling_search는 카테고리당 미리보기 3건만 반환하지만, 이 도구는 지정한 한 카테고리를
    페이지 단위(10건씩)로 깊게 탐색할 수 있고 기간 필터와 최신순 정렬도 서버측에서 지원합니다.
    특정 카테고리에서 많은 결과를 봐야 하거나 "더 보여줘", "2023년 것만" 같은 요청에 사용하세요.

    Args:
        keyword: 검색어
        category: "tax_tribunal"(조세심판원), "audit"(감사원), "constitutional"(헌재),
            "court"(법원판례), "mole_ruling"(법제처해석), "moi_ruling"(행안부 유권해석) 중 하나
        page: 페이지 번호 (1부터, 페이지당 10건)
        view_count: 반환할 결과 개수 (최대 10)
        date_from / date_to: 검색 기간 YYYYMMDD (서버측 필터)
        sort: "relevance"(정확도순, 기본) | "date_desc"(최신순)

    Returns:
        해당 카테고리의 총 건수, 페이지 번호, 결과 목록
    """
    return _olta_client.search_collection(
        keyword=keyword,
        category=category,
        page=page,
        view_count=view_count,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
    )


@mcp.tool()
def olta_get_detail(category: str, doc_id: str) -> dict:
    """
    지방세법령정보시스템 문서의 본문 전문을 조회합니다.

    olta_ruling_search / olta_collection_search 결과의 doc_id를 넣으면
    결정요지·참조조문·처분개요·판단 등 본문 전체를 가져옵니다.

    Args:
        category: "tax_tribunal"(조세심판원 결정례) 또는 "constitutional"(헌법재판소 결정례).
            법원판례·유권해석 등 다른 카테고리는 아직 본문 조회 미지원 (요지 필드 활용).
        doc_id: 검색 결과 항목의 doc_id 값

    Returns:
        found: 성공 여부, url: 원문 페이지 주소, content: 본문 전문 텍스트
    """
    return _olta_client.get_detail(category=category, doc_id=doc_id)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
