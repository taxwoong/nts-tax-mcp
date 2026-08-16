@echo off
rem nts-tax-mcp 확장판 실행 (기존 6개 + 법제처 5개 도구) — 작업 스케줄러가 부팅 시 자동 실행
cd /d %~dp0
set PORT=8734
set LAW_API_OC=taxwoong
.venv\Scripts\python.exe server_ext.py >> server.log 2>&1
