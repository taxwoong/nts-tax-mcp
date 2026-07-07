"""
server.py
국세법령정보시스템(taxlaw.nts.go.kr) 통합검색 MCP 서버

- 사전답변 / 서면질의 / 질의회신 (국세청·기획재정부·법제처)
- 조세심판원 심판결정례 + 국세청 심사청구 결정례 + 법원 판례
- 법령, 별표서식, 구 법령해석자료, 홈택스 상담사례
를 한 번의 도구 호출로 통합검색합니다.

로컬 실행:
    python server.py
    (기본: http://0.0.0.0:8000/mcp 로 streamable-http 서비스)

배포(Railway 등):
    환경변수 PORT 를 자동으로 읽어 바인딩합니다.
"""

import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from nts_tax_ruling_search import NtsTaxLawClient, COLLECTIONS

PORT = int(os.environ.get("PORT", 8000))

mcp = FastMCP(
    "nts-tax-ruling",
    instructions=(
        "대한민국 국세법령정보시스템(taxlaw.nts.go.kr) 통합검색 도구입니다. "
        "사전답변·서면질의·질의회신·조세심판원 심판청구·국세청 심사청구·법원판례·법령을 "
        "키워드로 한 번에 검색할 수 있습니다."
    ),
    host="0.0.0.0",
    port=PORT,
    stateless_http=True,
)

# 클라이언트는 서버 프로세스 전역에서 재사용 (세션 쿠키 재사용을 위함)
# 사내망/프록시 환경에서 인증서 오류가 나면 환경변수 NTS_VERIFY_SSL=false 로 임시 우회 가능
_verify_ssl = os.environ.get("NTS_VERIFY_SSL", "true").lower() != "false"
_client = NtsTaxLawClient(verify_ssl=_verify_ssl)


@mcp.tool()
def nts_ruling_search(
    keyword: str,
    collections: Optional[list] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    view_count: int = 20,
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
        date_from: 검색 시작일 YYYYMMDD (선택)
        date_to: 검색 종료일 YYYYMMDD (선택)
        view_count: 컬렉션별로 가져올 결과 개수 (기본 20)

    Returns:
        컬렉션별 총 건수와 결과 목록(제목, 문서번호, 출처기관, 날짜, 세목, 요지, 본문 등)
    """
    collection_codes = None
    if collections:
        collection_codes = [COLLECTIONS[c] for c in collections if c in COLLECTIONS]

    return _client.search(
        keyword=keyword,
        collections=collection_codes,
        date_from=date_from,
        date_to=date_to,
        view_count=view_count,
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
