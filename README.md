# nts-tax-mcp

국세법령정보시스템(taxlaw.nts.go.kr) 통합검색을 Claude에서 바로 쓸 수 있게 해주는
MCP(Model Context Protocol) 서버입니다.

사전답변 · 서면질의 · 질의회신(국세청/기획재정부/법제처), 조세심판원 심판청구,
국세청 심사청구, 법원 판례, 법령을 키워드 하나로 통합검색합니다.

## 파일 구성

```
nts-tax-mcp/
├── server.py                   # MCP 서버 본체 (FastMCP)
├── nts_tax_ruling_search.py    # taxlaw.nts.go.kr 검색 API 클라이언트
├── requirements.txt
├── Procfile                    # Railway 배포용
└── .gitignore
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

## 알려진 제한 사항 / 다음 개선 방향

- 세목별 필터는 서버 세목코드(`ntstTlawClCdList`)가 아니라 클라이언트단 후처리(`tax_type_filter`)로 구현되어 있습니다. 정확한 코드 매핑표를 확보하면 서버단 필터로 전환할 수 있습니다.
- 감사원 심사청구는 이 서버의 범위에 포함되지 않습니다(별도 관할 사이트).
- `nts_ruling_get_by_doc_no`는 전용 상세조회 API가 확인되지 않아, 문서번호를 검색어로 활용하는 방식으로 구현되어 있습니다. 정확도는 높지만 전용 API를 찾으면 더 안정적으로 전환 가능합니다.
