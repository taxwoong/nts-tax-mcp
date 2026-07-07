# 데이터 소스 사양서 — 호출 가능 목록 정리

`nts-tax-mcp` 서버가 다루는 두 데이터 소스의 실제 호출 가능 범위, 컬렉션/카테고리 목록,
반환 필드, 코드표를 실측 기반으로 정리한 문서입니다. (2026-07 조사 기준)

---

## 1. 국세법령정보시스템 (taxlaw.nts.go.kr)

### 호출 방식
- 엔드포인트: `POST https://taxlaw.nts.go.kr/action.do`
- 요청: `actionId=ASEISA001MR01` + `paramData`(JSON)
- 응답: **JSON** (구조화된 데이터, 파싱 안정성 높음)
- 인증: JSESSIONID 쿠키 (검색화면 GET 1회로 확보)

### 호출 가능한 컬렉션 (7개)

| 컬렉션 코드 | 한글명 | 내용 | MCP 도구에서의 키 |
|---|---|---|---|
| `question` | 질의 | **사전답변 + 서면질의 + 질의회신** (국세청/기재부) | `ruling` |
| `precedent` | 판례 | **조세심판원 심판(국세) + 국세청 심사·이의·적부 + 법원판례(1심~대법원)** | `precedent` |
| `statute` | 법령 | 세법 조문 | `statute` |
| `appendForm` | 별표서식 | 별표·서식 | `form` |
| `formerLibrary` | 전자도서관 | 구 법령해석 자료 | `old_ruling` |
| `intEpn` | 국제해설 | 국제조세 해설 (검색시 대부분 0건) | `intl` |
| `hometaxCnslThan` | 홈택스 상담사례 | 홈택스 Q&A | `hometax` |

### question 컬렉션 내 문서유형 (NTST_DCM_CL_NM)

| 문서유형 | 의미 | 샘플 문서번호 |
|---|---|---|
| 질의 | 서면질의·질의회신 | 서면-2019-법규재산-4276, 기획재정부 재산세제과-73 |
| 사전 | 사전답변 | 사전-2026-법규국조-0669 |

### precedent 컬렉션 내 문서유형 (NTST_DCM_CL_NM)

| 문서유형 | 의미 | 샘플 문서번호 |
|---|---|---|
| 심판 | 조세심판원 심판결정례 (국세) | 조심-2025-인-2268 |
| 심사 | 국세청 심사청구 결정례 | 심사-양도-2021-0067 |
| 이의 | 이의신청 결정례 | 이의-부산청-2024-0105 |
| 적부 | 과세전적부심사 결정례 | 적부-국세청-2024-0265 |
| 판례 | 법원 판결 | 아래 코드표 참조 |

### 출처기관 코드 (NTST_DCM_SRCS_ORGN_CL_CD)

| 코드 | 기관 |
|---|---|
| 01 | 국세청 |
| 02 | 기획재정부 |
| 03 | 법제처 |
| 04 | 조세심판원 |
| 51 | 지방법원 (1심) |
| 52 | 행정법원 |
| 53 | 고등법원 |
| 54 | 대법원 |

### 항목별 주요 반환 필드

| 서버 원본 필드 | MCP 반환 키 | 설명 |
|---|---|---|
| TTL | title | 제목 |
| NTST_DCM_CL_NM | doc_type | 문서유형 (질의/사전/심판/심사/이의/적부/판례) |
| NTST_DCM_DSCM_CNTN | doc_no | 문서번호/사건번호 |
| NTST_DCM_SRCS_ORGN_CL_CD | source_org | 출처기관 (코드→한글 변환) |
| DCM_RGT_DTM_S / DATE | date | 문서일자 (YYYYMMDD) |
| NTST_TLAW_CL_NM | tax_type | 세목명 |
| GIST_CNTN | summary | 요지 |
| CNTN | content | 질의/회신 본문 전문 |
| FILE_CN | detail_content | 사실관계 등 상세 |
| DOC_ID | doc_id | 내부 문서 ID |

### 지원되는 검색 옵션 (서버측)
- 정렬: `SCORE/DESC`(정확도), `DCM_RGT_DTM/DESC·ASC`(문서일자)
- 페이지네이션: `startCount`, `viewCount`
- **주의**: 기간 필터를 서버에 직접 전달하는 파라미터는 확인되지 않음 (통합검색 화면에 기간 UI 자체가 없음).
  MCP 도구의 `date_from/date_to`는 결과 수신 후 클라이언트단에서 필터링.
- 세목 서버측 필터(`ntstTlawClCdList`): 코드 매핑표 미확정 → 클라이언트단 `tax_type_filter` 사용.

### 이 시스템에 **없는** 것
- 지방세(취득세·재산세 등) 사건 — 조세심판원 결정례도 국세 사건만 색인됨
- 감사원 심사청구

---

## 2. 지방세법령정보시스템 (olta.re.kr)

### 호출 방식
- 엔드포인트: `POST https://olta.re.kr/search/PU_0003_search.jsp`
- 요청: `csrfToken=null`(문자열 그대로, 실질 검증 없음), `query`, `querySub`
- 응답: **HTML** (BeautifulSoup 파싱, 화면 구조 변경시 깨질 수 있음)
- 인증: JSESSIONID 쿠키 (진입 페이지 GET 1회로 확보)
- 주의: `www.olta.re.kr`은 일부 환경에서 DNS 문제 발생 → **`olta.re.kr`(www 없이) 사용**

### 호출 가능한 카테고리 (6개, 통합검색 결과 화면 기준)

| MCP 키 | 화면 표기 (p.se_title) | 내용 | 문서번호 예시 |
|---|---|---|---|
| `tax_tribunal` | 조세심판원 결정례 | 조세심판원 결정 (지방세) | 조심2026지0284 |
| `audit` | 감사원 결정례 | 감사원 심사청구 결정 | 감심2022-433 |
| `constitutional` | 헌법재판소 결정례 | 헌재 결정 | 2017헌바363 |
| `court` | 법원판례 | 대법원 + 하급심 판결 | 서울고등법원 2023구합50233 |
| `mole_ruling` | 법제처해석 | 법제처 유권해석 | 법제처24-0772 |
| `moi_ruling` | 행정안전부 유권해석 | 행안부 유권해석 | 부동산세제과-1666 |

**참고**: 사이트 내부 코드표에는 "자치단체 질의회신"(코드 80000)이 정의되어 있으나,
**통합검색 결과 화면에는 카테고리로 노출되지 않아 현재 호출 불가**. 별도 목록 페이지가
있는지는 추후 조사 대상.

### 항목별 반환 필드

| MCP 반환 키 | 설명 | 비고 |
|---|---|---|
| title | 제목 | |
| doc_no | 사건번호 | |
| date | 날짜 (YYYYMMDD로 정규화) | 법원판례는 원본이 2025.08.14 형식 → 변환됨 |
| tax_type | 세목 (취득세/재산세/등...) | |
| result | 처리결과 (기각/합헌/처분청 승소 등) | 심판·헌재·법원만 제공, 유권해석은 null |
| summary | 요지 (미리보기 텍스트) | 법원판례는 빈 경우 많음 |
| doc_id | 팝업 문서 ID | 상세 페이지 접근용 (팝업 URL 미구현) |
| court_level | 법원 급 (대법원/하급심) | 법원판례에만 존재 |

### 지원되는 검색 옵션
- **키워드 검색만 지원** (통합검색 미리보기 방식)
- 카테고리당 **미리보기 3건**만 반환됨 — `view_count`를 크게 줘도 3건 초과 확보 불가
- 정렬/기간/페이지네이션: 통합검색 단계에서는 미지원
  (각 카테고리의 전용 목록 페이지 `~List.do`에 상세검색 폼이 존재하므로, 페이지네이션이
  필요하면 그쪽 엔드포인트를 추가 분석해야 함 — 다음 개선 후보 1순위)

### 이 시스템에 **없는** 것
- 국세(양도세·법인세·부가세 등) 사건
- 행안부 유권해석 외의 사전답변류 (지방세는 사전답변 제도 운영 방식이 다름)

---

## 3. 두 시스템 관계 정리

| 항목 | NTS (국세) | OLTA (지방세) |
|---|---|---|
| 조세심판원 결정례 | 국세 사건만 (조심-YYYY-지역-NNNN) | 지방세 사건만 (조심YYYY지NNNN) |
| 감사원 | 없음 | 있음 (감심YYYY-NNN) |
| 헌법재판소 | 없음 | 있음 |
| 법원판례 | 있음 (국세 사건) | 있음 (지방세 사건) |
| 사전답변/질의회신 | 있음 (국세청·기재부) | 행안부 유권해석으로 대응 |
| 법제처 해석 | question 컬렉션에 일부 포함 | 별도 카테고리 |

→ **조세심판원 사건번호 체계가 완전히 달라 실제 중복은 발생하지 않음** (실측 확인).
`nts_and_olta_precedent_search`의 중복 제거는 만일을 위한 안전장치.

---

## 4. MCP 서버 노출 도구 요약 (현재 v3)

| 도구 | 소스 | 주요 파라미터 |
|---|---|---|
| `nts_ruling_search` | NTS | keyword, collections, page, view_count, date_from/to(클라단), sort, tax_type_filter, include_full_text |
| `nts_ruling_get_by_doc_no` | NTS | doc_no |
| `olta_ruling_search` | OLTA | keyword, categories, view_count(최대 3 실효), tax_type_filter |
| `nts_and_olta_precedent_search` | 둘 다 | keyword, view_count, tax_type_filter |

### 서버 모드
- **stateful streamable-http** (`stateless_http=False`) — initialize 시 `Mcp-Session-Id` 헤더로
  세션 ID가 발급되며, 이후 모든 요청에 이 헤더를 포함해야 함. 세션 없는 요청은
  `400 Missing session ID`로 거부됨.
- 세션은 서버 프로세스 메모리에 저장되므로 **Railway 단일 인스턴스(1 replica) 전제**.
  재배포/재시작 시 기존 세션은 무효화되며 클라이언트가 다시 initialize 해야 함.
- 직접 호출(curl/스크립트) 시 필수 헤더: `Accept: application/json, text/event-stream`,
  그리고 initialize 후 받은 `Mcp-Session-Id`.

## 5. 개선 이력 (v4에서 모두 반영 완료)

1. ~~OLTA 페이지네이션~~ → **완료.** `collection` 파라미터(screen/evaluation/ordinance/
   sentencing/legal/authoritative) + `startCount`(10건 단위) + `startDate/endDate`
   (YYYY.MM.DD) + `sort`(RANK/DATE)를 실측으로 확보. `olta_collection_search` 도구로 노출.
2. ~~OLTA 문서 본문 조회~~ → **완료.** 팝업 URL 패턴 확보:
   조세심판원 `/explainInfo/judgeDecisionDetail.do?num={doc_id}`,
   헌재 `/explainInfo/constitutionDcnDetail.do?num={doc_id}`.
   `olta_get_detail` 도구로 노출. (법원판례는 인자 2개 필요 구조라 미지원)
3. ~~NTS 세목 코드 매핑~~ → **완료.** 세목 코드표(301 국세기본 ~ 315 교육세) 실측 확보,
   `ntstTlawClCdList` 서버측 필터 동작 검증. `tax_type_filter`에 정확한 세목명을 주면
   자동으로 서버측 필터를 사용하고, 그 외 문자열이면 클라이언트단 후처리로 동작.
4. **NTS 기간 서버 필터** — 미해결 (통합검색 API에는 기간 파라미터가 없음이 재확인됨).
   클라이언트단 필터로 계속 처리. 컬렉션별 전용 화면(사전답변 목록 등)은 추후 조사 가능.

### NTS 세목 코드표 (실측)

| 코드 | 세목 | 코드 | 세목 |
|---|---|---|---|
| 301 | 국세기본 | 309 | 조세특례 |
| 302 | 국세징수 | 310 | 국제조세 |
| 303 | 법인세 | 311 | 종합부동산세 |
| 305 | 종합소득세 | 312 | 원천세 |
| 306 | 부가가치세 | 313 | 소비세 |
| 307 | 양도소득세 | 314 | 주세 |
| 308 | 상속증여세 | 315 | 교육세 |

### OLTA 컬렉션 코드표 (실측)

| MCP 카테고리 | 서버 collection 값 | 본문 조회 |
|---|---|---|
| tax_tribunal | screen | 지원 |
| audit | evaluation | 미지원 |
| constitutional | ordinance | 지원 |
| court | sentencing | 미지원 (URL 인자 2개 구조) |
| mole_ruling | legal | 미지원 |
| moi_ruling | authoritative | 미지원 |
