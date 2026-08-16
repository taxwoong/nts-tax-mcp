# 데이터 소스 사양서 — 호출 가능 목록 정리

`nts-tax-mcp` 서버가 다루는 세 데이터 소스의 실제 호출 가능 범위, 컬렉션/카테고리 목록,
반환 필드, 코드표를 실측 기반으로 정리한 문서입니다. NTS/OLTA는 2026-07 조사 기준,
law.go.kr(법제처)은 2026-08 서버컴퓨터 이전 시 `server_ext.py`로 통합된 내용 기준입니다.

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

## 3. 국가법령정보센터 law.go.kr Open API (`server_ext.py`에서만 노출)

### 호출 방식
- 베이스: `http://www.law.go.kr/DRF`
- 검색: `GET lawSearch.do` / 본문: `GET lawService.do`
- 요청: `OC`(기관코드, 환경변수 `LAW_API_OC` — law.go.kr 가입 시 발급, 필수) + `type=XML` + `target`(대상 구분) 등
- 응답: **XML** (JSON 지원이 target마다 들쭉날쭉해 XML로 통일 후 정규식으로 경량 파싱)
- 인증: **IP 화이트리스트**. `open.law.go.kr` → OpenAPI 신청내역에 서버 공인 IP를
  사전 등록해야 하며, 미등록 IP는 응답 앞부분에 "인증"+"실패" 문자열이 포함되어
  클라이언트가 이를 감지해 명시적 오류로 변환한다 (`LawGoKrError`).

### `target`별 MCP 도구 매핑 (7개)

| `target` | 내용 | 대응 MCP 도구 |
|---|---|---|
| `prec` | 판례 (대법원·하급심) | `court_case_search`, `court_case_detail` |
| `expc` | 법령해석례 | `law_interpretation_search` |
| `law` | 현행 법령 검색 / 특정 시행본(MST) 원문 | `law_history_search`(current_only), `law_article_as_of` 내부 |
| `eflaw` | 법령 연혁(전체 시행본 목록) | `law_history_search`, `law_article_as_of` 내부 |
| `admrul` | 행정규칙 (훈령·예규·고시·기본통칙) | `admin_rule_search` |
| `trty` | 조약 (조세조약 포함) | `treaty_search` |
| `ordin` | 자치법규 (조례·규칙) | `ordinance_search` |

### 항목별 주요 반환 필드

| 대상 | 목록 조회 필드 | 본문 조회 필드 |
|---|---|---|
| 판례(`prec`) | 판례일련번호, 사건명, 사건번호, 법원명, 선고일자, 판결유형, 사건종류명 | 판시사항, 판결요지, 참조조문, 참조판례, 판례내용 |
| 법령해석례(`expc`) | 해석례일련번호, 안건명, 안건번호, 회신기관, 회신일자 | 질의요지, 회답, 이유 |
| 법령(`law`/`eflaw`) | 법령명, 법령ID, MST(법령일련번호), 시행일자, 공포일자, 공포번호, 제개정구분 | 조문 원문(조번호 기준 슬라이스), 조문제목 |
| 행정규칙(`admrul`) | 일련번호, 행정규칙명, 종류, 소관부처, 발령일자, 발령번호, 시행일자 | 본문 전체(길이 포함) |
| 조약(`trty`) | 조약일련번호, 조약명, 조약구분, 서명일자, 발효일자 | 본문 전체(길이 포함) |
| 자치법규(`ordin`) | 일련번호, 자치법규명, 지자체, 공포일자, 시행일자, 제개정구분 | 본문 전체(길이 포함) |

### 도구별 구현 특이사항 (실측)
- **`court_case_search`**: `search=2`(본문 포함 검색), `curt`(법원 필터), `prncYd`(선고일자
  범위, 미지정시 `19450815~20991231`)로 동작.
- **`law_article_as_of`**: `eflaw`로 연혁 시행본 목록을 받아 `as_of_date` 이하 최대
  시행일자 본을 고른 뒤, 그 MST로 `law` 원문을 받아 `<조문번호>` 태그 **위치 기준
  순차 슬라이스**로 조문을 잘라낸다 (단순 `<조문>…</조문>` 정규식은 실패함을 확인,
  2026-07-21). 가지조문(`104의3` 형식)도 `<조문가지번호>`로 매칭. `law_id`를 안 주면
  같은 이름의 본법/시행령/시행규칙이 섞여 의도치 않은 시행본이 선택될 수 있음.
- **`law_history_search`**: `eflaw`를 페이지당 100건씩 최대 5페이지(최대 500건)까지
  수집. 그 이상 시행본이 있는 법령은 일부 누락 가능(극히 드문 케이스).
- **`ordinance_search`**의 `region` 필터는 **서버측이 아닌 클라이언트단 후처리**
  (반환된 지자체기관명에 문자열 포함 여부로 필터링).
- **`treaty_search`**: API 응답의 아이템 태그가 `Trty`/`trty`로 대소문자가 섞여 오는
  경우가 있어 두 태그를 모두 파싱해 합친다.

### 이 시스템에 **없는** 것 / 제한
- 국세청/기재부의 사전답변·서면질의·질의회신 (이건 NTS `nts_ruling_search`가 담당)
- 지방세 조세심판원·감사원·자치단체 질의회신 (이건 OLTA 쪽이 담당 — law.go.kr은
  판례·법령·해석례·행정규칙·조약·자치법규만 제공)
- IP 미등록 상태에서는 이 7개 target 전체가 "인증 실패"로 막힘 — 부분 등록(target별
  개별 허용) 개념은 없음.

---

## 4. 세 시스템 관계 정리

| 항목 | NTS (국세) | OLTA (지방세) | law.go.kr (법제처) |
|---|---|---|---|
| 조세심판원 결정례 | 국세 사건만 (조심-YYYY-지역-NNNN) | 지방세 사건만 (조심YYYY지NNNN) | 없음 |
| 감사원 | 없음 | 있음 (감심YYYY-NNN) | 없음 |
| 헌법재판소 | 없음 | 있음 | 없음 |
| 법원판례 | 있음 (국세 사건, taxlaw.nts.go.kr 자체 색인) | 있음 (지방세 사건) | 있음 (전체 법원 판례 DB, 세목 무관) |
| 사전답변/질의회신 | 있음 (국세청·기재부) | 행안부 유권해석으로 대응 | 없음 (대신 법령해석례 `expc`) |
| 법령 원문·연혁 | 현행 조문만 (`statute` 컬렉션) | 없음 | 있음 — 연혁 시행본 + 특정 시점 조문(`law_article_as_of`) |
| 행정규칙(기본통칙 등) | 없음 | 없음 | 있음 (`admin_rule_search`) |
| 조세조약 | 국제조세 해설(`intl`)만, 조약 원문은 없음 | 없음 | 있음 (`treaty_search`, 조약 원문·발효일) |
| 자치법규(조례) | 없음 | 없음 | 있음 (`ordinance_search`) |

→ **NTS/OLTA 조세심판원 사건번호 체계가 완전히 달라 실제 중복은 발생하지 않음** (실측 확인).
`nts_and_olta_precedent_search`의 중복 제거는 만일을 위한 안전장치. law.go.kr의 판례(`prec`)는
NTS/OLTA의 법원판례와 **별도 DB**라 문서번호 체계도 다르고 중복 제거 대상에도 포함되지 않는다.

---

## 5. MCP 서버 노출 도구 요약 (현재 v5, `server_ext.py` 기준 14개)

### 기본 6개 (`server.py`)

| 도구 | 소스 | 주요 파라미터 |
|---|---|---|
| `nts_ruling_search` | NTS | keyword, collections, page, view_count, date_from/to(클라단), sort, tax_type_filter, include_full_text |
| `nts_ruling_get_by_doc_no` | NTS | doc_no |
| `olta_ruling_search` | OLTA | keyword, categories, view_count(최대 3 실효), tax_type_filter |
| `olta_collection_search` | OLTA | keyword, category, page, view_count, date_from/to(서버단), sort |
| `olta_get_detail` | OLTA | category, doc_id |
| `nts_and_olta_precedent_search` | 둘 다 | keyword, view_count, tax_type_filter |

### 확장 8개 (`server_ext.py`에서만, law.go.kr)

| 도구 | target | 주요 파라미터 |
|---|---|---|
| `court_case_search` | prec | keyword, court, date_from/to, display, page |
| `court_case_detail` | prec | case_serial, max_chars |
| `law_interpretation_search` | expc | keyword, display, serial |
| `law_history_search` | law/eflaw | law_name, law_id, current_only |
| `law_article_as_of` | eflaw→law | law_name, as_of_date, article_no, law_id |
| `admin_rule_search` | admrul | keyword, serial, display |
| `treaty_search` | trty | keyword, serial, display |
| `ordinance_search` | ordin | keyword, region(클라단 필터), serial, display |

전체 파라미터 설명은 `README.md`의 "도구 파라미터 참고" 참고.

### 서버 모드
- **stateful streamable-http** (`stateless_http=False`) — initialize 시 `Mcp-Session-Id` 헤더로
  세션 ID가 발급되며, 이후 모든 요청에 이 헤더를 포함해야 함. 세션 없는 요청은
  `400 Missing session ID`로 거부됨.
- 세션은 서버 프로세스 메모리에 저장되므로 **단일 인스턴스 전제**. 예전엔 Railway
  단일 replica가 이 전제를 충족했고, 2026-08 이전 후에는 서버컴퓨터에서 `server_ext.py`
  프로세스 1개를 상시 구동하는 방식(Tailscale Funnel로 노출)으로 동일한 전제를
  유지한다. 프로세스 재시작 시 기존 세션은 무효화되며 클라이언트가 다시 initialize 해야 함.
- 직접 호출(curl/스크립트) 시 필수 헤더: `Accept: application/json, text/event-stream`,
  그리고 initialize 후 받은 `Mcp-Session-Id`.

## 6. 개선 이력 (v5까지 반영)

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
5. ~~법제처 law.go.kr 통합~~ → **완료 (v5, 2026-08 서버컴퓨터 이전과 함께).** 판례
   (`prec`)·법령해석례(`expc`)·법령 연혁 및 특정 시점 조문(`law`/`eflaw`)·행정규칙
   (`admrul`)·조약(`trty`)·자치법규(`ordin`) 6개 target을 `law_go_kr.py`로 클라이언트화하고,
   `server_ext.py`에서 도구 8개로 노출. `law_article_as_of`의 조문 위치 기반 슬라이스
   파싱 방식은 2026-07-21에 별도로 확립되어 있던 것을 이전 과정에서 통합했다. IP
   화이트리스트 전제는 3장 참고.

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
