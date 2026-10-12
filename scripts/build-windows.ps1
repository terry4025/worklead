# Windows 설치 파일(.exe, NSIS) 빌드. 개발 PC 에서만 실행 (최종 사용자는 Python·Node 불필요).
# 요구: uv, Node.js 22+, Rust (MSVC), WebView2 (설치 프로그램이 부트스트랩)
# 사용: powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1
param([string]$NodeExecutable)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

if ($NodeExecutable) {
    if (-not (Test-Path -LiteralPath $NodeExecutable -PathType Leaf)) { throw 'Selected Node executable not found' }
    $npmCli = Join-Path (Split-Path -Parent (Get-Command npm.cmd).Source) 'node_modules\npm\bin\npm-cli.js'
    if (-not (Test-Path -LiteralPath $npmCli -PathType Leaf)) { throw 'npm CLI not found next to npm.cmd' }
    $env:PATH = "$(Split-Path -Parent $NodeExecutable);$env:PATH"
}

function Invoke-Npm([string[]]$NpmArguments) {
    if ($NodeExecutable) { & $NodeExecutable $npmCli @NpmArguments }
    else { & npm.cmd @NpmArguments }
}

function Invoke-NativeStep([string]$Step, [scriptblock]$Action) {
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "$Step failed (exit $LASTEXITCODE); build stopped" }
}

Write-Host '[1/4] 백엔드 테스트'
Push-Location "$root\backend"
try {
    Invoke-NativeStep 'Dependency sync' { uv sync --frozen --group dev --group package }
    Invoke-NativeStep 'Backend tests' { uv run pytest -q }

    Write-Host '[2/4] 백엔드 sidecar 패키징 (PyInstaller)'
    Invoke-NativeStep 'Backend packaging' { uv run pyinstaller packaging\worklead-backend.spec --noconfirm --distpath build\dist --workpath build\work }
} finally { Pop-Location }

$compiler = Invoke-NativeStep 'Rust toolchain' { rustc -vV }
$hostLine = $compiler | Select-String '^host:'
if (-not $hostLine) { throw 'Rust host target missing; build stopped' }
$triple = $hostLine.ToString().Split(' ')[1].Trim()
$bin = "$root\desktop\src-tauri\binaries"
New-Item -ItemType Directory -Force $bin | Out-Null
Copy-Item "$root\backend\build\dist\worklead-backend.exe" "$bin\worklead-backend-$triple.exe" -Force

Write-Host '[3/4] 화면 테스트'
Push-Location "$root\desktop"
try {
    Invoke-NativeStep 'UI dependencies' { Invoke-Npm -NpmArguments @('ci') }
    Invoke-NativeStep 'TypeScript' { Invoke-Npm -NpmArguments @('run', 'typecheck') }
    Invoke-NativeStep 'UI tests' { Invoke-Npm -NpmArguments @('test') }

    Write-Host '[4/4] Tauri 번들 (NSIS)'
    Invoke-NativeStep 'Tauri bundle' { Invoke-Npm -NpmArguments @('run', 'tauri', 'build') }
} finally { Pop-Location }
Write-Host "완료: desktop\src-tauri\target\release\bundle\nsis\"
