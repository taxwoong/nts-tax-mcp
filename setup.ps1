# ============================================================
#  nts-tax-mcp 서버컴퓨터 설치 스크립트 (1회 실행) — v2
#  실행: 관리자 PowerShell에서
#    cd D:\Obsidian\nts-mcp-server
#    Set-ExecutionPolicy -Scope Process Bypass -Force
#    .\setup.ps1
# ============================================================
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 1. Python 확인 — Microsoft Store 가짜 별칭(WindowsApps) 제외, py 런처 우선
$pyCmd = $null
$c = Get-Command py -ErrorAction SilentlyContinue
if ($c -and $c.Source -notlike "*WindowsApps*") { $pyCmd = $c.Source }
if (-not $pyCmd) {
  $c = Get-Command python -ErrorAction SilentlyContinue
  if ($c -and $c.Source -notlike "*WindowsApps*") { $pyCmd = $c.Source }
}
if (-not $pyCmd) {
  Write-Host "진짜 Python이 설치되어 있지 않습니다 (지금 잡히는 건 Microsoft Store 바로가기)." -ForegroundColor Red
  Write-Host "다음 한 줄을 실행하고, 완전히 '새' 관리자 PowerShell 창에서 setup.ps1을 다시 실행하세요:" -ForegroundColor Yellow
  Write-Host "    winget install Python.Python.3.12" -ForegroundColor Cyan
  exit 1
}
Write-Host "Python: $pyCmd"

# 2. GitHub에서 소스 다운로드 (이미 받았어도 다시 받아 최신화)
Write-Host "소스 다운로드 중..."
Invoke-WebRequest -Uri "https://github.com/taxwoong/nts-tax-mcp/archive/refs/heads/main.zip" -OutFile "src.zip" -UseBasicParsing
Expand-Archive -Path "src.zip" -DestinationPath "_src" -Force
Copy-Item "_src\nts-tax-mcp-main\*" -Destination . -Recurse -Force
Remove-Item "src.zip" -Force
Remove-Item "_src" -Recurse -Force
Write-Host "소스 다운로드 완료"

# 3. requirements 핀 — mcp 2.0.0에서 mcp.server.fastmcp 제거되어 서버가 안 뜸 (2026-08-08 확인)
@"
mcp[cli]>=1.2.0,<2.0.0
requests>=2.31.0
beautifulsoup4>=4.12.0
"@ | Set-Content -Encoding UTF8 requirements.txt
Write-Host "requirements.txt 핀 적용 (mcp<2.0.0)"

# 4. 가상환경 + 의존성 (절대경로 사용)
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
  & $pyCmd -m venv (Join-Path $PSScriptRoot ".venv")
}
if (-not (Test-Path $venvPy)) {
  Write-Host "가상환경 생성 실패 — Python 설치 상태를 확인하세요 ($pyCmd)" -ForegroundColor Red
  exit 1
}
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -r (Join-Path $PSScriptRoot "requirements.txt")
Write-Host "의존성 설치 완료"

# 5. 부팅 시 자동 실행 등록 + 즉시 시작
$bat = Join-Path $PSScriptRoot "run_server.bat"
schtasks /Create /TN "nts-tax-mcp" /TR "`"$bat`"" /SC ONSTART /RU SYSTEM /RL HIGHEST /F | Out-Null
schtasks /Run /TN "nts-tax-mcp" | Out-Null
Write-Host "작업 스케줄러 등록 + 서버 시작"

# 6. 기동 확인 (GET은 405/406이 정상 — 무응답이면 실패)
Start-Sleep -Seconds 8
try {
  $r = Invoke-WebRequest -Uri "http://127.0.0.1:8734/mcp" -Method GET -UseBasicParsing -ErrorAction Stop
  Write-Host "서버 응답: $($r.StatusCode)" -ForegroundColor Green
} catch {
  $code = 0; try { $code = [int]$_.Exception.Response.StatusCode } catch {}
  if ($code -ge 400) { Write-Host "서버 기동 확인 (HTTP $code — 정상)" -ForegroundColor Green }
  else {
    Write-Host "서버 응답 없음 — 로그 확인:" -ForegroundColor Red
    if (Test-Path "$PSScriptRoot\server.log") { Get-Content "$PSScriptRoot\server.log" -Tail 5 }
  }
}
Write-Host ""
Write-Host "다음: 브라우저에서 https://desktop-ika1349.tail81ecba.ts.net/mcp 열어 영문 오류문구(=성공) 확인" -ForegroundColor Cyan
