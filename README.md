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

### 인증서 오류가 날 경우

일부 사내망/프록시 환경에서는 `taxlaw.nts.go.kr` 접속 시 SSL 인증서 검증이
실패할 수 있습니다. 이 경우에만 아래처럼 임시로 검증을 끄고 원인을 확인하세요.
(정식 운영 환경에서는 켜두는 것을 권장합니다.)

```bash
NTS_VERIFY_SSL=false python server.py
```

## 2. Railway 배포

1. 이 폴더를 새 GitHub 저장소로 올립니다.
2. Railway에서 "New Project" → "Deploy from GitHub repo" 선택.
3. Railway가 `Procfile`을 인식해서 `python server.py`로 자동 실행합니다.
   (`PORT` 환경변수는 Railway가 자동으로 주입합니다.)
4. 배포가 끝나면 Railway가 발급하는 도메인 뒤에 `/mcp`를 붙인 주소가
   실제 MCP 서버 URL이 됩니다.
   예) `https://nts-tax-mcp-production.up.railway.app/mcp`

필요하면 Settings에서 커스텀 도메인도 연결할 수 있습니다.

## 3. Claude에 커넥터로 등록

1. claude.ai 접속 → 프로필 → 설정(Settings) → 커넥터(Connectors)
2. "사용자 지정 커넥터 추가(Add custom connector)" 클릭
3. 이름: `국세법령정보시스템` (원하는 이름으로)
4. URL: 2번에서 확인한 `.../mcp` 주소 입력 후 저장
5. 새 대화창에서 도구(Tools) 목록에 뜨는지 확인

## 4. 사용 예시 (Claude 채팅에서)

- "국세법령정보시스템에서 조정대상지역 관련 질의회신이랑 심판례 찾아줘"
- "부당행위계산 부인 관련 최근 조세심판원 결정례 있는지 확인해줘"

Claude가 `nts_ruling_search` 도구를 자동으로 호출해서
질의회신/심판/심사/판례/법령 결과를 정리해서 보여줍니다.

## 도구 파라미터 참고

| 파라미터 | 설명 |
|---|---|
| `keyword` | 검색어 (필수) |
| `collections` | 검색 범위 제한. 생략시 전체.<br>`form`(별표서식), `statute`(법령), `ruling`(사전답변·서면질의·질의회신), `precedent`(심판·심사·판례), `old_ruling`(구 법령해석자료), `intl`(국제조세 해설), `hometax`(홈택스 상담사례) |
| `date_from` / `date_to` | 검색 기간 (YYYYMMDD) |
| `view_count` | 컬렉션별로 가져올 결과 개수 (기본 20) |

## 알려진 제한 사항 / 다음 개선 방향

- 세목별 필터 코드(`ntstTlawClCdList`)는 아직 매핑표를 확정하지 못해 비워둔 상태입니다.
  현재는 전체 검색 후 결과의 `tax_type` 필드로 걸러 쓰면 됩니다.
- 감사원 심사청구는 이 서버의 범위에 포함되지 않습니다(별도 관할 사이트).
- 검색 결과 안에 본문(`content`, `detail_content`)이 대부분 포함되지만,
  드물게 잘리는 문서가 있을 수 있어 별도 상세조회 API는 추후 조사가 필요합니다.
