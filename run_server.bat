@echo off
rem nts-tax-mcp 확장판 실행 (기존 6개 + 법제처 8개 도구) — 작업 스케줄러가 부팅 시 자동 실행
rem LAW_API_OC(기관코드)는 개인 식별정보라 이 파일(공개 레포)에 직접 적지 않는다.
rem 서버컴퓨터에만 두는 local_env.bat(.gitignore 처리됨)에서 불러온다:
rem   @echo off
rem   set LAW_API_OC=본인_기관코드
cd /d %~dp0
set PORT=8734
if exist local_env.bat call local_env.bat
if "%LAW_API_OC%"=="" echo [경고] LAW_API_OC 미설정 — local_env.bat을 만들어 기관코드를 지정하세요. law.go.kr 도구 8개는 인증 실패로 동작하지 않습니다. >> server.log
.venv\Scripts\python.exe server_ext.py >> server.log 2>&1
