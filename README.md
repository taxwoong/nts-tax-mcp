# nts-tax-mcp

국세법령정보시스템(taxlaw.nts.go.kr) + 지방세법령정보시스템(olta.re.kr) 통합검색을
Claude에서 바로 쓸 수 있게 해주는 MCP(Model Context Protocol) 서버입니다.

**국세**: 사전답변 · 서면질의 · 질의회신(국세청/기획재정부/법제처), 조세심판원 심판청구,
국세청 심사청구, 법원 판례, 법령

**지방세** (v3에서 추가): 취득세 · 재산세 · 자동차세 · 지방소득세 · 등록면허세 관련
조세심판원 결정례, 감사원 심사결정례, 헌법재판소 결정례, 법원판례, 법제처/행정안전부
유권해석, 자치단체 질의회신

## v3 — 지방세법령정보시스템(olta.re.kr) 추가

국세와 지방세는 조세심판원 사건번호 체계 자체가 다릅니다.
- 국세: `조심-YYYY-지역청코드-NNNN` (예: 조심-2023-서-9465)
- 지방세: `조심YYYY지NNNN` (예: 조심2026지0284)

실제로 두 시스템에서 같은 키워드로 검색해본 결과, **조세심판원 결정례는 거의 겹치지 않습니다**
(국세청 시스템은 지방세 사건을 색인하지 않음). 그래도 안전하게 `nts_and_olta_precedent_search`
도구는 문서번호 정규화 후 중복을 제거하고 `duplicates_removed` 건수를 함께 알려줍니다.

## 파일 구성

```
nts-tax-mcp/
├── server.py                    # MCP 서버 본체 (FastMCP) — 도구 6개
├── nts_tax_ruling_search.py     # 국세: taxlaw.nts.go.kr 검색 클라이언트
├── olta_tax_ruling_search.py    # 지방세: olta.re.kr 검색 클라이언트
├── test_mcp_client.py           # 서버 상태 독립 점검 스크립트
├── client/                      # MCP 커넥터 우회 독립 클라이언트 (CLI 포함)
│   ├── nts_client.py
│   ├── nts_search.py
│   └── README.md
├── requirements.txt
└── Procfile                     # Railway 배포용
```

## 제공 도구 6개

| 도구 | 용도 |
|---|---|
| `nts_ruling_search` | 국세 통합검색 (세목명이 정확하면 서버측 세목필터 자동 적용) |
| `nts_ruling_get_by_doc_no` | 국세 문서 사건번호로 직접 조회 |
| `olta_ruling_search` | 지방세 통합검색 (전체 카테고리 미리보기, 카테고리당 3건) |
| `olta_collection_search` | 지방세 특정 카테고리 깊은 탐색 — 페이지네이션·기간·최신순 정렬 (서버측) |
| `olta_get_detail` | 지방세 문서 본문 전문 조회 (조세심판원·헌재 지원) |
| `nts_and_olta_precedent_search` | 국세+지방세 조세심판원 계열을 한 번에, 중복 제거해서 검색 |

## v4 — 심층 검색 기능 (개선 후보 전면 반영)

- **OLTA 페이지네이션·기간·정렬**: `olta_collection_search`로 특정 카테고리를 10건 단위로
  깊게 탐색. 기간(YYYYMMDD)과 최신순 정렬은 서버측에서 처리되어 정확합니다.
- **OLTA 본문 조회**: `olta_get_detail`로 조세심판원·헌재 결정문 전문(결정요지·처분개요·판단)
  을 가져옵니다.
- **NTS 서버측 세목필터**: `tax_type_filter`에 정확한 세목명(양도소득세, 법인세, 부가가치세,
  상속증여세, 종합부동산세 등 14종)을 주면 서버측 코드 필터가 자동 적용되어, 전체 데이터
  기준으로 정확하게 걸러집니다.

세부 데이터 사양·코드표는 `DATA_SOURCES.md` 참고.


## v2.1 버그 수정 (중요)

`client/` 폴더 작업 중 발견된 문제를 수정했습니다.

- **날짜 필터(`date_from`/`date_to`)가 검색 자체를 깨뜨리던 문제** — taxlaw.nts.go.kr
  통합검색 화면에는 애초에 기간 필터 UI가 없어서, 이전 버전에서 추측으로 넣었던
  `bltnStrtDtm`/`bltnEndDtm` 서버 파라미터가 잘못된 값으로 취급되어 **검색 결과가
  통째로 0건으로 나오는 문제**가 있었습니다. 이번에 해당 파라미터를 제거하고,
  결과를 받아온 뒤 `date` 필드로 걸러내는 **클라이언트단 필터**로 교체했습니다.
- 문서번호(`doc_no`) 필드에 검색어 하이라이트 마커(`<!HS>`, `<!HE>`)가 안 지워져
  `nts_ruling_get_by_doc_no`의 정확 매칭이 실패하던 문제도 함께 수정했습니다.

## MCP 커넥터 우회 독립 클라이언트 (`client/`)

Claude 커넥터 연결이 불안정할 때, MCP를 거치지 않고 서버에 직접 접속해서 검색할 수
있는 독립 클라이언트를 `client/` 폴더에 추가했습니다. 사용법은 `client/README.md` 참고.

```bash
cd client
python nts_search.py --ping
python nts_search.py "조정대상지역" -c precedent -n 10
```

## v2 개선사항

최초 버전 이후 아래 항목들을 개선했습니다.

| # | 개선 내용 |
|---|---|
| ① | 페이지네이션(`page`) 지원 — "더 보여줘" 같은 후속 요청 대응 |
| ② | 사건번호로 직접 조회 (`nts_ruling_get_by_doc_no`) — 이미 아는 문서를 재검색 없이 바로 확인 |
| ③ | 검색 결과 0건일 때 안내 메시지(`_guidance`) 자동 첨부 |
| ④ | 세목 필터(`tax_type_filter`) — 클라이언트단 후처리 방식 (서버 세목코드 매핑표는 미확정) |
| ⑤ | 정렬 옵션(`sort`) — 정확도순/최신순/오래된순 |
| ⑥ | 응답 크기 관리(`include_full_text=False`) — 본문 생략, 요약만 조회 가능 |
| ⑦ | 세션 만료 자동 감지 및 재접속 |
| ⑧ | 캐싱(기본 5분) + 최소 요청 간격(기본 0.5초) — 정중한 크롤링 |
| ⑨ | 예상치 못한 응답 구조에 대한 로깅 |

## 1. 로컬 실행 확인

```bash
pip install -r requirements.txt
python server.py
```

기본적으로 `http://0.0.0.0:8000/mcp` 에서 streamable-http 방식으로 서비스됩니다.
포트는 환경변수 `PORT`로 바꿀 수 있습니다.

```bash
PORT=8765 python server.py
```

### 환경변수 옵션

| 변수 | 기본값 | 설명 |
|---|---|---|
| `PORT` | 8000 | 서버 포트 |
| `NTS_VERIFY_SSL` | true | SSL 인증서 검증 여부. 사내망/프록시에서 인증서 오류 시에만 `false`로 임시 우회 |
| `NTS_CACHE_TTL` | 300 | 동일 검색 결과 캐시 유지 시간(초) |
| `NTS_MIN_REQUEST_INTERVAL` | 0.5 | 국세청 서버로 보내는 요청 사이 최소 간격(초) |
| `LOG_LEVEL` | INFO | 로깅 레벨 (DEBUG로 두면 세션 재접속/캐시 히트 등이 상세히 찍힘) |

## 2. Railway 배포

1. 이 폴더를 새 GitHub 저장소로 올립니다.
2. Railway에서 "New Project" → "Deploy from GitHub repo" 선택.
3. Railway가 `Procfile`을 인식해서 `python server.py`로 자동 실행합니다.
   (`PORT` 환경변수는 Railway가 자동으로 주입합니다.)
4. 배포가 끝나면 Railway가 발급하는 도메인 뒤에 `/mcp`를 붙인 주소가
   실제 MCP 서버 URL이 됩니다.

## 3. Claude에 커넥터로 등록

1. claude.ai 접속 → 프로필 → 설정(Settings) → 커넥터(Connectors)
2. "사용자 지정 커넥터 추가(Add custom connector)" 클릭
3. 이름: `국세법령정보센터` (원하는 이름으로)
4. URL: 2번에서 확인한 `.../mcp` 주소 입력 후 저장
5. 도구 권한을 **"항상 허용"**으로 설정 (기본값 "승인 필요"는 매번 승인을 물어봄)
6. **완전히 새 대화창**을 열어서 도구 목록에 뜨는지 확인
   (커넥터를 새로 켠 직후에는 기존에 열려 있던 대화창에 반영되지 않을 수 있습니다)

## 4. 사용 예시 (Claude 채팅에서)

- "국세법령정보센터에서 조정대상지역 관련 질의회신이랑 심판례 찾아줘"
- "부당행위계산 부인 관련 최근 조세심판원 결정례 있는지 확인해줘. 2024년 이후만."
- "조심-2023-서-9465 판례 원문 보여줘" (사건번호 직접 조회)
- "양도소득세만 걸러서 다시 보여줘" (세목 필터)
- "취득세 중과 관련 지방세 심판례 찾아줘" (지방세 → `olta_ruling_search`)
- "재산세 과세기준일 관련해서 감사원 결정례 있는지 확인해줘" (지방세 → `olta_ruling_search`)
- "조정대상지역 관련해서 국세랑 지방세 심판례 다 찾아줘, 중복은 빼고" (→ `nts_and_olta_precedent_search`)

## 5. 서버 상태 독립 점검 (Claude 없이 확인하기)

Claude 채팅에서 도구가 안 잡히는 문제가 생겼을 때, **서버 자체 문제인지 Claude 쪽 문제인지**를
빠르게 구분하기 위한 스크립트입니다. Claude를 거치지 않고 서버에 직접 MCP 프로토콜로 요청을
보내서 initialize → tools/list → tools/call까지 전체 흐름을 검증합니다.

```bash
python test_mcp_client.py
```

기본적으로 배포된 Railway 서버(`https://web-production-10fe2.up.railway.app/mcp`)를 검사합니다.
다른 주소나 로컬 서버를 검사하려면:

```bash
python test_mcp_client.py --url http://127.0.0.1:8000/mcp
```

**이 스크립트가 전부 성공하는데 Claude 채팅에서는 도구가 안 보인다면**, 원인은 서버가 아니라
Claude 쪽 커넥터 인식/캐싱 문제입니다. 이 경우 아래를 시도해 보세요.

- 완전히 새 대화창에서 다시 확인 (커넥터를 새로 켠 직후엔 기존 대화창에 반영 안 될 수 있음)
- 설정 → 커넥터에서 해당 커넥터를 삭제 후 재등록
- 그래도 안 되면 `support.claude.com`에 문의 (Claude 플랫폼 쪽 반영 지연/버그일 가능성)

## 도구 파라미터 참고

### `nts_ruling_search`

| 파라미터 | 설명 |
|---|---|
| `keyword` | 검색어 (필수) |
| `collections` | 검색 범위 제한. 생략시 전체.<br>`form`(별표서식), `statute`(법령), `ruling`(사전답변·서면질의·질의회신), `precedent`(심판·심사·판례), `old_ruling`(구 법령해석자료), `intl`(국제조세 해설), `hometax`(홈택스 상담사례) |
| `page` | 페이지 번호 (1부터 시작) |
| `view_count` | 컬렉션별로 가져올 결과 개수 (기본 20) |
| `date_from` / `date_to` | 검색 기간 (YYYYMMDD) |
| `sort` | `relevance`(정확도순, 기본) / `date_desc`(최신순) / `date_asc`(오래된순) |
| `tax_type_filter` | 세목명에 이 문자열이 포함된 것만 남김 (예: "양도소득세") |
| `include_full_text` | `false`면 본문 생략, 요약(`summary`)만 반환 |

### `nts_ruling_get_by_doc_no`

| 파라미터 | 설명 |
|---|---|
| `doc_no` | 사건번호/문서번호. 예: `조심-2023-서-9465`, `서면-2019-법규재산-4276`, `기획재정부 재산세제과-73` |

### `olta_ruling_search` (지방세)

| 파라미터 | 설명 |
|---|---|
| `keyword` | 검색어 (필수) |
| `categories` | 검색 범위 제한. 생략시 전체.<br>`court`(법원판례), `moi_ruling`(행안부 유권해석), `mole_ruling`(법제처해석), `tax_tribunal`(조세심판원 결정례), `audit`(감사원 결정례), `constitutional`(헌법재판소 결정례), `local_gov_ruling`(자치단체 질의회신) |
| `view_count` | 카테고리별 최대 결과 개수 (기본 20). 사이트 구조상 카테고리당 미리보기 몇 건까지만 확보 가능 |
| `tax_type_filter` | 세목명에 이 문자열이 포함된 것만 남김 (예: "취득세", "재산세") |

### `nts_and_olta_precedent_search` (국세+지방세 통합, 중복제거)

| 파라미터 | 설명 |
|---|---|
| `keyword` | 검색어 (필수) |
| `view_count` | 각 소스에서 가져올 결과 개수 (기본 20) |
| `tax_type_filter` | 세목 필터 |

반환값에 `nts_precedent`, `olta_precedent`, `duplicates_removed`(실제 제외된 중복 건수)가 포함됩니다.

### `olta_collection_search` (지방세 심층 탐색)

| 파라미터 | 설명 |
|---|---|
| `keyword` | 검색어 (필수) |
| `category` | 카테고리 1개 지정 (필수): `tax_tribunal`, `audit`, `constitutional`, `court`, `mole_ruling`, `moi_ruling` |
| `page` | 페이지 번호 (1부터, 페이지당 10건 서버 고정) |
| `view_count` | 반환 개수 (최대 10) |
| `date_from` / `date_to` | 검색 기간 YYYYMMDD (**서버측 필터**) |
| `sort` | `relevance`(정확도순) / `date_desc`(최신순) — 서버측 정렬 |

### `olta_get_detail` (지방세 본문 조회)

| 파라미터 | 설명 |
|---|---|
| `category` | `tax_tribunal`(조세심판원) 또는 `constitutional`(헌법재판소) |
| `doc_id` | 검색 결과 항목의 `doc_id` 값 |

결정요지·참조조문·처분개요·판단 등 본문 전문 텍스트를 반환합니다.

## 알려진 제한 사항 (v4 기준)

- **NTS 세목 필터**: 정확한 세목명(양도소득세 등 14종, `DATA_SOURCES.md` 코드표 참고)을 주면
  서버측 필터가 적용되고, 그 외 문자열은 클라이언트단 후처리로 동작합니다.
- **NTS 기간 필터**: 통합검색 API에 기간 파라미터가 존재하지 않음이 확인되어(실측),
  `date_from/date_to`는 클라이언트단 필터로 처리됩니다. 최신순 정렬(`sort=date_desc`)과
  함께 쓰면 더 안정적입니다.
- **감사원 심사청구(국세)**: 이 서버의 범위에 포함되지 않습니다. (지방세 감사원 결정례는
  `olta_ruling_search` / `olta_collection_search`로 커버됩니다.)
- **`nts_ruling_get_by_doc_no`**: 전용 상세조회 API가 확인되지 않아, 문서번호를 검색어로
  활용하는 방식으로 구현되어 있습니다.
- **OLTA HTML 파싱**: olta.re.kr은 HTML로 응답하는 구조라 BeautifulSoup으로 파싱합니다.
  사이트 화면 구조가 바뀌면(클래스명 `p.se_title`, `ul.search_out` 등) 파싱이 깨질 수 있습니다.
- **`olta_ruling_search`(통합검색)는 카테고리당 미리보기 3건**만 반환됩니다. 더 많은 결과가
  필요하면 `olta_collection_search`(페이지당 10건, 페이지네이션·기간·정렬 지원)를 사용하세요.
- **`olta_get_detail` 본문 조회**는 조세심판원·헌법재판소만 지원합니다. 법원판례는 상세 URL이
  인자 2개를 요구하는 구조라 미지원이며, 유권해석류는 요지(summary)로 갈음합니다.
- **자치단체 질의회신**: olta.re.kr 내부 코드표에는 존재하지만 통합검색 결과 화면에
  노출되지 않아 현재 검색 불가입니다.
- **중복 제거**: 국세/지방세 조세심판원 사건번호 체계가 달라 실제 중복은 발생하지 않음을
  실측으로 확인했으며, `nts_and_olta_precedent_search`의 정규화 기반 중복 제거는 안전장치입니다.
