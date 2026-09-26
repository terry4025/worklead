# Windows 설치 파일(.exe, NSIS) 빌드. 개발 PC 에서만 실행 (최종 사용자는 Python·Node 불필요).
# 요구: uv, Node.js 22+, Rust (MSVC), WebView2 (설치 프로그램이 부트스트랩)
# 사용: powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

Write-Host '[1/4] 백엔드 테스트'
Push-Location "$root\backend"
uv sync --frozen --group dev --group package
uv run pytest -q

Write-Host '[2/4] 백엔드 sidecar 패키징 (PyInstaller)'
uv run pyinstaller packaging\worklead-backend.spec --noconfirm --distpath build\dist --workpath build\work
Pop-Location

$triple = ((rustc -vV) | Select-String '^host:').ToString().Split(' ')[1].Trim()
$bin = "$root\desktop\src-tauri\binaries"
New-Item -ItemType Directory -Force $bin | Out-Null
Copy-Item "$root\backend\build\dist\worklead-backend.exe" "$bin\worklead-backend-$triple.exe" -Force

Write-Host '[3/4] 화면 테스트'
Push-Location "$root\desktop"
npm ci
npm run typecheck
npm test

Write-Host '[4/4] Tauri 번들 (NSIS)'
npm run tauri build
Pop-Location
Write-Host "완료: desktop\src-tauri\target\release\bundle\nsis\"
