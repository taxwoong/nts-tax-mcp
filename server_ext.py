# -*- coding: utf-8 -*-
"""
server_ext.py — nts-tax-mcp 확장 진입점
기존 server.py의 도구 6개(국세 NTS + 지방세 olta)를 그대로 물려받고,
법제처 law.go.kr Open API 도구 5개를 추가한다. 커넥터 하나로 통합 운영.

실행: PORT=8734 LAW_API_OC=<발급받은 기관코드> python server_ext.py
(run_server.bat이 이 파일을 실행한다. server.py는 수정하지 않는다.)
"""
import logging
from typing import Optional

from server import mcp  # 기존 FastMCP 인스턴스 + 도구 6개 그대로 재사용
from law_go_kr import LawGoKrClient, LawGoKrError

logger = logging.getLogger("nts-tax-mcp.ext")
_law = LawGoKrClient()


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LawGoKrError as e:
        return {"오류": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.exception("law.go.kr 호출 실패")
        return {"오류": f"{type(e).__name__}: {e}"}


@mcp.tool()
def court_case_search(keyword: str, court: str = "", date_from: str = "",
                      date_to: str = "", display: int = 10, page: int = 1) -> dict:
    """법제처(law.go.kr) 판례 검색 — 대법원·하급심 판례를 키워드로 찾는다.

    국세청 시스템(nts_ruling_search)의 법원판례와 별개로, 법제처가 제공하는
    전체 법원 판례 DB를 검색한다. 세무 판례의 상고심 확정 여부 확인에도 사용.

    Args:
        keyword: 검색어 (예: "청산금 양도시기")
        court: "대법원" 또는 "하위법원" (빈값 = 전체)
        date_from: 선고일 시작 YYYYMMDD (선택)
        date_to: 선고일 종료 YYYYMMDD (선택)
        display: 결과 수 (기본 10)
        page: 페이지 번호
    """
    return _safe(_law.search_cases, keyword, court, date_from, date_to, display, page)


@mcp.tool()
def court_case_detail(case_serial: str, max_chars: int = 8000) -> dict:
    """법제처 판례 본문 조회 — court_case_search 결과의 '판례일련번호'로
    판시사항·판결요지·참조조문·판례내용 전문을 가져온다."""
    return _safe(_law.get_case, case_serial, max_chars)


@mcp.tool()
def law_interpretation_search(keyword: str, display: int = 10,
                              serial: str = "") -> object:
    """법제처 법령해석례 검색/조회.

    serial 없이 호출하면 키워드 검색(안건명·회신기관·회신일자 목록),
    serial(해석례일련번호)을 주면 질의요지·회답·이유 전문을 반환한다.
    """
    if serial:
        return _safe(_law.get_interpretation, serial)
    return _safe(_law.search_interpretations, keyword, display)


@mcp.tool()
def law_history_search(law_name: str, law_id: str = "", current_only: bool = False) -> object:
    """법령 연혁 조회 — 제정부터 현재까지 모든 시행본 목록(시행일자·공포번호·MST).

    세법은 개정이 잦아 예규·판례가 인용한 '당시 조문'을 봐야 할 때가 많다.
    이 도구로 시행본 목록을 확인하고, 특정 시점 조문은 law_article_as_of를 쓴다.

    Args:
        law_name: 법령명 (예: "부가가치세법")
        law_id: 법령ID로 본법만 필터 (예: 부가가치세법=001571). 같은 이름의
                시행령·시행규칙 혼입을 막으려면 지정 권장.
        current_only: True면 현행 법령 검색만 (법령ID·MST 확인용)
    """
    if current_only:
        return _safe(_law.search_laws, law_name)
    return _safe(_law.law_history, law_name, law_id)


@mcp.tool()
def law_article_as_of(law_name: str, as_of_date: str, article_no: str,
                      law_id: str = "", max_chars: int = 6000) -> dict:
    """특정 날짜에 시행 중이던 법령 조문 원문 — '그 시점의 법'을 가져온다.

    예규 회신일·판결 선고일 당시의 조문을 확인할 때 사용한다. 연혁 시행본 중
    as_of_date 이하 최대 시행일자 본을 자동 선택해 조문을 잘라 반환한다.

    Args:
        law_name: 법령명 (예: "소득세법 시행령")
        as_of_date: 기준일 YYYYMMDD (예: 예규 회신일 "20080715")
        article_no: 조번호 — "162" 또는 가지조문 "104의3" 형식 (패딩 없음)
        law_id: 법령ID 필터 (권장 — 본법/시행령 혼입 방지)
        max_chars: 원문 최대 길이 (기본 6000). 응답에 "잘림" 항목이 있으면
            거기 안내된 길이 이상으로 지정해 다시 호출하면 전문을 받는다.
    """
    return _safe(_law.law_article_as_of, law_name, as_of_date, article_no, law_id, max_chars)


@mcp.tool()
def admin_rule_search(keyword: str, serial: str = "", display: int = 10) -> object:
    """행정규칙(훈령·예규·고시) 검색/조회 — 기본통칙·조사사무처리규정·국세청 고시.

    serial 없이 호출하면 키워드 검색(규칙명·종류·소관부처·발령일자 목록),
    serial(일련번호)을 주면 본문 전문을 반환한다.
    예: "법인세법 기본통칙", "조사사무처리규정", "상속세 및 증여세 사무처리규정"
    """
    if serial:
        return _safe(_law.get_admin_rule, serial)
    return _safe(_law.search_admin_rules, keyword, display)


@mcp.tool()
def treaty_search(keyword: str, serial: str = "", display: int = 10) -> object:
    """조약 검색/조회 — 조세조약 원문·발효일 확인용.

    serial 없이 호출하면 키워드 검색(조약명·서명일·발효일 목록),
    serial(조약일련번호)을 주면 조약 본문을 반환한다.
    예: "홍콩 소득에 대한 조세", "대한민국과 미합중국 간의 조세"
    """
    if serial:
        return _safe(_law.get_treaty, serial)
    return _safe(_law.search_treaties, keyword, display)


@mcp.tool()
def ordinance_search(keyword: str, region: str = "", serial: str = "", display: int = 20) -> object:
    """자치법규(조례·규칙) 검색/조회 — 지방세 탄력세율·감면조례 확인용.

    serial 없이 호출하면 키워드 검색, region으로 지자체 필터(예: "서울", "용산구").
    serial(일련번호)을 주면 본문을 반환한다.
    예: keyword="시세 감면", region="서울" / keyword="도시계획세"
    """
    if serial:
        return _safe(_law.get_ordinance, serial)
    return _safe(_law.search_ordinances, keyword, region, display)


if __name__ == "__main__":
    logger.info("nts-tax-mcp 확장판 기동 — 기존 6개 + 법제처 8개 도구")
    mcp.run(transport="streamable-http")
