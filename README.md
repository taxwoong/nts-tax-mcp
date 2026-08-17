# nts-tax-mcp

국세법령정보시스템(taxlaw.nts.go.kr) + 지방세법령정보시스템(olta.re.kr) 통합검색을
Claude에서 바로 쓸 수 있게 해주는 MCP(Model Context Protocol) 서버입니다.

**국세**: 사전답변 · 서면질의 · 질의회신(국세청/기획재정부/법제처), 조세심판원 심판청구,
국세청 심사청구, 법원 판례, 법령

**지방세** (v3에서 추가): 취득세 · 재산세 · 자동차세 · 지방소득세 · 등록면허세 관련
조세심판원 결정례, 감사원 심사결정례, 헌법재판소 결정례, 법원판례, 법제처/행정안전부
유권해석, 자치단체 질의회신

**법령정보 확장** (v5, 확장판 `server_ext.py`에서 추가): 국가법령정보센터(law.go.kr)
Open API 기반으로 대법원·하급심 판례, 법령 연혁·특정 시점 조문, 법령해석례, 행정규칙
(기본통칙 등), 조세조약, 자치법규(조례)까지 커넥터 하나로 검색

## v5 — 서버컴퓨터 이전 + 법제처(law.go.kr) 도구 8개 추가 (2026-08)

Railway 크레딧 소진으로 서버가 죽어(2026-08-08) 자체 서버컴퓨터 상시 구동 +
[Tailscale Funnel](https://tailscale.com/kb/1223/funnel) 노출 방식으로 이전했습니다
(2026-08-09 완료). 이전 작업 중 법제처(law.go.kr) Open API 도구 8개를 추가로 붙여서
커넥터 하나로 총 **14개 도구**를 쓸 수 있게 확장했습니다.

- **확장 진입점**: `server_ext.py` — 기존 `server.py`의 도구 6개(국세/지방세)를
  `from server import mcp`로 그대로 물려받고, `law_go_kr.py` 클라이언트를 통해
  법제처 도구 8개를 추가로 등록합니다. `server.py` 자체는 수정되지 않았으므로,
  기존 6개 도구만 필요하면 `server.py`를 그대로 실행해도 됩니다.
- **새 도구 8개**: `court_case_search`/`court_case_detail`(법제처 판례),
  `law_interpretation_search`(법령해석례), `law_history_search`(법령 연혁 시행본 목록),
  `law_article_as_of`(특정 날짜 시행 조문 원문 — 예규·판례 인용 당시 조문 확인용),
  `admin_rule_search`(행정규칙 — 기본통칙·조사사무처리규정·고시),
  `treaty_search`(조세조약 원문·발효일), `ordinance_search`(자치법규 — 지방세 감면조례 등)
- **전제조건**: law.go.kr Open API는 **등록된 IP에서만** 동작합니다.
  [open.law.go.kr](https://open.law.go.kr) → OpenAPI 신청내역에서 서버의 공인 IP를
  사전 등록해야 합니다. 미등록 상태면 법제처 8개 도구만 "인증 실패"가 뜨고 기존
  국세/지방세 6개 도구는 정상 동작합니다. 환경변수 `LAW_API_OC`(law.go.kr 가입 시
  발급받는 기관코드, 필수)로 인증 계정을 지정합니다. 개인 식별정보라 이 저장소에는
  실제 값을 커밋하지 않고, 서버컴퓨터에만 두는 `.gitignore`된 로컬 파일에서 불러옵니다.
- **현재 운영 방식**: 서버컴퓨터에서 `run_server.bat`(포트 `8734`, `server_ext.py` 실행)를
  Windows 작업 스케줄러(`nts-tax-mcp`, 부팅 시 SYSTEM 권한 자동 실행)로 상시 구동하고,
  `tailscale funnel --bg 8734`로 고정 주소 **`https://desktop-ika1349.tail81ecba.ts.net/mcp`**
  에 외부 노출합니다. 최초 설치는 `setup.ps1`(GitHub에서 소스 다운로드 → 의존성 설치 →
  작업 스케줄러 등록까지 자동화) 1회 실행으로 끝납니다.

## v5.1 — 조문 파싱 버그 수정 + 조문 잘림 명시 (2026-08-16)

- **절 첫 조문 파싱 버그 수정**: `law_article_as_of`가 절(節)·관·장이 시작되는 조문
  (예: 소득세법 104조·55조)을 조회하면 조문 본문 대신 "제6절 …" 같은 표제만 반환하던
  버그를 수정했습니다. 표제 노드가 실제 조문과 같은 `<조문번호>`를 단 채 먼저 나오는
  구조가 원인으로, 조번호 문자열이 본문에 없는 표제 블록을 걸러냅니다.
- **조문 잘림 명시 + `max_chars` 노출**: 조문이 `max_chars`(기본 6000자)를 넘으면
  이전에는 뒷부분(마지막 항들)이 아무 표시 없이 잘렸습니다. 이제 잘린 경우 응답에
  `"잘림"` 항목으로 전체 길이와 재조회 방법을 안내하고, `law_article_as_of` 도구에
  `max_chars` 파라미터를 추가해 전문을 받을 수 있습니다.
- **운영 주의 — `.bat`은 반드시 CRLF 줄바꿈**: `run_server.bat`이 LF 줄바꿈으로
  저장되면 cmd.exe가 줄을 건너뛰어 `PORT=8734` 설정이 무시되고 서버가 기본 포트
  8000으로 뜹니다(실제 장애 사례 — Funnel이 8734를 바라보므로 커넥터가 먹통이 됨).
  편집기에 따라 저장 시 줄바꿈이 LF로 바뀔 수 있으니 `.bat` 수정 후에는 CRLF인지
  확인하세요.

## v5.2 — 행정규칙 조문 단위 조회 (2026-08-17)

`admin_rule_search`에 `article`(조번호)·`max_chars`·`start_char` 파라미터를 추가했습니다.
외국환거래규정(재정경제부 고시, 약 30만 자) 같은 대형 고시는 전문 반환이 불가능해
이전에는 앞 10,000자만 보고 끝이었는데, 이제 조번호를 지정하면 해당 조문만 잘라
받습니다 — 예: 해외직접투자 신고는 `serial=외국환거래규정 일련번호, article="9-5"`
(제9-5조). 조번호를 모르면 `start_char` 오프셋으로 이어 읽을 수 있고, 잘린 경우
응답의 `"잘림"` 항목이 다음 조회 방법을 안내합니다.

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
├── server.py                    # MCP 서버 본체 (FastMCP) — 기본 도구 6개 (국세+지방세)
├── server_ext.py                # 확장 진입점 — server.py 6개 + 법제처 8개 = 14개 도구
├── nts_tax_ruling_search.py     # 국세: taxlaw.nts.go.kr 검색 클라이언트
├── olta_tax_ruling_search.py    # 지방세: olta.re.kr 검색 클라이언트
├── law_go_kr.py                 # 법령정보: law.go.kr Open API 클라이언트 (판례/법령/해석례/행정규칙/조약/자치법규)
├── test_mcp_client.py           # 서버 상태 독립 점검 스크립트
├── client/                      # MCP 커넥터 우회 독립 클라이언트 (CLI 포함)
│   ├── nts_client.py
│   ├── nts_search.py
│   └── README.md
├── requirements.txt
├── Procfile                     # Railway 배포용 (레거시 — 현재 운영은 서버컴퓨터+Tailscale Funnel)
├── setup.ps1                    # 서버컴퓨터 최초 설치 스크립트 (소스 다운로드→의존성→작업 스케줄러 등록)
├── run_server.bat               # 확장판(server_ext.py) 상시 구동용 — 작업 스케줄러가 부팅 시 실행
└── local_env.bat                # (커밋 안 됨) LAW_API_OC 등 개인 식별정보 — .gitignore 처리, 서버컴퓨터에서 직접 생성
```

## 제공 도구

`server.py`는 기본 6개, `server_ext.py`는 기본 6개 + 법제처 8개 = 총 14개 도구를 노출합니다.
실제 운영 서버(서버컴퓨터)는 `server_ext.py`로 구동되어 14개 도구가 모두 열려 있습니다.

### 기본 6개 (국세·지방세, `server.py`)

| 도구 | 용도 |
|---|---|
| `nts_ruling_search` | 국세 통합검색 (세목명이 정확하면 서버측 세목필터 자동 적용) |
| `nts_ruling_get_by_doc_no` | 국세 문서 사건번호로 직접 조회 |
| `olta_ruling_search` | 지방세 통합검색 (전체 카테고리 미리보기, 카테고리당 3건) |
| `olta_collection_search` | 지방세 특정 카테고리 깊은 탐색 — 페이지네이션·기간·최신순 정렬 (서버측) |
| `olta_get_detail` | 지방세 문서 본문 전문 조회 (조세심판원·헌재 지원) |
| `nts_and_olta_precedent_search` | 국세+지방세 조세심판원 계열을 한 번에, 중복 제거해서 검색 |

### 확장 8개 (법제처 law.go.kr, `server_ext.py`에서만 추가)

| 도구 | 용도 |
|---|---|
| `court_case_search` | 법제처 판례 검색 (대법원·하급심, 국세청 시스템 판례와 별도 DB) |
| `court_case_detail` | 판례 본문 전문 조회 (판시사항·판결요지·참조조문·판례내용) |
| `law_interpretation_search` | 법령해석례 검색/본문 조회 |
| `law_history_search` | 법령 연혁(전체 시행본 목록: 시행일자·공포번호·MST) 조회 |
| `law_article_as_of` | 특정 날짜 시행 중이던 법령 조문 원문 (예규·판례 인용 당시 조문 확인용) |
| `admin_rule_search` | 행정규칙(훈령·예규·고시 — 기본통칙·조사사무처리규정 등) 검색/본문 조회 |
| `treaty_search` | 조약(조세조약) 검색/본문 조회 — 원문·발효일 확인 |
| `ordinance_search` | 자치법규(조례·규칙 — 지방세 탄력세율·감면조례 등) 검색/본문 조회, 지자체 필터 |

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
| `LAW_API_OC` | 없음 (필수) | `server_ext.py` 전용. law.go.kr 가입 시 발급받는 기관코드 — 미설정시 법제처 8개 도구가 명시적 오류를 반환. 이 코드로 등록된 IP에서만 동작 (`open.law.go.kr` → OpenAPI 신청내역에서 서버 공인 IP 사전 등록 필요). 개인 식별정보이므로 소스에 직접 적지 말고 배포 환경에서 주입할 것 |

## 2. 배포

### 2-A. 현재 운영 방식 — 서버컴퓨터 상시 구동 + Tailscale Funnel (2026-08~)

Railway 크레딧 소진으로 서버가 다운된 뒤(2026-08-08), 자체 서버컴퓨터에서 상시 구동하는
방식으로 전환했습니다. 14개 도구(`server_ext.py`)가 이 방식으로 운영됩니다.

1. 서버컴퓨터 관리자 PowerShell에서 `setup.ps1` 1회 실행 — GitHub에서 소스를 받아
   의존성을 설치하고, Windows 작업 스케줄러에 `nts-tax-mcp`(부팅 시 SYSTEM 권한 자동 실행)를
   등록한 뒤 즉시 기동합니다.
   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass -Force
   .\setup.ps1
   ```
2. `run_server.bat`이 `PORT=8734`를 설정하고 `server_ext.py`를 실행합니다 (로그:
   `server.log`). `LAW_API_OC`는 이 파일에 직접 적지 않고, `.gitignore`된 로컬 파일
   (`local_env.bat` — `set LAW_API_OC=본인_기관코드` 한 줄)에서 불러옵니다. 이 파일이
   없으면 법제처 8개 도구만 동작하지 않고 기본 6개는 정상입니다.
3. [Tailscale](https://tailscale.com)을 설치해 로그인 후 Funnel로 외부에 고정 주소로 노출합니다.
   ```powershell
   tailscale funnel --bg 8734
   ```
4. 실제 MCP 서버 URL(고정): **`https://desktop-ika1349.tail81ecba.ts.net/mcp`**

포트를 바꾸면 `run_server.bat`의 `PORT`와 `tailscale funnel`의 대상 포트를 함께 바꿔야 합니다.
공유기에서 이 포트를 직접 포워딩하지 말고 Tailscale Funnel만 사용하세요.

### 2-B. Railway 배포 (레거시)

`Procfile`은 여전히 `python server.py`를 실행하므로, Railway로 배포하면 **기본 6개
도구만** 뜨고 법제처 8개 도구(`server_ext.py`)는 포함되지 않습니다. 크레딧이 소진되면
서버가 그대로 죽으므로 현재는 권장하지 않지만, 여전히 동작은 합니다.

1. 이 폴더를 GitHub 저장소로 올립니다.
2. Railway에서 "New Project" → "Deploy from GitHub repo" 선택.
3. Railway가 `Procfile`을 인식해서 `python server.py`로 자동 실행합니다.
   (`PORT` 환경변수는 Railway가 자동으로 주입합니다.)
4. 배포가 끝나면 Railway가 발급하는 도메인 뒤에 `/mcp`를 붙인 주소가
   실제 MCP 서버 URL이 됩니다.

## 3. Claude에 커넥터로 등록

1. claude.ai 접속 → 프로필 → 설정(Settings) → 커넥터(Connectors)
2. "사용자 지정 커넥터 추가(Add custom connector)" 클릭
3. 이름: 원하는 이름으로 (현재 운영 커넥터명: `Korea nts`)
4. URL: 2번에서 확인한 `.../mcp` 주소 입력 후 저장
   (현재 운영 주소: `https://desktop-ika1349.tail81ecba.ts.net/mcp`)
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
- "법령해석례에서 청산금 검색해줘" (→ `law_interpretation_search`)
- "소득세법 시행령 연혁 보여줘" (→ `law_history_search`)
- "부가가치세법 17조, 2008년 7월 15일 당시 조문 보여줘" (→ `law_article_as_of`)
- "법인세법 기본통칙 찾아줘" (→ `admin_rule_search`)
- "한·홍콩 조세조약 발효일 확인해줘" (→ `treaty_search`)
- "서울시 취득세 감면조례 찾아줘" (→ `ordinance_search`)

## 5. 서버 상태 독립 점검 (Claude 없이 확인하기)

Claude 채팅에서 도구가 안 잡히는 문제가 생겼을 때, **서버 자체 문제인지 Claude 쪽 문제인지**를
빠르게 구분하기 위한 스크립트입니다. Claude를 거치지 않고 서버에 직접 MCP 프로토콜로 요청을
보내서 initialize → tools/list → tools/call까지 전체 흐름을 검증합니다.

```bash
python test_mcp_client.py
```

스크립트 기본값은 예전 Railway 서버 주소(`https://web-production-10fe2.up.railway.app/mcp`)로
남아 있는데, **Railway는 크레딧 소진으로 더 이상 운영되지 않습니다** (2-A 참고). 현재 운영
중인 서버를 점검하려면 반드시 `--url`로 실제 주소를 지정하세요.

```bash
python test_mcp_client.py --url https://desktop-ika1349.tail81ecba.ts.net/mcp
python test_mcp_client.py --url http://127.0.0.1:8734/mcp
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

### `court_case_search` (법제처 판례, `server_ext.py`)

| 파라미터 | 설명 |
|---|---|
| `keyword` | 검색어 (필수) |
| `court` | `"대법원"` 또는 `"하위법원"` (빈값 = 전체) |
| `date_from` / `date_to` | 선고일자 범위 YYYYMMDD |
| `display` | 결과 수 (기본 10) |
| `page` | 페이지 번호 |

### `court_case_detail` (`server_ext.py`)

| 파라미터 | 설명 |
|---|---|
| `case_serial` | `court_case_search` 결과의 `판례일련번호` |
| `max_chars` | 판례내용 최대 길이 (기본 8000) |

### `law_interpretation_search` (법령해석례, `server_ext.py`)

| 파라미터 | 설명 |
|---|---|
| `keyword` | 검색어 (`serial` 없이 호출 시 사용) |
| `display` | 결과 수 (기본 10) |
| `serial` | 해석례일련번호 — 지정하면 질의요지·회답·이유 전문 반환 |

### `law_history_search` (법령 연혁, `server_ext.py`)

| 파라미터 | 설명 |
|---|---|
| `law_name` | 법령명 (예: "부가가치세법") |
| `law_id` | 법령ID로 본법만 필터 (같은 이름의 시행령·시행규칙 혼입 방지, 예: 부가가치세법=001571) |
| `current_only` | `true`면 현행 법령 검색만 (법령ID·MST 확인용) |

### `law_article_as_of` (특정 시점 조문, `server_ext.py`)

| 파라미터 | 설명 |
|---|---|
| `law_name` | 법령명 (예: "소득세법 시행령") |
| `as_of_date` | 기준일 YYYYMMDD (예: 예규 회신일) |
| `article_no` | 조번호 — `"162"` 또는 가지조문 `"104의3"` 형식 (패딩 없음) |
| `law_id` | 법령ID 필터 (권장 — 본법/시행령 혼입 방지) |
| `max_chars` | 원문 최대 길이 (기본 6000). 조문이 이보다 길면 응답에 `"잘림"` 항목으로 전체 길이가 안내되며, 그 길이 이상으로 지정해 다시 호출하면 전문 반환 |

### `admin_rule_search` (행정규칙, `server_ext.py`)

| 파라미터 | 설명 |
|---|---|
| `keyword` | 검색어 (예: "법인세법 기본통칙", "조사사무처리규정", "외국환거래규정") |
| `serial` | 일련번호 — 지정하면 본문 반환 |
| `display` | 결과 수 (기본 10) |
| `article` | 조번호 — `"9-5"`(제9-5조), `"23"`, `"23의2"` 형식. 해당 조문만 잘라 반환. 대형 고시(외국환거래규정 등)는 사실상 필수 |
| `max_chars` | 본문 최대 길이 (기본 10000). 잘리면 응답에 `"잘림"` 안내 포함 |
| `start_char` | 본문 시작 오프셋 — 조번호를 모를 때 이어 읽기용 |

### `treaty_search` (조세조약, `server_ext.py`)

| 파라미터 | 설명 |
|---|---|
| `keyword` | 검색어 (예: "대한민국과 미합중국 간의 조세") |
| `serial` | 조약일련번호 — 지정하면 본문 반환 |
| `display` | 결과 수 (기본 10) |

### `ordinance_search` (자치법규, `server_ext.py`)

| 파라미터 | 설명 |
|---|---|
| `keyword` | 검색어 (예: "취득세 감면") |
| `region` | 지자체명 필터 (예: "서울", "용산구") |
| `serial` | 일련번호 — 지정하면 본문 반환 |
| `display` | 결과 수 (기본 20) |

## 알려진 제한 사항 (v5 기준)

- **법제처(law.go.kr) 도구 8개는 `server_ext.py`로 실행했을 때만 사용 가능**합니다.
  `server.py`만 단독 실행하면 기본 6개만 노출됩니다.
- **law.go.kr IP 화이트리스트**: 등록되지 않은 IP에서 호출하면 8개 도구 모두
  "인증 실패" 오류를 반환합니다. `open.law.go.kr` → OpenAPI 신청내역에서 서버의
  공인 IP를 먼저 등록해야 합니다.
- **law.go.kr XML 파싱**: 응답을 정규식 기반 경량 파서로 처리합니다. API 응답
  구조가 바뀌면(태그명 변경 등) 파싱이 깨질 수 있습니다.
- **`law_article_as_of`**: 연혁 시행본 중 기준일 이하 최대 시행일자 본을 자동
  선택하는 방식이라, 같은 이름의 법령이 여러 개(본법/시행령/시행규칙) 섞여 있으면
  `law_id`를 지정하지 않는 한 의도치 않은 시행본이 선택될 수 있습니다.

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
