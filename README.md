# nts-tax-mcp

**한국 세법·법령 통합 검색 MCP 서버.** 국세청 국세법령정보시스템(taxlaw.nts.go.kr),
지방세법령정보시스템(olta.re.kr), 법제처 국가법령정보센터(law.go.kr)를 하나의 커넥터로
묶어 **도구 18개**로 검색합니다. 심판례·판례·질의회신은 결정문 **전문**(붙임 HWP)까지,
법령은 조문·부칙·조문 개정 diff까지 Claude 채팅에서 바로 조회하고, 인용한 문서번호의
실존 여부까지 검증합니다.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![MCP](https://img.shields.io/badge/MCP-streamable--http-black)
![tools](https://img.shields.io/badge/tools-18-brightgreen)
![status](https://img.shields.io/badge/version-v5.6-informational)
![license](https://img.shields.io/badge/license-MIT-green)

```bash
git clone https://github.com/taxwoong/nts-tax-mcp.git
cd nts-tax-mcp
pip install -r requirements.txt
python server_ext.py          # http://0.0.0.0:8000/mcp (포트는 PORT 환경변수)
```

그다음 claude.ai → 설정 → 커넥터 → **사용자 지정 커넥터 추가**에 위 `/mcp` 주소를
넣으면 끝입니다 ([자세히](#claude에-커넥터로-등록)).

> **셀프호스팅 전용입니다.** 공개된 공용 서버는 운영하지 않습니다(무인증 엔드포인트라
> 주소를 공개하지 않습니다). 직접 띄워서 쓰세요.

## 무엇을 검색할 수 있나

| 영역 | 대상 | 키 필요 |
|---|---|---|
| **국세** | 사전답변 · 서면질의 · 질의회신(국세청/기재부/법제처) · 조세심판원 심판청구 · 국세청 심사청구 · 법원 판례 | 불필요 |
| **지방세** | 취득세 · 재산세 · 자동차세 · 지방소득세 · 등록면허세 — 조세심판원 결정례 · 감사원 심사결정례 · 헌재 결정례 · 법원판례 · 법제처/행안부 유권해석 | 불필요 |
| **법령정보** | 대법원·하급심 판례, 법령 연혁 · 특정 시점 조문 · 조문 개정 diff · 부칙(시행일·적용례), 법령해석례, 행정규칙(기본통칙 등), 조세조약, 자치법규(조례) | `LAW_API_OC` |

- **키 없이** `python server.py` → 국세·지방세 도구 **8개**가 바로 동작합니다.
- **법령정보 10개**를 더 쓰려면 `server_ext.py`로 실행하고 `LAW_API_OC`를 설정하세요.
  [open.law.go.kr](https://open.law.go.kr)에서 무료로 발급받는 기관코드이며, **등록한
  IP에서만** 동작하므로 서버 공인 IP를 사전 등록해야 합니다. 미설정 시 법령정보 도구만
  `AUTH_ERROR`를 반환하고 나머지 8개는 정상 동작합니다.

## 제공 도구 (18개)

실제 운영 서버는 `server_ext.py`로 구동되어 18개가 모두 열려 있습니다.
모든 응답에는 `status` 필드가 포함됩니다 — `OK` / `NOT_FOUND`(결과 없음, **부존재 판단 가능**)
/ `UPSTREAM_ERROR`·`PARSE_ERROR`(원천 접근·해석 실패, **부존재 단정 금지**) / `AUTH_ERROR` /
`INVALID_INPUT`. 파라미터 상세는 [도구 파라미터 참고](#도구-파라미터-참고)에 있습니다.

### 기본 8개 (국세·지방세, `server.py`)

| 도구 | 용도 |
|---|---|
| `nts_ruling_search` | 국세 통합검색 (세목명이 정확하면 서버측 세목필터 자동 적용) |
| `nts_ruling_get_by_doc_no` | 국세 문서 사건번호로 직접 조회 (요지·스니펫) |
| `nts_ruling_get_full_text` | **결정문·판결문 전문 조회** — 붙임 HWP를 받아 본문 텍스트로 반환 (v5.5) |
| `olta_ruling_search` | 지방세 통합검색 (전체 카테고리 미리보기, 카테고리당 3건) |
| `olta_collection_search` | 지방세 특정 카테고리 깊은 탐색 — 페이지네이션·기간·최신순 정렬 (서버측) |
| `olta_get_detail` | 지방세 문서 본문 전문 조회 (조세심판원·헌재 지원) |
| `nts_and_olta_precedent_search` | 국세+지방세 조세심판원 계열을 한 번에, 중복 제거해서 검색 |
| `verify_citations` | 인용된 문서번호(국세·지방세) 실존 여부 일괄 검증 — 인용 환각 방지 (v5.4) |

### 확장 10개 (법제처 law.go.kr, `server_ext.py`에서만 추가)

| 도구 | 용도 |
|---|---|
| `court_case_search` | 법제처 판례 검색 (대법원·하급심, 국세청 시스템 판례와 별도 DB) |
| `court_case_detail` | 판례 본문 전문 조회 (판시사항·판결요지·참조조문·판례내용) |
| `law_interpretation_search` | 법령해석례 검색/본문 조회 |
| `law_history_search` | 법령 연혁(전체 시행본 목록: 시행일자·공포번호·MST) 조회 |
| `law_article_as_of` | 특정 날짜 시행 중이던 법령 조문 원문 (예규·판례 인용 당시 조문 확인용) + 조문별 시행일자 |
| `law_article_diff` | 조문 개정 diff(신구조문 대비) + 현행 문안 시작 시점 특정 + 개정 부칙 연결 (v5.4) |
| `law_addenda_search` | 법령 부칙(시행일·적용례·경과조치) — 목록/특정 개정 전문/특정 조문 적용시기 발췌 |
| `admin_rule_search` | 행정규칙(훈령·예규·고시 — 기본통칙·조사사무처리규정 등) 검색/본문 조회 |
| `treaty_search` | 조약(조세조약) 검색/본문 조회 — 원문·발효일 확인 |
| `ordinance_search` | 자치법규(조례·규칙 — 지방세 탄력세율·감면조례 등) 검색/본문 조회, 지자체 필터 |

## 사용 예시

Claude 채팅에서 자연어로 물어보면 됩니다.

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
- "방금 인용한 심판례 번호들 실제 있는 건지 확인해줘" (→ `verify_citations`)
- "소득세법 104조, 2020년이랑 지금이랑 뭐가 바뀌었어? 언제부터 적용돼?" (→ `law_article_diff`)

## 설치와 실행

```bash
pip install -r requirements.txt

python server.py         # 기본 8개 (국세·지방세·인용검증) — 키 불필요
python server_ext.py     # 18개 (기본 8개 + 법제처 10개) — LAW_API_OC 필요
```

기본적으로 `http://0.0.0.0:8000/mcp` 에서 streamable-http 방식으로 서비스됩니다.
포트는 환경변수 `PORT`로 바꿉니다.

```bash
PORT=8765 python server_ext.py
```

`server_ext.py`는 `from server import mcp`로 기본 8개를 그대로 물려받고 법제처 10개를
추가 등록하는 구조라, `server.py` 자체는 수정되지 않습니다. 기본 8개만 필요하면
`server.py`를 그대로 실행하면 됩니다.

### 환경변수 옵션

| 변수 | 기본값 | 설명 |
|---|---|---|
| `PORT` | 8000 | 서버 포트 |
| `NTS_VERIFY_SSL` | true | SSL 인증서 검증 여부. 사내망/프록시에서 인증서 오류 시에만 `false`로 임시 우회 |
| `OLTA_VERIFY_SSL` | true | 위와 같음 (지방세 olta.re.kr용) |
| `NTS_CACHE_TTL` | 300 | 동일 검색 결과 캐시 유지 시간(초) |
| `NTS_MIN_REQUEST_INTERVAL` | 0.5 | 국세청 서버로 보내는 요청 사이 최소 간격(초) |
| `NTS_RESPONSE_CHAR_CAP` | 30000 | 검색 응답 총량 상한(자). 넘으면 항목이 많은 컬렉션부터 덜어내고 `_잘림` 안내를 붙임 (v5.5) |
| `NTS_AUTH_MODE` | 자동 | 접근 토큰 게이트 모드 `off`/`warn`/`enforce` — [접근 제한](#접근-제한-사무실-인원만-쓰게-하기) 참고 |
| `NTS_PUBLIC_BASE` | 없음 | 외부 공개 주소(예: `https://<서버주소>`). 설정하면 `auth_gate.py add`가 완성된 URL을 출력 |
| `LOG_LEVEL` | INFO | 로깅 레벨 (DEBUG로 두면 세션 재접속/캐시 히트 등이 상세히 찍힘) |
| `LAW_API_OC` | 없음 (필수) | `server_ext.py` 전용. law.go.kr 가입 시 발급받는 기관코드 — 미설정시 법제처 10개 도구가 `AUTH_ERROR`를 반환. 이 코드로 등록된 IP에서만 동작 (`open.law.go.kr` → OpenAPI 신청내역에서 서버 공인 IP 사전 등록 필요). 개인 식별정보이므로 소스에 직접 적지 말고 배포 환경에서 주입할 것 |

## 상시 운영

### 서버컴퓨터 상시 구동 + Tailscale Funnel (현재 운영 방식, 2026-08~)

Railway 크레딧 소진으로 서버가 다운된 뒤(2026-08-08), 자체 서버컴퓨터에서 상시 구동하는
방식으로 전환했습니다. 18개 도구(`server_ext.py`)가 이 방식으로 운영됩니다.

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
   없으면 법제처 10개 도구만 동작하지 않고 기본 8개는 정상입니다.
3. [Tailscale](https://tailscale.com)을 설치해 로그인 후 Funnel로 외부에 고정 주소로 노출합니다.
   ```powershell
   tailscale funnel --bg 8734
   ```
4. 실제 MCP 서버 URL(고정): `https://<머신명>.<테일넷>.ts.net/mcp` —
   서버컴퓨터에서 `tailscale funnel status`로 확인 (보안상 실제 주소는 저장소에 기재하지 않음)

포트를 바꾸면 `run_server.bat`의 `PORT`와 `tailscale funnel`의 대상 포트를 함께 바꿔야 합니다.
공유기에서 이 포트를 직접 포워딩하지 말고 Tailscale Funnel만 사용하세요.

### 접근 제한 (사무실 인원만 쓰게 하기)

Funnel 주소는 인증이 없으면 주소를 아는 누구나 호출할 수 있습니다. 그 호출은 **서버 운영자의
법제처 인증키(OC)와 IP로** 원천 사이트에 나가고, 원천 요청 간격 제한(`NTS_MIN_REQUEST_INTERVAL`)이
프로세스 전역 공유라서 외부 호출이 늘면 내 조회까지 느려집니다.

`auth_gate.py`가 **사람마다 다른 URL**을 발급해 이를 막습니다. claude.ai 커넥터는 요청이
사용자 PC가 아니라 Anthropic 서버에서 나오기 때문에 IP 허용목록·Tailscale ACL로는 구분할 수
없고, 커스텀 헤더를 못 넣는 클라이언트도 있어 **URL 경로에 토큰을 넣는 방식**을 씁니다.

```bash
python auth_gate.py add 홍길동      # 발급 + 그 사람 전용 URL 출력
python auth_gate.py list            # 발급 현황 (토큰 뒷자리만)
python auth_gate.py revoke 홍길동   # 그 사람 URL만 무효화 (서버 재시작 후 적용)
```

발급된 주소는 `https://<서버주소>/t/<토큰>/mcp` 형태이고, 커넥터 URL에 이 주소를 그대로
넣으면 됩니다. `Authorization: Bearer <토큰>` 헤더나 `?k=<토큰>` 쿼리도 같이 받습니다.
토큰은 `local_tokens.json`(`.gitignore` 처리)에 저장되며 저장소에 올라가지 않습니다.

모드는 환경변수 `NTS_AUTH_MODE`로 정합니다.

| 값 | 동작 |
|---|---|
| `off` | 게이트 없음 (종전과 동일). **토큰을 아직 발급하지 않았으면 자동으로 이 값** |
| `warn` | 차단하지 않고 토큰 없는 요청만 `server.log`에 기록 — 전환 기간용 |
| `enforce` | 토큰 없는 요청은 401. **토큰 파일이 있으면 기본값** |

전환은 `warn`으로 며칠 돌려 누가 쓰는지 로그로 확인한 뒤 `enforce`로 넘기는 순서를 권합니다.
`local_env.bat`에 `set NTS_AUTH_MODE=warn`, `set NTS_PUBLIC_BASE=https://<서버주소>` 두 줄을
넣어두면 발급 명령이 완성된 URL을 바로 찍어줍니다. 토큰 없이 서버 생존만 확인하려면
`GET /healthz`를 쓰세요.

### Railway 배포 (레거시)

`Procfile`은 여전히 `python server.py`를 실행하므로, Railway로 배포하면 **기본 8개
도구만** 뜨고 법제처 10개 도구(`server_ext.py`)는 포함되지 않습니다. 크레딧이 소진되면
서버가 그대로 죽으므로 현재는 권장하지 않지만, 여전히 동작은 합니다.

1. 이 폴더를 GitHub 저장소로 올립니다.
2. Railway에서 "New Project" → "Deploy from GitHub repo" 선택.
3. Railway가 `Procfile`을 인식해서 `python server.py`로 자동 실행합니다.
   (`PORT` 환경변수는 Railway가 자동으로 주입합니다.)
4. 배포가 끝나면 Railway가 발급하는 도메인 뒤에 `/mcp`를 붙인 주소가
   실제 MCP 서버 URL이 됩니다.

## Claude에 커넥터로 등록

1. claude.ai 접속 → 프로필 → 설정(Settings) → 커넥터(Connectors)
2. "사용자 지정 커넥터 추가(Add custom connector)" 클릭
3. 이름: 원하는 이름으로 (현재 운영 커넥터명: `Korea nts`)
4. URL: 서버의 `.../mcp` 주소 입력 후 저장
   (운영 주소는 서버컴퓨터에서 `tailscale funnel status`로 확인)
5. 도구 권한을 **"항상 허용"**으로 설정 (기본값 "승인 필요"는 매번 승인을 물어봄)
6. 새 대화창에서 도구 목록에 뜨는지 확인
   (커넥터를 새로 켜거나 서버에 도구를 추가한 직후에는 목록이 늦게 반영됩니다. 기존
   대화창에서도 몇 분 지나면 자동으로 갱신되니 재등록할 필요는 없습니다)

## 문제가 생겼을 때

### 먼저 서버 자체를 점검

Claude 채팅에서 도구가 안 잡히는 문제가 생겼을 때, **서버 자체 문제인지 Claude 쪽 문제인지**를
빠르게 구분하기 위한 스크립트입니다. Claude를 거치지 않고 서버에 직접 MCP 프로토콜로 요청을
보내서 initialize → tools/list → tools/call까지 전체 흐름을 검증합니다.

```bash
python test_mcp_client.py
```

스크립트 기본값은 예전 Railway 서버 주소(`https://web-production-10fe2.up.railway.app/mcp`)로
남아 있는데, **Railway는 크레딧 소진으로 더 이상 운영되지 않습니다**. 현재 운영
중인 서버를 점검하려면 반드시 `--url`로 실제 주소를 지정하세요.

```bash
python test_mcp_client.py --url https://<서버-tailnet-주소>/mcp
python test_mcp_client.py --url http://127.0.0.1:8734/mcp
```

**이 스크립트가 전부 성공하는데 Claude 채팅에서는 도구가 안 보인다면**, 원인은 서버가 아니라
Claude 쪽 커넥터 인식/캐싱 문제입니다. 이 경우 아래를 시도해 보세요.

- 몇 분 기다렸다 다시 확인 (도구 목록은 캐시되어 늦게 반영되지만, 같은 대화창에서도 자동 갱신됨 — 2026-08 실측)
- 새 대화창에서 다시 확인
- 그래도 안 되면 설정 → 커넥터에서 해당 커넥터를 삭제 후 재등록
- 그래도 안 되면 `support.claude.com`에 문의 (Claude 플랫폼 쪽 반영 지연/버그일 가능성)

### 그래도 안 되면 이슈로 알려주세요

버그·질문·기능 제안 모두 [Issues](https://github.com/taxwoong/nts-tax-mcp/issues)로
받습니다. 파싱이 깨졌거나(원천 사이트 개편) 응답이 이상한 경우, **호출한 도구 이름 ·
넘긴 파라미터 · 받은 `status` 값**을 함께 적어주시면 재현이 빠릅니다.

스크래핑·Open API 기반이라 원천 사이트가 개편되면 조용히 깨질 수 있습니다.
`tests/compare_with_site.py`가 그 드리프트를 감지하지만, 실제 사용 중 발견한 이상 동작이
가장 빠른 신호입니다.

## MCP 커넥터 우회 독립 클라이언트 (`client/`)

Claude 커넥터 연결이 불안정할 때, MCP를 거치지 않고 서버에 직접 접속해서 검색할 수
있는 독립 클라이언트를 `client/` 폴더에 추가했습니다. 사용법은 `client/README.md` 참고.

```bash
cd client
python nts_search.py --ping
python nts_search.py "조정대상지역" -c precedent -n 10
```

## 파일 구성

```
nts-tax-mcp/
├── server.py                    # MCP 서버 본체 (FastMCP) — 기본 도구 8개 (국세+지방세+인용검증)
├── server_ext.py                # 확장 진입점 — server.py 8개 + 법제처 10개 = 18개 도구
├── nts_tax_ruling_search.py     # 국세: taxlaw.nts.go.kr 검색 클라이언트
├── olta_tax_ruling_search.py    # 지방세: olta.re.kr 검색 클라이언트
├── law_go_kr.py                 # 법령정보: law.go.kr Open API 클라이언트 (판례/법령/해석례/행정규칙/조약/자치법규)
├── hwp_text.py                  # 붙임 HWP 5.0 → 본문 텍스트 변환 (전문 조회용, v5.5)
├── auth_gate.py                 # 사람별 URL 토큰 게이트 + 발급·조회·폐기 CLI
├── test_mcp_client.py           # 서버 상태 독립 점검 스크립트
├── tests/                       # 파서 회귀 테스트 (v5.4)
│   ├── refresh_fixtures.py      #   실제 응답을 픽스처로 캡처 (OC 키 자동 마스킹)
│   ├── test_parsers.py          #   오프라인 파싱 검증 (네트워크 불필요, pytest 호환)
│   ├── compare_with_site.py     #   라이브 드리프트 감지 (사이트 개편 조기 경보)
│   └── fixtures/                #   캡처된 응답 (*.gz) + manifest.json
├── client/                      # MCP 커넥터 우회 독립 클라이언트 (CLI 포함)
│   ├── nts_client.py
│   ├── nts_search.py
│   └── README.md
├── requirements.txt
├── LICENSE                      # MIT
├── CHANGELOG.md                 # 버전별 변경 이력 (v2 ~ v5.6)
├── DATA_SOURCES.md              # 자료원 사양·코드표 (세목/카테고리 코드 등)
├── Procfile                     # Railway 배포용 (레거시 — 현재 운영은 서버컴퓨터+Tailscale Funnel)
├── setup.ps1                    # 서버컴퓨터 최초 설치 스크립트 (소스 다운로드→의존성→작업 스케줄러 등록)
├── run_server.bat               # 확장판(server_ext.py) 상시 구동용 — 작업 스케줄러가 부팅 시 실행
├── local_env.bat                # (커밋 안 됨) LAW_API_OC 등 개인 식별정보 — .gitignore 처리, 서버컴퓨터에서 직접 생성
└── local_tokens.json            # (커밋 안 됨) auth_gate.py가 발급한 접근 토큰
```

## 도구 파라미터 참고

### `nts_ruling_search`

| 파라미터 | 설명 |
|---|---|
| `keyword` | 검색어 (필수) |
| `collections` | 검색 범위 제한. 생략시 전체.<br>`form`(별표서식), `statute`(법령), `ruling`(사전답변·서면질의·질의회신), `precedent`(심판·심사·판례), `old_ruling`(구 법령해석자료), `intl`(국제조세 해설), `hometax`(홈택스 상담사례) |
| `page` | 페이지 번호 (1부터 시작) |
| `view_count` | 컬렉션별로 가져올 결과 개수 (**기본 5**, v5.5에서 20→5). 컬렉션 7개에 곱해지므로 20이면 최대 140건 |
| `date_from` / `date_to` | 검색 기간 (YYYYMMDD) |
| `sort` | `relevance`(정확도순, 기본) / `date_desc`(최신순) / `date_asc`(오래된순) |
| `tax_type_filter` | 세목명에 이 문자열이 포함된 것만 남김 (예: "양도소득세") |
| `include_full_text` | **기본 `false`** (v5.5에서 뒤집음). `true`로 줘도 얻는 건 전문이 아니라 검색어 주변 500~900자 스니펫이다 — 전문은 `nts_ruling_get_full_text`로 조회 |

### `nts_ruling_get_by_doc_no`

| 파라미터 | 설명 |
|---|---|
| `doc_no` | 사건번호/문서번호. 예: `조심-2023-서-9465`, `서면-2019-법규재산-4276`, `기획재정부 재산세제과-73` |

여기서 나오는 `content`/`detail_content`는 전문이 아니라 검색 스니펫입니다
(심판·심사의 `content`는 `"결정내용은 붙임과 같습니다."` 상수).
전문은 아래 `nts_ruling_get_full_text`로 조회하세요.

### `nts_ruling_get_full_text` (전문 조회, v5.5)

| 파라미터 | 설명 |
|---|---|
| `doc_no` | 사건번호/문서번호. 예: `조심-2026-서-1112`, `사전-2026-법규법인-0502` |
| `max_chars` | 본문 최대 길이 (기본 30000). 전문은 보통 2천~2만 자 |
| `start_char` | 본문 시작 오프셋 — 잘린 뒷부분을 이어 읽을 때. 응답의 `잘림` 안내가 다음 값을 알려줌 |

검색 API는 본문을 주지 않습니다 — 심판·심사는 `"결정내용은 붙임과 같습니다."`만 나오고,
`include_full_text=true`로 얻는 `detail_content`도 검색어 주변 500~900자 스니펫입니다.
**결정 이유·처분개요·청구주장·사실관계는 전부 붙임 HWP 안에** 있습니다.
이 도구가 붙임을 받아 텍스트로 변환해 돌려줍니다.

| 문서 종류 | 읽을 수 있는 것 |
|---|---|
| 심판·심사 | 주문 / 처분개요 / 청구주장 / 처분청 의견 / 심리 및 판단(쟁점·관련법령·판단) |
| 판례 | 사건·당사자 / 청구취지 / 이유 / 판단 |
| 사전답변·질의회신 | 1. 사실관계 / 2. 질의내용 / 3. 회신 |

붙임은 HWP 5.0 형식이며 `hwp_text.py`(의존성은 `olefile` 하나)가 파싱합니다.
**2행·2열 이상인 표는 Markdown 표로 복원**됩니다(v5.6) — 병합된 열 제목은 펼쳐서
"세액 당초 | 세액 경정"처럼 한 줄 머리행으로 합칩니다. 본문 전체를 감싼 1칸짜리 틀과
서식처럼 칸을 배치한 표는 표가 아니라 글이므로 문단으로 둡니다.

| 상황 | 응답 |
|---|---|
| 붙임이 HWP가 아님(PDF 등) | `status=PARSE_ERROR` + 형식 안내 — **자료 부존재가 아닙니다** |
| 붙임이 글자 없는 빈 HWP (주로 오래된 질의회신·일부 기재부 회신) | 검색 결과 본문을 `전문`으로 대신 주고 `비고`로 알림. 그것도 없으면 `NOT_FOUND` |

### `verify_citations` (인용 검증, v5.4)

| 파라미터 | 설명 |
|---|---|
| `doc_nos` | 검증할 문서번호 목록 (호출당 최대 10건). 예: `["조심-2023-서-9465", "조심2026지0284"]` |
| `search_local_tax` | `false`면 지방세 시스템 검색 생략 (국세 문서만 검증할 때 왕복 절약). 지방세 표기(조심YYYY지…, 감심…, 헌재 사건번호)는 이 값과 무관하게 지방세 시스템 확인 |

반환: 문서번호별 판정 — `확인`(실존, 제목·날짜·세목 메타 포함) / `미확인`(원천에서 못 찾음,
`유사문서_후보` 포함 가능) / `판단불가`(원천 장애 — 부존재로 단정 금지). 축약 표기
(예: "재산세제과-73")는 부분일치로 잡되 6자 미만 입력은 오매치 방지를 위해 제외됩니다.

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
| `view_count` | 각 소스에서 가져올 결과 개수 (**기본 5**, v5.5에서 20→5) |
| `tax_type_filter` | 세목 필터 |

반환값에 `nts_precedent`, `olta_precedent`, `duplicates_removed`(국세 결과와 겹쳐
제외된 지방세 항목 수 — `view_count`로 자르기 전 기준, v5.5에서 정확해짐)가 포함됩니다.

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

응답에 `조문시행일자`(그 조문의 개별 시행일)와 `적용시행본`(선택된 시행본의
시행일자·공포번호 등)이 포함됩니다. 적용례·경과조치까지 필요하면 `law_addenda_search`를 사용하세요.

### `law_article_diff` (조문 개정 diff, v5.4, `server_ext.py`)

| 파라미터 | 설명 |
|---|---|
| `law_name` | 법령명 (예: "소득세법", "법인세법 시행령") |
| `article_no` | 조번호 — `"104"` 또는 가지조문 `"104의3"` 형식 |
| `date_from` | 비교 기준 A (YYYYMMDD, 예: 예규 회신일) |
| `date_to` | 비교 기준 B (YYYYMMDD, 생략 시 오늘 = 현행) |
| `law_id` | 법령ID 필터 (본법/시행령 혼입 방지, 권장) |
| `max_chars` | diff 최대 길이 (기본 8000). 잘리면 `"잘림"` 안내 포함 |
| `find_change` | `false`면 변경 시점 특정·부칙 연결 생략 (빠른 비교만) |

반환: `변경여부`·`변경유형`(개정/신설/삭제), `diff`(unified diff — `-`줄은 A에만,
`+`줄은 B에만. 〈개정 …〉 주석 보존), `현행_문안_시작`(B 문안이 처음 수록된 시행본 —
조문별 단계 시행이면 `이_조문의_시행일자`가 실제 적용 시작일), `개정_부칙`(그 개정의
시행일 요약 + 이 조문이 언급된 적용례·경과조치 발췌). A~B 사이 개정이 여러 번이면
마지막 변경만 특정되며, 응답의 안내에 따라 `date_to`를 좁혀 재호출하면 이전 변경도
찾을 수 있습니다.

### `law_addenda_search` (법령 부칙 — 시행일·적용례·경과조치, `server_ext.py`)

| 파라미터 | 설명 |
|---|---|
| `law_name` | 법령명 (예: "소득세법", "법인세법 시행령") — `mst` 미지정 시 필수. 동명 하위법령이 섞이면 정확 일치 우선 |
| `mst` | 법령일련번호 직접 지정 (`law_history_search`·`law_article_as_of` 결과의 MST) |
| `law_id` | 법령ID 필터 (본법/시행령 혼입 방지) |
| `as_of_date` | YYYYMMDD — 이 날짜 당시 시행본의 부칙 조회 (미지정 시 현행본) |
| `promul_no` | 공포번호 (예: "21221") — 그 개정 부칙의 **전문** 반환 |
| `article_no` | 조번호 (예: `"96"`, `"104의3"`) — 그 조문이 언급된 부칙 항(적용례·경과조치)만 최신순 발췌. `"96"`이 `"96의2"`에 오매치되지 않도록 가지조문을 구분 |
| `recent` | 목록 모드 반환 건수 (기본 10, 최신순) |
| `max_chars` | 본문 최대 길이 (기본 8000). 잘리면 응답에 `"잘림"` 안내 포함 |

`promul_no`·`article_no`를 모두 생략하면 목록 모드: 부칙 최신순 `recent`건의
공포일자·공포번호·시행일 요약("다만, 다음 각 호…" 단서의 호별 시행일 포함)을 반환합니다.
조문별 시행일이 다른 개정이면 `조문별_상이한_시행일` 항목이 함께 옵니다.

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

## 알려진 제한 사항

- **법제처(law.go.kr) 도구 10개는 `server_ext.py`로 실행했을 때만 사용 가능**합니다.
  `server.py`만 단독 실행하면 기본 8개만 노출됩니다.
- **law.go.kr IP 화이트리스트**: 등록되지 않은 IP에서 호출하면 법제처 도구 전체가
  "인증 실패"(`status: AUTH_ERROR`)를 반환합니다. `open.law.go.kr` → OpenAPI
  신청내역에서 서버의 공인 IP를 먼저 등록해야 합니다.
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

## 변경 이력

버전별 상세 변경 내용은 [CHANGELOG.md](CHANGELOG.md)를 참고하세요.
최신은 **v5.6** (2026-09-30) — 붙임 표를 Markdown 표로 복원 · 빈 붙임 처리 · 접근 토큰 게이트.

세부 데이터 사양·코드표(세목 코드, 카테고리 코드 등)는 [DATA_SOURCES.md](DATA_SOURCES.md)에 있습니다.

## 라이선스

[MIT](LICENSE). 이 저장소의 코드에 한합니다 — 검색 대상인 국세청·행정안전부·법제처의
법령·판례·심판례 원문은 각 기관의 이용 조건을 따릅니다. 또한 이 서버는 공개 사이트를
정중한 간격(`NTS_MIN_REQUEST_INTERVAL`, 기본 0.5초)과 캐싱으로 조회하도록 만들어져
있습니다. 간격을 줄이거나 대량 수집 용도로 쓰지 마세요.
