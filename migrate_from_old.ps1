# migrate_from_old.ps1
# Run this from the NEW project folder (the one you extracted from the zip).
# It copies the things that are NOT in the zip (secrets, OSM data, git history)
# from your OLD project folder, then rebuilds the Python virtual environment.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File migrate_from_old.ps1 -OldPath "C:\path\to\old\nepal-map-agent"
#
# It only READS from the old folder. Nothing in the old folder is changed or deleted.

param(
    [Parameter(Mandatory = $true)][string]$OldPath,
    [switch]$SkipVenv
)

$ErrorActionPreference = "Stop"
$NewPath = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Test-Path $OldPath)) {
    throw "Old folder not found: $OldPath"
}
$OldPath = (Resolve-Path $OldPath).Path
if ($OldPath -eq (Resolve-Path $NewPath).Path) {
    throw "OldPath and the new folder are the same folder. Point -OldPath at your OLD copy."
}

Write-Host "Old folder: $OldPath"
Write-Host "New folder: $NewPath"
Write-Host ""

# Items copied from old to new (relative paths). All optional.
$items = @(
    ".env",
    "services\geo-data\.env",
    "data",
    ".git"
)

foreach ($rel in $items) {
    $src = Join-Path $OldPath $rel
    if (-not (Test-Path $src)) {
        Write-Host "  not found, skipped : $rel" -ForegroundColor DarkGray
        continue
    }
    $dstParent = Split-Path -Parent (Join-Path $NewPath $rel)
    if (-not (Test-Path $dstParent)) {
        New-Item -ItemType Directory -Path $dstParent -Force | Out-Null
    }
    Copy-Item -Path $src -Destination $dstParent -Recurse -Force
    Write-Host "  copied             : $rel" -ForegroundColor Green
}

# Virtual environments contain absolute paths, so they cannot be copied.
# Rebuild it in the same place the README uses.
if (-not $SkipVenv) {
    $svc = Join-Path $NewPath "services\geo-data"
    $venv = Join-Path $svc "mapenv"
    Write-Host ""
    Write-Host "Creating virtual environment: $venv" -ForegroundColor Yellow
    python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "python -m venv failed (is Python 3.11+ on PATH?)" }
    $py = Join-Path $venv "Scripts\python.exe"
    & $py -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
    & $py -m pip install -r (Join-Path $svc "requirements-dev.txt")
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

Write-Host ""
Write-Host "Done. Next steps:" -ForegroundColor Cyan
Write-Host "  1. In the OLD folder:  docker compose down      (keeps your database volume)"
Write-Host "  2. In this folder:     docker compose up --build -d"
Write-Host "  3. Check:              docker compose ps        (both services healthy)"
Write-Host "  4. Run the tests:      cd services\geo-data ; .\mapenv\Scripts\python.exe -m pytest tests -v"
Write-Host "If you use git, run 'git status' here to see exactly what changed."
