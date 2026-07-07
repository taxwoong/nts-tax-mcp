# client/ — MCP 커넥터 우회 독립 클라이언트

Claude MCP 커넥터 연결 상태와 무관하게, nts-tax-mcp 서버에 직접 HTTP로 접속해서
검색하는 재사용 가능한 클라이언트입니다.

**왜 필요한가** — Claude 채팅에서 커넥터로 연결하면 가끔 도구 인식/디스커버리 단계가
불안정할 때가 있습니다 (서버 자체는 멀쩡한데 Claude 쪽에서 도구 목록에 안 잡히는 경우).
이 클라이언트는 그 계층을 완전히 우회해서 MCP 프로토콜(JSON-RPC 2.0 + SSE)로 서버에
직접 요청을 보내므로, 서버가 살아있는 한 항상 동일하게 동작합니다.

## 구성

```
client/
├── nts_client.py   # 재사용 라이브러리 (nts_ruling_search, nts_ruling_get_by_doc_no, ping, list_tools)
└── nts_search.py   # 커맨드라인 도구
```

## 설치

`requests`만 있으면 됩니다 (저장소 루트의 `requirements.txt`에 이미 포함).

```bash
pip install requests
```

## CLI 사용법

```bash
# 서버 연결 상태만 점검
python nts_search.py --ping

# 등록된 도구 목록 확인
python nts_search.py --list-tools

# 기본 검색
python nts_search.py "홍콩 거주자" -c precedent -n 30

# 기간 + 정렬 + 세목 필터 + JSON 파일 저장
python nts_search.py "조정대상지역" -c ruling precedent --from 20230101 --to 20241231 --sort date_desc --tax-type 양도소득세 --json > result.json

# 사건번호로 직접 조회
python nts_search.py --doc-no "조심-2023-서-9465"

# 다른 서버 주소로 테스트 (로컬 개발 서버 등)
python nts_search.py --ping --endpoint http://127.0.0.1:8000/mcp
```

### 옵션 전체 목록

| 옵션 | 설명 |
|---|---|
| `keyword` | 검색어 (위치 인자) |
| `-c, --collections` | 검색 범위: `form statute ruling precedent old_ruling intl hometax` 중 선택 |
| `-n, --view-count` | 결과 개수 (기본 20) |
| `-p, --page` | 페이지 번호 |
| `--from`, `--to` | 검색 기간 YYYYMMDD (클라이언트단 필터 — 아래 참고) |
| `--sort` | `relevance`(기본) / `date_desc` / `date_asc` |
| `--tax-type` | 세목 필터 (예: 양도소득세) |
| `--no-full-text` | 본문 생략, 요약만 조회 |
| `--doc-no` | 사건번호 직접 조회 |
| `--json` | 원본 JSON 그대로 출력 (파이프/파일 저장용) |
| `--endpoint` | MCP 서버 URL (기본: 배포된 Railway 서버) |
| `--ping` | 연결 상태만 점검 |
| `--list-tools` | 도구 목록만 조회 |

### 알아두실 점 — 날짜 필터는 클라이언트단 처리입니다

taxlaw.nts.go.kr 통합검색 화면 자체에 기간 필터 UI가 없어서, 서버 API에 공식적으로
기간을 전달하는 파라미터를 찾지 못했습니다. 그래서 `--from`/`--to`는 서버에 보내는 게
아니라, **검색 결과를 받아온 뒤 각 항목의 날짜로 걸러내는 방식**입니다. `--sort date_desc`와
같이 쓰면 최신 문서부터 가져와 필터링하므로 더 안정적입니다.

## 라이브러리로 사용하기

```python
from nts_client import nts_ruling_search, nts_ruling_get_by_doc_no, ping

print(ping())

result = nts_ruling_search(
    "조정대상지역",
    collections=["precedent"],
    view_count=10,
    sort="date_desc",
    tax_type_filter="양도소득세",
)

detail = nts_ruling_get_by_doc_no("조심-2023-서-9465")
```

## 문제 진단에 활용하기

Claude 채팅에서 "이 도구가 안 잡혀요" 싶을 때:

```bash
python nts_search.py --ping
```

- **성공하면** → 서버는 정상. Claude 쪽 커넥터 인식/캐싱 문제이니 완전히 새 대화창에서
  다시 시도하거나 커넥터를 삭제 후 재등록해 보세요.
- **실패하면** → 서버 자체(Railway 배포, 코드) 문제이니 Railway 로그를 확인하세요.
