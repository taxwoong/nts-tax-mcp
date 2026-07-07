"""
nts_tax_ruling_search.py
국세법령정보시스템(taxlaw.nts.go.kr) 통합검색 API 클라이언트

한 번의 호출로 다음을 동시에 검색합니다:
- 사전답변 / 서면질의 / 질의회신 (국세청·기획재정부·법제처)
- 조세심판원 심판결정례 + 법원 판례
- 법령, 별표서식, 구 법령해석자료, 홈택스 상담사례

브라우저 없이 requests만으로 동작 (세션 쿠키 1회 확보 후 재사용).
"""

import json
from typing import Optional

import requests

BASE_URL = "https://taxlaw.nts.go.kr"
SEARCH_ENTRY_URL = f"{BASE_URL}/qt/USEQTA001M.do?ntstDcmClCd=01"
ACTION_URL = f"{BASE_URL}/action.do"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# collection 파라미터 값 (콤마로 이어붙여서 전달)
COLLECTIONS = {
    "form": "appendForm",           # 별표서식
    "statute": "statute",           # 법령
    "ruling": "question",           # 사전답변·서면질의·질의회신 (국세청/기재부/법제처)
    "precedent": "precedent",       # 조세심판원 심판결정례 + 법원 판례
    "old_ruling": "formerLibrary",  # 구 법령해석 자료
    "intl": "intEpn",               # 국제조세 해설
    "hometax": "hometaxCnslThan",   # 홈택스 상담사례
}
ALL_COLLECTIONS = list(COLLECTIONS.values())

# 문서 출처기관 코드 (검색결과 필드 NTST_DCM_SRCS_ORGN_CL_CD 및
# 화면상 발신기관 필터 버튼 qstnPrdcOrgnClCtl_xx 에서 확인)
ORG_CODES = {
    "01": "국세청",
    "02": "기획재정부",
    "03": "법제처",
    "04": "조세심판원",  # precedent 컬렉션 내 NTST_DCM_CL_NM == '심판' 문서에서 확인
}


class NtsTaxLawClient:
    """국세법령정보시스템 통합검색 클라이언트 (세션 재사용)"""

    def __init__(self, verify_ssl: bool = True, timeout: int = 20):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._bootstrapped = False

    def _bootstrap(self):
        """검색 화면에 최초 1회 접속해 JSESSIONID 쿠키를 확보합니다."""
        resp = self.session.get(SEARCH_ENTRY_URL, verify=self.verify_ssl, timeout=self.timeout)
        resp.raise_for_status()
        self._bootstrapped = True

    def search(
        self,
        keyword: str,
        collections: Optional[list] = None,
        start_count: int = 1,
        view_count: int = 20,
        date_from: Optional[str] = None,   # YYYYMMDD
        date_to: Optional[str] = None,     # YYYYMMDD
        date_base: str = "DCM_RGT_DTM",    # 문서일자 기준(기본). 등록일 기준은 FRS_RGT_DTM
        sort: str = "SCORE/DESC",
    ) -> dict:
        """
        키워드로 통합검색을 수행하고, 컬렉션별로 정리된 결과를 반환합니다.

        collections: COLLECTIONS 값 리스트 중 원하는 것만 선택 (기본값: 전체)
                      예) ["question", "precedent"] -> 질의회신 + 심판/판례만
        """
        if not self._bootstrapped:
            self._bootstrap()

        if collections is None:
            collections = ALL_COLLECTIONS

        param_data = {
            "schVcb": keyword,
            "startCount": start_count,
            "collection": ",".join(collections),
            "wnKey": "",
            "searchType": "",
            "sortField": sort,
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
        return self._parse(resp.json())

    def _parse(self, raw: dict) -> dict:
        try:
            srv = raw["data"]["ASEISA001MR01"]["searchResultVO"]
        except (KeyError, TypeError):
            return {"error": "예상치 못한 응답 구조", "raw_status": raw.get("status")}

        result = {}
        for coll in srv.get("collectionList", []):
            name_en = coll.get("nameEn")
            result[name_en] = {
                "name_kr": coll.get("nameKr"),
                "total_count": coll.get("totalCount", 0),
                "items": [self._clean_item(it) for it in coll.get("resultList", [])],
            }
        return result

    @staticmethod
    def _clean_item(it: dict) -> dict:
        def strip_hl(text):
            """검색어 하이라이트 마커(<!HS>, <!HE>) 제거"""
            if not text:
                return text
            return text.replace("<!HS>", "").replace("<!HE>", "")

        org_code = it.get("NTST_DCM_SRCS_ORGN_CL_CD")
        return {
            "title": strip_hl(it.get("TTL")),
            "doc_type": it.get("NTST_DCM_CL_NM"),              # 예: 질의, 심판, 판결
            "doc_no": it.get("NTST_DCM_DSCM_CNTN"),            # 예: "기획재정부 재산세제과-73", "조심-2023-서-9465"
            "source_org": ORG_CODES.get(org_code, org_code),   # 국세청/기획재정부/법제처/조세심판원
            "date": it.get("DCM_RGT_DTM_S") or it.get("DATE"),
            "tax_type": it.get("NTST_TLAW_CL_NM"),             # 세목명 (양도소득세 등)
            "summary": strip_hl(it.get("GIST_CNTN")),          # 요지
            "content": strip_hl(it.get("CNTN")),               # 질의/회신 전문(있는 경우)
            "detail_content": strip_hl(it.get("FILE_CN")),     # 사실관계 등 상세(있는 경우)
            "doc_id": it.get("DOC_ID"),
            "related_doc_ids": it.get("RFRN_QUT_NTST_DCM_ID"),
        }


if __name__ == "__main__":
    # 간단한 동작 확인용
    client = NtsTaxLawClient(verify_ssl=False)
    result = client.search("조정대상지역", collections=["question", "precedent"], view_count=5)
    for coll_name, coll_data in result.items():
        print(f"\n=== {coll_data['name_kr']} (총 {coll_data['total_count']}건) ===")
        for item in coll_data["items"]:
            print(f"- [{item['doc_type']}][{item['source_org']}] {item['title']} ({item['doc_no']}, {item['date']})")
