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

import json
import logging
import os
import re
from typing import Optional

import requests

from mcp.server.fastmcp import FastMCP

from nts_tax_ruling_search import NtsTaxLawClient, NtsParseError, COLLECTIONS
from olta_tax_ruling_search import (
    OltaTaxLawClient, OltaParseError, ALL_CATEGORY_KEYS, normalize_doc_no,
)

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("nts-tax-mcp")

PORT = int(os.environ.get("PORT", 8000))

mcp = FastMCP(
    "nts-tax-ruling",
    instructions=(
        "대한민국 국세법령정보시스템(taxlaw.nts.go.kr) + 지방세법령정보시스템(olta.re.kr) "
        "통합검색 도구입니다. 국세(사전답변·질의회신·조세심판원·법원판례)는 nts_ruling_search, "
        "지방세(취득세·재산세 등 조세심판원·감사원·헌재·자치단체 질의회신)는 olta_ruling_search를 "
        "사용하세요. 국세/지방세를 모두 아우르는 질문이면 nts_and_olta_precedent_search로 "
        "한 번에 검색하고 중복 없이 결과를 받을 수 있습니다. 답변 초안에 인용한 문서번호는 "
        "verify_citations로 실존 여부를 일괄 검증할 수 있습니다.\n"
        "**검색 결과의 본문은 전문이 아니라 검색어 주변 스니펫입니다** — 심판·심사는 "
        "'결정내용은 붙임과 같습니다.'만 나옵니다. 결정 이유·처분개요·청구주장·사실관계가 "
        "필요하면 검색으로 문서번호를 찾은 뒤 nts_ruling_get_full_text로 전문을 읽으세요. "
        "사실관계 비교나 쟁점 분석에는 요지만으로 결론을 내지 말고 반드시 전문을 확인해야 합니다.\n"
        "모든 도구 응답의 status 필드 해석: OK(정상) · NOT_FOUND(검색은 성공했고 결과 없음 — "
        "자료 부존재로 판단해도 됨) · UPSTREAM_ERROR/PARSE_ERROR(원천 사이트 접근·해석 실패 — "
        "자료가 없다고 절대 단정하지 말 것) · AUTH_ERROR(law.go.kr 인증 실패 — 부존재와 무관, "
        "서버 관리자 확인 필요) · INVALID_INPUT(입력 형식 오류)."
    ),
    host="0.0.0.0",
    port=PORT,
    stateless_http=False,
)

# ---------------------------------------------------------------------------
# 오류 응답 계약: 원천 접근 실패와 '결과 0건'을 구분해서 반환한다.
# 실패를 0건처럼 보이게 두면 LLM이 "해당 자료 없음"으로 단정하는 사고가 난다.
# ---------------------------------------------------------------------------
_UPSTREAM_GUIDE = (
    "원천 사이트 접속에 실패했습니다. 검색 결과가 '없다'는 뜻이 아니므로 자료 부존재로 "
    "단정하지 마세요. 잠시 후 재시도하거나, 사용자에게 일시 장애 가능성을 알리세요."
)
_PARSE_GUIDE = (
    "응답은 받았으나 예상한 구조가 아닙니다(사이트 개편 가능성). 자료 부존재로 단정하지 "
    "마세요. 이 오류가 반복되면 서버 관리자에게 파서 업데이트가 필요함을 알려야 합니다."
)


def _tool_guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (NtsParseError, OltaParseError) as e:
        logger.error("파싱 실패: %s", e)
        return {"status": "PARSE_ERROR", "오류": str(e), "_guidance": _PARSE_GUIDE}
    except requests.exceptions.RequestException as e:
        logger.error("원천 접속 실패: %s", e)
        return {"status": "UPSTREAM_ERROR", "오류": f"{type(e).__name__}: {e}",
                "_guidance": _UPSTREAM_GUIDE}
    except ValueError as e:
        return {"status": "INVALID_INPUT", "오류": str(e)}
    except Exception as e:  # noqa: BLE001 — status 없는 응답이 나가지 않도록 최종 방어
        logger.exception("도구 호출 실패")
        return {"status": "UPSTREAM_ERROR", "오류": f"{type(e).__name__}: {e}",
                "_guidance": _UPSTREAM_GUIDE}

# ---------------------------------------------------------------------------
# 응답 총량 상한
#
# 검색 응답은 대화 컨텍스트로 그대로 들어간다. 기본값(view_count=5, 본문 off)이면
# 1만 자 안쪽이지만, view_count를 크게 올리면 컬렉션 수만큼 곱해져 수만 자가 된다.
# 원천이 주는 순서대로 다 넘기는 대신 상한을 두고, 넘치면 뒤에서부터 덜어낸다.
# ---------------------------------------------------------------------------
_RESPONSE_CHAR_CAP = int(os.environ.get("NTS_RESPONSE_CHAR_CAP", "30000"))


def _response_size(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False, default=str))


def _cap_response(result, cap: int = _RESPONSE_CHAR_CAP):
    """컬렉션별 items를 한 건씩 덜어내 응답 총량을 cap 이하로 맞춘다."""
    if not isinstance(result, dict) or _response_size(result) <= cap:
        return result

    # 컬렉션은 최상위(nts_ruling_search)에도 있고 한 단계 안쪽에도 있다
    # (nts_and_olta_precedent_search의 olta_precedent 아래 카테고리별 묶음)
    def _collect(node, depth=0):
        found = []
        if not isinstance(node, dict) or depth > 2:
            return found
        for v in node.values():
            if not isinstance(v, dict):
                continue
            if isinstance(v.get("items"), list):
                found.append(v)
            else:
                found.extend(_collect(v, depth + 1))
        return found

    colls = _collect(result)
    if not colls:
        return result

    def _notice(n: int) -> str:
        return (f"응답이 상한({cap:,}자)을 넘어 뒤쪽 {n}건을 생략했습니다. "
                f"컬렉션별 총 건수(total_count)는 생략 전 기준 그대로입니다. "
                f"더 보려면 page를 올리거나, collections로 범위를 좁혀 다시 조회하세요.")

    # 안내 문구도 응답에 실리므로 그만큼 미리 빼둔다 — 안 그러면 상한을 넘겨 끝난다
    budget = cap - len(_notice(9999)) - len('"_잘림": ,')

    dropped = 0
    # 항목이 가장 많은 컬렉션부터 떼어낸다 — 한 컬렉션만 통째로 날아가지 않도록.
    # 컬렉션마다 최소 1건은 남긴다(무엇이 걸렸는지는 보여야 한다).
    while _response_size(result) > budget:
        target = max(colls, key=lambda c: len(c["items"]))
        if len(target["items"]) <= 1:
            break
        target["items"].pop()
        dropped += 1

    if dropped:
        result["_잘림"] = _notice(dropped)
    return result


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
    view_count: int = 5,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort: str = "relevance",
    tax_type_filter: Optional[str] = None,
    include_full_text: bool = False,
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
        view_count: 컬렉션별로 가져올 결과 개수 (기본 5). 넓게 훑어야 할 때만 늘리세요 —
            컬렉션 7개에 곱해지므로 20으로 올리면 최대 140건이 한 번에 돌아옵니다
        date_from: 검색 시작일 YYYYMMDD (선택)
        date_to: 검색 종료일 YYYYMMDD (선택)
        sort: "relevance"(정확도순, 기본) | "date_desc"(최신순) | "date_asc"(오래된순)
        tax_type_filter: 특정 세목만 보고 싶을 때 (예: "양도소득세", "부가가치세").
            결과의 세목명에 이 문자열이 포함된 것만 남깁니다.
        include_full_text: True로 주면 content/detail_content를 함께 반환합니다(기본 False).
            **이건 전문이 아니라 검색어 주변 500~900자 스니펫입니다** — 심판·심사의
            content는 "결정내용은 붙임과 같습니다." 상수라 아무 정보가 없습니다.
            켜면 응답이 두 배가 되지만 얻는 건 발췌뿐이므로 거의 켤 일이 없습니다.
            전문이 필요하면 nts_ruling_get_full_text를 쓰세요.

    Returns:
        컬렉션별 총 건수와 결과 목록(제목, 문서번호, 출처기관, 날짜, 세목, 요지, 붙임 파일 ID).
        검색 결과가 전혀 없으면 "_guidance" 키에 안내 메시지가 포함됩니다.

    권장 흐름: 이 도구로 요지를 훑어 후보를 좁힌 뒤,
    필요한 문서만 nts_ruling_get_full_text(문서번호)로 전문을 읽습니다.
    """
    collection_codes = None
    if collections:
        # 무효 이름을 조용히 버리면 전부 무효일 때 빈 리스트가 넘어가 collection=""로
        # 검색되거나(nts), 필터가 통째로 해제된다(olta) — 명시적 오류로 되돌린다
        unknown = [c for c in collections if c not in COLLECTIONS]
        if unknown:
            return {"status": "INVALID_INPUT",
                    "오류": f"지원하지 않는 collections 값: {unknown} "
                            f"(가능: {list(COLLECTIONS.keys())})"}
        collection_codes = [COLLECTIONS[c] for c in collections]

    return _cap_response(_tool_guard(
        _client.search,
        keyword=keyword,
        collections=collection_codes,
        page=page,
        view_count=view_count,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        tax_type_filter=tax_type_filter,
        include_full_text=include_full_text,
    ))


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
        items: 일치하는 문서 목록 (요지 + 검색 스니펫)
        message: 못 찾은 경우 안내 메시지

    주의: 여기서 나오는 content/detail_content는 전문이 아니라 검색 스니펫입니다
    (심판·심사의 content는 "결정내용은 붙임과 같습니다." 상수).
    결정 이유·처분개요·청구주장·사실관계가 필요하면
    nts_ruling_get_full_text를 사용하세요.
    """
    return _tool_guard(_client.get_by_doc_no, doc_no)


@mcp.tool()
def nts_ruling_get_full_text(doc_no: str, max_chars: int = 30000,
                             start_char: int = 0) -> dict:
    """
    문서번호로 결정문·판결문 '전문'을 가져옵니다 (붙임 HWP 본문).

    검색 도구(nts_ruling_search)가 주는 본문은 검색어 주변 500~900자 스니펫이고,
    심판·심사는 아예 "결정내용은 붙임과 같습니다."만 나옵니다. 실제 결정 이유는
    붙임 파일에 있으며, 이 도구가 그 붙임을 받아 텍스트로 변환해 돌려줍니다.

    이런 걸 읽을 수 있습니다:
        심판·심사 → 주문 / 처분개요 / 청구주장 / 처분청 의견 / 심리 및 판단(쟁점·관련법령·판단)
        판례       → 사건·당사자 / 청구취지 / 이유 / 판단
        사전답변·질의회신 → 1. 사실관계 / 2. 질의내용 / 3. 회신

    사실관계나 판단 논리가 중요한 질문(유사 사례 비교, 쟁점 분석)에는 반드시 이
    도구로 전문을 확인하세요. 검색 결과의 요지만으로 결론을 단정하면 안 됩니다.

    Args:
        doc_no: 사건번호/문서번호. 예: "조심-2026-서-1112", "사전-2026-법규법인-0502"
        max_chars: 본문 최대 길이 (기본 30000). 전문은 보통 2천~2만 자입니다
        start_char: 본문 시작 오프셋 — 잘린 뒷부분을 이어 읽을 때
            응답의 "잘림" 안내가 다음 start_char 값을 알려줍니다

    Returns:
        문서번호·제목·종류·일자·세목·요지 + 전문(본문 텍스트) + 전문자수.
        붙임이 없는 문서는 status=NOT_FOUND와 함께 요지만 반환합니다.
        세액 비교표 같은 표(2행·2열 이상)는 Markdown 표로 복원되어 열 제목과 숫자가
        짝지어 나옵니다. 붙임이 글자 없는 빈 파일이면 검색 결과 본문을 대신 주고
        '비고'로 알립니다.
    """
    return _tool_guard(_client.get_full_text, doc_no, max_chars, start_char)


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
        unknown = [c for c in categories if c not in ALL_CATEGORY_KEYS]
        if unknown:
            return {"status": "INVALID_INPUT",
                    "오류": f"지원하지 않는 categories 값: {unknown} "
                            f"(가능: {ALL_CATEGORY_KEYS})"}
        category_codes = list(categories)

    return _tool_guard(
        _olta_client.search,
        keyword=keyword,
        categories=category_codes,
        view_count=view_count,
        tax_type_filter=tax_type_filter,
    )


@mcp.tool()
def nts_and_olta_precedent_search(
    keyword: str,
    view_count: int = 5,
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
        view_count: 각 소스에서 가져올 결과 개수 (기본 5). 국세 1개 + 지방세 4개
            카테고리에 각각 곱해집니다. 국세측 본문 스니펫은 항상 생략되며,
            전문이 필요하면 nts_ruling_get_full_text로 따로 조회하세요
        tax_type_filter: 세목 필터 (예: "양도소득세" 또는 "취득세")

    Returns:
        nts_precedent: 국세법령정보시스템의 심판·심사·판례 결과
        olta_precedent: 지방세법령정보시스템의 조세심판원·감사원·헌재·법원 결과
            (nts_precedent와 문서번호가 겹치는 항목은 제외됨)
        duplicates_removed: 국세 결과와 겹쳐 제외된 지방세 항목 수
            (view_count로 자르기 전 기준 — 겹친 건수 전부를 셉니다)
    """
    def _impl():
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
        # 제외 건수는 olta 클라이언트가 세어 돌려준다 — 예전에는 이 숫자 하나 때문에
        # 같은 검색을 한 번 더 돌렸다(대개 캐시에 맞았지만 TTL이 만료됐거나 빈 결과가
        # 캐시되지 않은 경우엔 실제로 두 번 요청했다)
        duplicates_removed = olta_result.pop("excluded_count", 0)

        nts_status = nts_result.get("status", "OK")
        olta_status = olta_result.get("status", "OK")
        return {
            "status": "OK" if "OK" in (nts_status, olta_status) else "NOT_FOUND",
            "nts_precedent": nts_result.get("precedent"),
            "olta_precedent": {k: v for k, v in olta_result.items()},
            "duplicates_removed": duplicates_removed,
        }

    return _cap_response(_tool_guard(_impl))


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
    return _tool_guard(
        _olta_client.search_collection,
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
    return _tool_guard(_olta_client.get_detail, category=category, doc_id=doc_id)


# ---------------------------------------------------------------------------
# 인용 검증 — 문서번호 실존 확인 (인용 환각 방지)
# ---------------------------------------------------------------------------

# 지방세 계열임이 표기상 분명한 문서번호 — 이 경우 지방세 시스템을 먼저 확인한다.
# 조심YYYY지NNNN / 감심YYYY-NNN(감심 제YYYY-N호 표기 포함) / 헌재 사건번호
# (96헌바14처럼 2자리 연도 표기도 있어 \d{2,4}로 받는다)
_LOCAL_TAX_DOC_RE = re.compile(r"^(조심\d{4}지|감심제?\d{2,4}|\d{2,4}헌[가-힣])")

_VERIFY_MAX = 10  # 호출당 검증 상한 (문서번호 1건당 원천 검색 1~2회가 나가므로 제한)


def _collect_nts_candidates(doc_no: str) -> list:
    """문서번호를 검색어로 국세 시스템을 조회해 (doc_no, 메타) 후보 목록을 반환."""
    res = _client.search(
        keyword=doc_no,
        collections=["question", "precedent"],
        view_count=30,
        include_full_text=False,
    )
    out = []
    for coll_data in res.values():
        if not isinstance(coll_data, dict):
            continue
        for it in coll_data.get("items", []):
            if it.get("doc_no"):
                out.append((it["doc_no"], {
                    "제목": it.get("title"),
                    "문서번호": it.get("doc_no"),
                    "문서유형": it.get("doc_type"),
                    "출처기관": it.get("source_org"),
                    "날짜": it.get("date"),
                    "세목": it.get("tax_type"),
                }))
    return out


def _collect_olta_candidates(doc_no: str) -> list:
    res = _olta_client.search(keyword=doc_no, view_count=20)
    out = []
    for key, data in res.items():
        if not isinstance(data, dict):
            continue
        for it in data.get("items", []):
            if it.get("doc_no"):
                out.append((it["doc_no"], {
                    "제목": it.get("title"),
                    "문서번호": it.get("doc_no"),
                    "카테고리": data.get("name_kr"),
                    "날짜": it.get("date"),
                    "세목": it.get("tax_type"),
                }))
    return out


def _partial_match_ok(norm: str, cand: str) -> bool:
    """축약 표기 부분일치 허용 여부. 인용 검증 도구이므로 오탐(없는 인용을 '확인')이
    미탐보다 훨씬 위험하다.

    실무의 문서번호 축약은 앞쪽 기관명을 생략하는 형태이므로("기획재정부 재산세제과-73"
    → "재산세제과-73") 후보의 **접미 일치**만 허용한다. 이 한 가지 규칙으로 두 오탐이
    동시에 막힌다:
      ① "조심2023"·"서면2019"처럼 연도까지만 있는 접두부 입력 — 문서를 특정하지 못하고
         그 해 아무 사건에나 걸린다 (접미가 아니므로 거부).
      ② "재산세제과73"이 "…재산세제과732"에 걸리는 숫자 경계 문제 (접미가 아니므로 거부).
    일련번호가 없는 입력도 배제하기 위해 숫자 1개 이상·6자 이상을 요구한다."""
    if len(norm) < 6 or not any(ch.isdigit() for ch in norm):
        return False
    return cand != norm and cand.endswith(norm)


def _match_citation(norm: str, candidates: list):
    """정규화 일치 우선, 다음으로 축약 표기 포함 일치 (오매치 방지 조건부).
    반환: (일치항목 메타 or None, 일치방식)"""
    for cand_no, meta in candidates:
        if normalize_doc_no(cand_no) == norm:
            return meta, "정확"
    for cand_no, meta in candidates:
        if _partial_match_ok(norm, normalize_doc_no(cand_no)):
            return meta, "부분 (입력이 축약 표기 — 전체 문서번호는 '문서번호' 필드 참고)"
    return None, ""


def _verify_one_citation(raw: str, search_local_tax: bool) -> dict:
    entry = {"입력": raw}
    norm = normalize_doc_no(raw)
    if not norm:
        entry.update({"판정": "미확인", "비고": "빈 문서번호"})
        return entry

    candidates = []
    sources = []
    local_first = bool(_LOCAL_TAX_DOC_RE.match(norm))
    try:
        if not local_first:
            nts_cands = _collect_nts_candidates(raw)
            matched, how = _match_citation(norm, nts_cands)
            if matched:
                entry.update({"판정": "확인", "출처": "국세법령정보시스템",
                              "일치방식": how, "문서": matched})
                return entry
            candidates += [c for c, _ in nts_cands]
        if search_local_tax or local_first:
            olta_cands = _collect_olta_candidates(raw)
            matched, how = _match_citation(norm, olta_cands)
            if matched:
                entry.update({"판정": "확인", "출처": "지방세법령정보시스템",
                              "일치방식": how, "문서": matched})
                return entry
            candidates += [c for c, _ in olta_cands]
        if local_first:  # 지방세 표기였지만 못 찾음 — 국세 쪽도 마저 확인
            nts_cands = _collect_nts_candidates(raw)
            matched, how = _match_citation(norm, nts_cands)
            if matched:
                entry.update({"판정": "확인", "출처": "국세법령정보시스템",
                              "일치방식": how, "문서": matched})
                return entry
            candidates += [c for c, _ in nts_cands]
    except (NtsParseError, OltaParseError, requests.exceptions.RequestException) as e:
        entry.update({"판정": "판단불가", "오류": f"{type(e).__name__}: {e}",
                      "비고": "원천 접근 실패 — 존재하지 않는다는 뜻이 아님"})
        return entry

    entry["판정"] = "미확인"
    if candidates:
        seen, similar = set(), []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                similar.append(c)
        entry["유사문서_후보"] = similar[:5]
    return entry


@mcp.tool()
def verify_citations(doc_nos: list, search_local_tax: bool = True) -> dict:
    """인용된 국세·지방세 문서번호(사건번호)들이 실제 존재하는지 일괄 검증합니다.

    답변·의견서 초안에 인용한 예규·심판례·판례 번호가 실재하는지 확인하는
    인용 환각 방지 도구입니다. 각 번호를 원천 시스템(국세법령정보시스템,
    지방세법령정보시스템)에서 검색해 정규화 비교(띄어쓰기·하이픈 차이 흡수)로
    실존 여부를 판정합니다.

    Args:
        doc_nos: 검증할 문서번호 목록 (호출당 최대 10건). 예:
            ["조심-2023-서-9465", "서면-2019-법규재산-4276", "조심2026지0284"]
        search_local_tax: False면 지방세 시스템 검색을 생략 (국세 문서만 검증할 때
            왕복 절약). 지방세 표기(조심YYYY지…, 감심…, 헌재 사건번호)는 이 값과
            무관하게 지방세 시스템을 확인합니다.

    Returns:
        요약: {확인, 미확인, 판단불가} 건수
        결과: 문서번호별 판정 —
            "확인"(실존, 문서 메타 포함) / "미확인"(원천에서 못 찾음, 유사문서_후보 포함 가능)
            / "판단불가"(원천 접근 실패 — 부존재로 단정 금지)
        미확인이라도 표기 변형·미색인 자료(감사원 국세 결정례, 아주 오래된 예규 등)일 수
        있으니 유사문서_후보와 키워드 재검색으로 확인하세요. 법령 조문 인용의 검증은
        law_article_as_of를 사용하세요.
    """
    if not isinstance(doc_nos, list) or not doc_nos:
        return {"status": "INVALID_INPUT",
                "오류": "doc_nos는 문서번호 문자열의 목록이어야 합니다 (1~10건)"}

    head = doc_nos[:_VERIFY_MAX]
    targets = [d.strip() for d in head if isinstance(d, str) and d.strip()]
    invalid = [d for d in head if not isinstance(d, str) or not d.strip()]
    if not targets:
        return {"status": "INVALID_INPUT",
                "오류": ("검증할 문서번호가 없습니다 — doc_nos의 모든 원소가 빈 문자열이거나 "
                        "문자열이 아닙니다"),
                "무시된_입력": [repr(d)[:40] for d in invalid]}
    skipped = len(doc_nos) - len(head)

    results = []
    consecutive_failures = 0
    for doc_no in targets:
        if consecutive_failures >= 2:
            # 원천 장애가 이어지면 남은 건은 호출 없이 판단불가 처리 (원천 부하 방지)
            results.append({"입력": doc_no, "판정": "판단불가",
                            "비고": "앞선 검증이 연속 실패해 원천 장애로 보고 중단함"})
            continue
        entry = _verify_one_citation(doc_no, search_local_tax)
        if entry.get("판정") == "판단불가":
            consecutive_failures += 1
        else:
            consecutive_failures = 0
        results.append(entry)

    counts = {"확인": 0, "미확인": 0, "판단불가": 0}
    for r in results:
        counts[r["판정"]] = counts.get(r["판정"], 0) + 1

    out = {
        "status": ("UPSTREAM_ERROR"
                   if results and counts["판단불가"] == len(results) else "OK"),
        "요약": counts,
        "결과": results,
    }
    if invalid:
        out["무시된_입력"] = [repr(d)[:40] for d in invalid]
    if skipped > 0:
        out["안내"] = f"호출당 최대 {_VERIFY_MAX}건 — 뒤의 {skipped}건은 검증되지 않음 (나눠서 재호출)"
    if counts["미확인"]:
        out["_guidance"] = (
            "'미확인'은 '존재하지 않음'과 동의어가 아닙니다 — 표기 변형이나 원천 미색인"
            "(감사원 국세 심사결정, 오래된 자료 등)일 수 있습니다. 유사문서_후보를 확인하거나 "
            "핵심 키워드로 재검색해 보세요. '판단불가'는 원천 장애이므로 부존재 판단에 쓰지 마세요."
        )
    return out


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
