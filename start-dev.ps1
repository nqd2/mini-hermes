  param(
    [switch]$SkipSeaweedFS,
    [switch]$SkipPostgres
)

$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'
$backendEnv = Join-Path $repo 'packages\backend\.env'
$frontendEnv = Join-Path $repo 'packages\frontend\.env'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Python venv missing. Run: uv venv --python 3.12 .venv; uv pip install --python .venv\Scripts\python.exe -r packages\backend\requirements-mini-hermes.lock'
}
if (-not (Test-Path -LiteralPath $backendEnv)) {
    throw 'Backend env missing. Copy packages\backend\.env.example to packages\backend\.env and fill it.'
}
if (-not (Test-Path -LiteralPath $frontendEnv)) {
    throw 'Frontend env missing. Copy packages\frontend\.env.example to packages\frontend\.env.'
}
if (-not (Test-Path -LiteralPath (Join-Path $repo 'packages\frontend\node_modules\.bin\vite.cmd'))) {
    throw 'Vite dependencies missing. Run npm ci --force from packages\frontend.'
}

if (-not $SkipSeaweedFS) {
    & (Join-Path $repo 'scripts\dev-seaweedfs.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'SeaweedFS startup failed.' }
}

if (-not $SkipPostgres) {
    & (Join-Path $repo 'scripts\dev-postgres.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL startup failed.' }
}

Write-Host 'Starting sandbox, backend, connector, worker and frontend...'
& $python (Join-Path $repo 'scripts\dev-runner.py')
exit $LASTEXITCODE
