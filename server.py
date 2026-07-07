"""
server.py (v2)
국세법령정보시스템(taxlaw.nts.go.kr) 통합검색 MCP 서버

- 사전답변 / 서면질의 / 질의회신 (국세청·기획재정부·법제처)
- 조세심판원 심판결정례 + 국세청 심사청구 결정례 + 법원 판례
- 법령, 별표서식, 구 법령해석자료, 홈택스 상담사례
를 도구 호출로 통합검색합니다.

v2 개선사항: 페이지네이션, 사건번호 직접 조회, 정렬 옵션, 세목 필터,
응답 크기 관리, 세션 자동 재접속, 캐싱/요청 속도 제한, 로깅.
자세한 배경은 nts_tax_ruling_search.py 상단 주석 참고.

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

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

PORT = int(os.environ.get("PORT", 8000))

mcp = FastMCP(
    "nts-tax-ruling",
    instructions=(
        "대한민국 국세법령정보시스템(taxlaw.nts.go.kr) 통합검색 도구입니다. "
        "사전답변·서면질의·질의회신·조세심판원 심판청구·국세청 심사청구·법원판례·법령을 "
        "키워드로 검색하거나(nts_ruling_search), 사건번호로 바로 조회할 수 있습니다(nts_ruling_get_by_doc_no)."
    ),
    host="0.0.0.0",
    port=PORT,
    stateless_http=True,
)

# 클라이언트는 서버 프로세스 전역에서 재사용 (세션 쿠키·캐시 재사용을 위함)
# 사내망/프록시 환경에서 인증서 오류가 나면 환경변수 NTS_VERIFY_SSL=false 로 임시 우회 가능
_verify_ssl = os.environ.get("NTS_VERIFY_SSL", "true").lower() != "false"
_cache_ttl = int(os.environ.get("NTS_CACHE_TTL", "300"))
_min_interval = float(os.environ.get("NTS_MIN_REQUEST_INTERVAL", "0.5"))

_client = NtsTaxLawClient(
    verify_ssl=_verify_ssl,
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


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
