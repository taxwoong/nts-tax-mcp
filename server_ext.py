# -*- coding: utf-8 -*-
"""
server_ext.py — nts-tax-mcp 확장 진입점
기존 server.py의 도구 8개(국세 NTS + 붙임 전문 + 지방세 olta + 인용검증)를 그대로 물려받고,
법제처 law.go.kr Open API 도구 11개를 추가한다. 커넥터 하나로 통합 운영.

실행: PORT=8734 LAW_API_OC=<발급받은 기관코드> python server_ext.py
(run_server.bat이 이 파일을 실행한다. server.py는 수정하지 않는다.)
"""
import logging
from typing import Optional

import requests

from server import mcp  # 기존 FastMCP 인스턴스 + 도구 그대로 재사용
from law_go_kr import (LawGoKrClient, LawGoKrError, LawGoKrAuthError, LawGoKrNotFound,
                       LawGoKrUpstreamError, _today_kst, install_oc_log_mask, mask_oc)

logger = logging.getLogger("nts-tax-mcp.ext")
_law = LawGoKrClient()
# server.py가 import 시점에 logging.basicConfig를 했으므로 루트 핸들러가 있다 — 그 핸들러들에 기관코드(OC)
# 가림 필터를 단다 (트레이스백까지 가린다. law_go_kr._get도 예외 문자열을 가려서 올린다)
install_oc_log_mask()

# status 계약 (server.py와 동일한 규칙):
#   OK          정상 — 결과 있음
#   NOT_FOUND   조회는 성공했으나 해당 자료가 원천에 없음 (부존재로 판단 가능)
#   AUTH_ERROR  law.go.kr 인증 실패 (OC 미설정·IP 미등록) — 부존재와 무관
#   UPSTREAM_ERROR  원천 접속 실패 — 부존재로 단정 금지
#   INVALID_INPUT   입력 형식 오류
_UPSTREAM_GUIDE = (
    "law.go.kr 접속에 실패했습니다. 자료가 '없다'는 뜻이 아니므로 부존재로 단정하지 말고 "
    "잠시 후 재시도하세요."
)
# 법령 조문 도구(law_article_as_of·law_article_diff)의 NOT_FOUND 안내 — 조문 부재는 신설 전·조번호 오류일 수 있다.
# 다른 도구(판례·해석례 등)의 NOT_FOUND 의미('자료 부존재로 판단 가능')는 그대로 둔다
_ARTICLE_NOT_FOUND_GUIDE = (
    "조문이 없다고 단정하지 말고 신설 전·조번호 오류 가능성을 확인 — law_history_search로 최근 개정"
    "(시행일자·공포번호)을 확인하고, 시행 예정 조문이면 as_of_date를 시행일 이후로 지정해 다시 조회")


def _safe(fn, *args, **kwargs):
    # 오류 문자열은 모두 mask_oc를 거친다 — requests 예외 문자열에 요청 URL(OC=…)이 들어 있다
    try:
        out = fn(*args, **kwargs)
    except LawGoKrAuthError as e:
        return {"status": "AUTH_ERROR", "오류": mask_oc(e),
                "_guidance": "인증 문제이므로 자료 부존재로 해석하지 말 것 — 서버 관리자 확인 필요"}
    except LawGoKrNotFound as e:
        return {"status": "NOT_FOUND", "오류": mask_oc(e),
                "_guidance": "조회는 정상 수행됐고 해당 자료가 원천에 없음 — 입력(법령명·번호·형식·기준일)을 확인"}
    except LawGoKrError as e:
        return {"status": "INVALID_INPUT", "오류": mask_oc(e)}
    except LawGoKrUpstreamError as e:
        # 통합본 조회 실패 뒤 대체 원문에도 조문이 없는 경우 등 — 원천 응답 이상이라 부존재로 단정할 수 없다
        logger.warning("law.go.kr 응답 이상: %s", mask_oc(e))
        return {"status": "UPSTREAM_ERROR", "오류": mask_oc(e), "_guidance": _UPSTREAM_GUIDE}
    except requests.exceptions.RequestException as e:
        logger.error("law.go.kr 접속 실패: %s", mask_oc(e))
        return {"status": "UPSTREAM_ERROR", "오류": mask_oc(f"{type(e).__name__}: {e}"),
                "_guidance": _UPSTREAM_GUIDE}
    except Exception as e:  # noqa: BLE001
        logger.exception("law.go.kr 호출 실패")
        return {"status": "UPSTREAM_ERROR", "오류": mask_oc(f"{type(e).__name__}: {e}"),
                "_guidance": _UPSTREAM_GUIDE}
    # 성공 경로 — 응답 형태를 status 계약에 맞춘다
    if isinstance(out, list):
        if not out:
            return {"status": "NOT_FOUND", "결과": [],
                    "_guidance": "검색 결과 0건 (검색 자체는 정상) — 검색어를 바꿔 재시도"}
        return {"status": "OK", "결과": out}
    if isinstance(out, dict):
        if "status" not in out:
            # 일부 조회 함수는 실패를 {"오류": …}로 반환한다 — NOT_FOUND로 분류
            out["status"] = "NOT_FOUND" if "오류" in out else "OK"
        return out
    return out


def _with_article_guide(out):
    """법령 조문 도구의 NOT_FOUND에만 조문 전용 안내를 단다."""
    if isinstance(out, dict) and out.get("status") == "NOT_FOUND":
        out["_guidance"] = _ARTICLE_NOT_FOUND_GUIDE
    return out


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
def court_case_detail(case_serial: str, max_chars: int = 8000, start_char: int = 0) -> dict:
    """법제처 판례 본문 조회 — court_case_search 결과의 '판례일련번호'로
    판시사항·판결요지·참조조문·판례내용 전문을 가져온다.

    판례내용이 max_chars보다 길면 응답의 "잘림" 안내대로 start_char를 넘겨 이어 읽는다.
    검색 결과에 '본문조회' 안내가 붙은 판례(국세청 제공분)는 이 도구로 열리지 않으니
    안내대로 nts_ruling_get_full_text를 쓴다.

    Args:
        case_serial: 판례일련번호 (court_case_search 결과)
        max_chars: 판례내용 최대 길이 (기본 8000)
        start_char: 판례내용 시작 오프셋 — 잘린 뒷부분을 이어 읽을 때
    """
    return _safe(_law.get_case, case_serial, max_chars, start_char)


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
    목록의 공포번호를 law_addenda_search(promul_no=…)에 넣으면 그 개정의
    부칙(시행일·적용례·경과조치) 전문을 볼 수 있다.

    Args:
        law_name: 법령명 (예: "부가가치세법")
        law_id: 법령ID로 본법만 필터 (예: 부가가치세법=001571). 생략하면 이름이 정확히 같은 법령으로 좁힌다(시행령·시행규칙 제외).
                이름이 같은 법령이 둘 이상이면 지정.
        current_only: True면 현행 법령 검색만 (법령ID·MST 확인용)
    """
    if current_only:
        return _safe(_law.search_laws, law_name)
    return _safe(_law.law_history, law_name, law_id)


@mcp.tool()
def law_article_as_of(law_name: str, article_no: str, as_of_date: str = "",
                      law_id: str = "", max_chars: int = 6000) -> dict:
    """법령 조문 원문 — 현행(기준일 생략 시 오늘) 또는 과거·미래 특정 시점

    현행 조문 확인(as_of_date 생략 = 오늘, 한국 시간)과 예규 회신일·판결 선고일 당시의 조문
    확인(as_of_date 지정)에 모두 쓴다. 미래 날짜를 주면 그날 시행될 문안(공포된 개정 반영)이다.
    선택 규칙: 연혁 시행본 중 시행일자가 기준일 이하인 마지막 시행본(적용시행본)을 고르고, 그
    시행일 기준 통합본(법제처가 그 시행일 기준으로 만든 문안)에서 조문을 잘라 반환한다.
    - 적용시행본.시행일자는 '그 시행본'의 시행일이지 이 조문의 개별 시행일이 아니다.
      이 조문 문안이 언제부터 시행됐는지는 law_article_diff의 현행_문안_시작으로 확인.
    - 시행예정_부분: 조문 일부(항·호)가 기준일 현재 아직 시행 전이면 그 시행일·대상
      (사업장 규모 등 조건별 시행이면 '조건' 포함). 인용할 때 그 시행일을 함께 밝힌다.
    - 공포본_참고: 과거 기준일 조회에서 법제처 과거 통합본이 뒤에 공포된 개정을 소급해
      실은 흔적(기준일 뒤 날짜의 개정 주석)이 있거나, 통합본에는 없는데 기준일 당시 공포돼
      있던 조문(시행 전)이면 기준일 이전 마지막 공포본의 같은 조문을 함께 준다 — '주의'를 따를 것.
    - 원문의 〈개정 2024.12.31, 2025.12.23〉 같은 주석은 그 항의 개정 이력이다.
    개정규정의 적용례("시행 이후 양도분부터 적용" 등)는 law_addenda_search(article_no)로 본다.

    NOT_FOUND는 조문이 없다고 단정하지 말고 신설 전·조번호 오류 가능성을 확인한다(_guidance 참고).

    Args:
        law_name: 법령명 (예: "소득세법 시행령") — 약칭 불가, 이름이 정확히 같은 법령이 없으면 INVALID_INPUT
        article_no: 조번호 — "162" 또는 가지조문 "104의3" 형식 ("제104조의3"도 됨, 항·호는 빼고)
        as_of_date: 기준일 YYYYMMDD — 생략하면 오늘(현행). 예규 회신일 "20080715"처럼 과거 시점도 가능
        law_id: 법령ID 필터 — 생략하면 이름이 정확히 같은 법령으로 좁힌다(시행령·시행규칙 제외)
        max_chars: 원문 최대 길이 (기본 6000). 응답에 "잘림" 항목이 있으면
            거기 안내된 길이 이상으로 지정해 다시 호출하면 전문을 받는다.
    """
    return _with_article_guide(_safe(_law.law_article_as_of, law_name, as_of_date or _today_kst(),
                                     article_no, law_id, max_chars))


@mcp.tool()
def law_article_diff(law_name: str, article_no: str, date_from: str,
                     date_to: str = "", law_id: str = "", max_chars: int = 8000,
                     find_change: bool = True) -> dict:
    """조문 개정 diff(신구조문 대비) — 두 시점 사이에 조문이 어떻게 바뀌었는지 비교.

    date_from(A)과 date_to(B, 생략하면 오늘=현행) 각각의 시행본에서 같은 조문을
    가져와 문안을 줄 단위로 대비한다. "이 예규가 나온 뒤에 조문이 바뀌었나?",
    "지금 문구는 언제부터 시행됐나?" 같은 질문에 사용.

    변경이 있으면:
    - diff: unified diff 형식 (-줄 = A에만, +줄 = B에만. 〈개정 …〉 주석 포함)
    - 현행_문안_시작: B 문안이 처음 시행된 시행본 (A~B 사이 시행본을 이진탐색으로 특정).
      그 시행일자가 곧 이 조문 현행 문안의 시행일이다. 특정근거가 '불확실'이면 법제처 과거
      통합본이 뒤 개정을 소급해 실은 경우라 '뒤_개정_후보'(개정문_언급=True 우선)를 확인할 것
    - 개정_부칙: 그 개정 부칙의 시행일 요약 + 이 조문이 언급된 적용례·경과조치 발췌
      ("시행 후 양도분부터 적용" 등 — 언제 거래분부터 새 조문이 적용되는지)
    표 괘선·전각 기호만 다르면 변경여부 False + 서식차이 True. 비교기준.A/B.시행예정_부분은
    각 기준일 현재 아직 시행 전인 부분([시행일] 표시)이다.

    주의: A~B 사이 개정이 여러 번이면 마지막 변경만 특정된다. 응답의 안내에 따라
    date_to를 좁혀 재호출하면 그 이전 변경도 찾을 수 있다. find_change=True는
    시행본 원문을 여러 번 내려받으므로 수 초 걸릴 수 있다 (10분 캐시로 재호출은 빠름).

    Args:
        law_name: 법령명 (예: "소득세법", "법인세법 시행령")
        article_no: 조번호 — "104" 또는 가지조문 "104의3" 형식
        date_from: 비교 기준 A (YYYYMMDD, 예: 예규 회신일)
        date_to: 비교 기준 B (YYYYMMDD, 생략 시 오늘 = 현행)
        law_id: 법령ID 필터 — 생략하면 이름이 정확히 같은 법령으로 좁힌다(시행령·시행규칙 제외)
        max_chars: diff 최대 길이 (기본 8000)
        find_change: False면 변경 시점 특정·부칙 연결을 생략 (빠른 비교만)
    """
    return _with_article_guide(_safe(_law.law_article_diff, law_name, article_no, date_from,
                                     date_to, law_id, max_chars, find_change))


@mcp.tool()
def law_addenda_search(law_name: str = "", mst: str = "", law_id: str = "",
                       as_of_date: str = "", promul_no: str = "",
                       article_no: str = "", recent: int = 10,
                       max_chars: int = 8000) -> dict:
    """법령 부칙(附則) 조회 — 개정규정이 '언제 시행되고 어떤 분부터 적용되는지' 확인.

    개정 세법의 시행일, 적용례("이 법 시행 이후 양도하는 분부터 적용"), 경과조치는
    본문 조문이 아니라 부칙에 있다. 이 도구가 그 부칙을 가져온다. 세 가지 사용법:

    1) 목록: law_name만 주면 부칙 목록을 최신순으로 반환
       — 각 부칙의 공포일자·공포번호·시행일 요약. 조문별 시행일이 다르면 그 내역도 포함
    2) 특정 개정의 부칙 전문: promul_no(공포번호) 지정
       — law_history_search 결과의 공포번호를 넣으면 그 개정의 시행일·적용례·경과조치 전문
    3) 특정 조문의 적용시기: article_no 지정 (예: "96", "104의3")
       — 전체 부칙에서 그 조문이 언급된 적용례·경과조치 항만 최신순으로 발췌

    기준일을 생략하면 오늘(현행) 시행본 기준이다. 부칙은 그 시행본의 시행일 기준 통합본에서
    읽는다 — 그날까지 시행된 개정의 부칙만 들어 있다(부칙범위 참고). 기준일 현재 공포됐지만 아직
    시행 전인 개정은 '시행전_개정'(목록)·'시행전_개정_부칙_언급'(article_no)으로 따로 나온다 —
    시행일과 함께 안내할 것. law_name은 이름이 정확히 같은 현행 법령만 쓴다(약칭·일부 이름이면
    INVALID_INPUT과 검색된 법령 후보).

    Args:
        law_name: 법령명 (예: "소득세법", "법인세법 시행령", "상법") — 권장. 현행·기준일 시행본을 자동 선택
        mst: 법령일련번호 직접 지정 — 그 MST의 연혁 행 중 기준일(as_of_date, 없으면 오늘) 이하
             마지막 시행본의 통합본을 쓴다. 보통은 law_name(+as_of_date)이 더 정확하다
        law_id: 법령ID 필터 (같은 이름의 본법/시행령 혼입 방지)
        as_of_date: YYYYMMDD — 이 날짜 당시 시행본의 부칙을 조회 (생략 시 오늘 시행본 = 현행)
        promul_no: 공포번호 (예: "21221") — 그 개정 부칙의 전문 반환
        article_no: 조번호 (예: "96", "57의2") — 그 조문이 언급된 부칙 항만 발췌
        recent: 목록 모드에서 반환할 부칙 수 (기본 10, 최신순)
        max_chars: 본문 최대 길이 (기본 8000). 응답에 "잘림"이 있으면 늘려서 재조회
    """
    return _safe(_law.law_addenda, mst, law_name, law_id, as_of_date,
                 promul_no, article_no, recent, max_chars)


@mcp.tool()
def law_revision_reason(law_name: str, promul_no: str = "", year: str = "",
                        law_id: str = "", include_amendment_text: bool = False,
                        max_chars: int = 6000) -> dict:
    """법령 제정·개정이유 원문 — 국가법령정보센터 '제정·개정이유'와 같은 자료.

    "이 조문은 왜 신설됐나", "2011년 개정 취지는?" 같은 입법취지 질문에 사용한다.
    각 개정의 ◇개정이유 / ◇주요내용을 법제처 원문 그대로 반환한다(제정부터 수록).

    사용법:
    1) law_name만 주면 개정 목록(공포일자·공포번호·제개정구분)을 최신순으로 반환
    2) promul_no 또는 year를 지정하면 그 개정들의 개정이유 전문
       예: promul_no="제정,10410" / year="2011,2019,2020,2022" (한 번에 최대 10건)

    타법개정(다른 법 개정에 딸린 개정)은 개정이유가 그 다른 법의 것이므로 응답의
    '주의'와 개정문(이 법이 실제로 어떻게 고쳐졌는지)을 함께 보라.

    Args:
        law_name: 법령명 (예: "국제조세조정에 관한 법률", "소득세법 시행령")
        promul_no: 공포번호, 쉼표로 여러 개 — "제정"이라고 쓰면 제정 법령
        year: 공포연도(YYYY), 쉼표로 여러 개 — 그해 공포된 개정 전부
        law_id: 법령ID (생략 시 법령명 정확 일치로 자동 결정)
        include_amendment_text: True면 개정문(어느 조문을 어떻게 고쳤는지)도 함께
        max_chars: 개정 1건당 본문 최대 길이 (기본 6000)
    """
    return _safe(_law.law_revision_reason, law_name, law_id, promul_no, year,
                 include_amendment_text, max_chars)


@mcp.tool()
def admin_rule_search(keyword: str, serial: str = "", display: int = 10,
                      article: str = "", max_chars: int = 10000,
                      start_char: int = 0) -> object:
    """행정규칙(훈령·예규·고시) 검색/조회 — 기본통칙·조사사무처리규정·국세청 고시·외국환거래규정.

    serial 없이 호출하면 키워드 검색(규칙명·종류·소관부처·발령일자 목록),
    serial(일련번호)을 주면 본문을 반환한다.
    예: "법인세법 기본통칙", "조사사무처리규정", "외국환거래규정"

    외국환거래규정(30만 자) 같은 대형 고시는 전문이 max_chars에 잘리므로
    article에 조번호를 지정해 해당 조문만 받는 것을 권장한다
    (예: 해외직접투자 신고 = article="9-5"). 응답의 "잘림" 안내를 따르면 된다.

    Args:
        keyword: 검색어 (serial 없이 호출 시)
        serial: 행정규칙 일련번호 — 지정하면 본문 조회
        display: 검색 결과 수 (기본 10)
        article: 조번호 — "9-5"(제9-5조), "23", "23의2" 형식. 해당 조문만 반환
        max_chars: 본문 최대 길이 (기본 10000)
        start_char: 본문 시작 오프셋 — 조번호를 모를 때 이어 읽기용
    """
    if serial:
        return _safe(_law.get_admin_rule, serial, max_chars, article, start_char)
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
    import uvicorn

    from auth_gate import TokenGate, describe

    logger.info("nts-tax-mcp 확장판 기동 — 기존 8개 + 법제처 11개 도구")

    # mcp.run() 대신 앱을 직접 만들어 접근 토큰 게이트를 한 겹 두른다.
    # (mcp.run(transport="streamable-http")이 내부적으로 하던 것과 동일한 구성)
    app = TokenGate(
        mcp.streamable_http_app(),
        mcp_path=mcp.settings.streamable_http_path,
    )
    logger.info("접근 제어: %s", describe(app))
    uvicorn.run(
        app,
        host=mcp.settings.host,
        port=mcp.settings.port,
        log_level=mcp.settings.log_level.lower(),
    )
