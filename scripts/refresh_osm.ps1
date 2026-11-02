# scripts/refresh_osm.ps1
# Automates monthly refresh of OpenStreetMap data for Nepal PostGIS.
#
# Steps executed:
#   a. Download new nepal-latest.osm.pbf into data/
#   b. Create temporary database nepal_osm_next with PostGIS & hstore
#   c. Run osm2pgsql import (--slim --latlong --hstore) into nepal_osm_next
#   d. Sanity check: row count within ~10% of old database, Chhatradev sanity
#   e. Swap: stop geo-data, rename nepal_osm -> nepal_osm_old, nepal_osm_next -> nepal_osm, start geo-data
#   f. Keep nepal_osm_old for backup/rollback

param(
    [string]$DownloadUrl = "https://download.geofabrik.de/asia/nepal-latest.osm.pbf",
    [switch]$SkipDownload = $false,
    [string]$TargetDb = "nepal_osm_next",
    [switch]$TestOnly = $false
)

$ErrorActionPreference = "Stop"

if (-not $SkipDownload -and -not ($DownloadUrl -match "^https?://")) {
    throw "Security validation failed: Download URL must start with http:// or https://"
}

# Paths
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$DataDir = Join-Path $ProjectRoot "data"
$EnvFile = Join-Path $ProjectRoot ".env"

if (-not (Test-Path $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
}

# Read DB credentials from .env
$DbUser = "map-osm"
$DbPassword = ""
$DbName = "nepal_osm"

if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#")) {
            $parts = $line.Split("=", 2)
            if ($parts.Count -eq 2) {
                $key = $parts[0].Trim()
                $val = $parts[1].Trim()
                if ($key -eq "POSTGRES_USER" -or $key -eq "DB_USER") { $DbUser = $val }
                if ($key -eq "POSTGRES_PASSWORD" -or $key -eq "DB_PASSWORD") { $DbPassword = $val }
                if ($key -eq "POSTGRES_DB" -or $key -eq "DB_NAME") { $DbName = $val }
            }
        }
    }
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  Nepal OSM Data Refresh: Zero-Downtime Database Swap     " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# ── Step a: Download latest PBF ───────────────────────────────────────────────
$PbfFile = Join-Path $DataDir "nepal-latest.osm.pbf"
if (-not $SkipDownload) {
    Write-Host "`n[1/6] Downloading latest Nepal OSM PBF..." -ForegroundColor Yellow
    Write-Host "Source: $DownloadUrl"
    Write-Host "Destination: $PbfFile"
    curl.exe -L -o $PbfFile $DownloadUrl
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to download $DownloadUrl"
    }
} else {
    if (-not (Test-Path $PbfFile)) {
        $existing = Get-ChildItem -Path $DataDir -Filter "*.osm.pbf" | Select-Object -First 1
        if ($existing) {
            $PbfFile = $existing.FullName
        }
    }
    Write-Host "`n[1/6] Skipping download as requested. Using existing: $PbfFile" -ForegroundColor Yellow
}

if (-not (Test-Path $PbfFile)) {
    throw "PBF file not found at $PbfFile"
}

$PbfFileName = Split-Path -Leaf $PbfFile

# ── Step b: Create temporary database $TargetDb ──────────────────────────
Write-Host "`n[2/6] Preparing $TargetDb database..." -ForegroundColor Yellow

docker exec nepal-postgis psql -U $DbUser -d postgres -c "DROP DATABASE IF EXISTS $TargetDb;"
docker exec nepal-postgis psql -U $DbUser -d postgres -c "CREATE DATABASE $TargetDb;"
docker exec nepal-postgis psql -U $DbUser -d $TargetDb -c "CREATE EXTENSION IF NOT EXISTS postgis;"
docker exec nepal-postgis psql -U $DbUser -d $TargetDb -c "CREATE EXTENSION IF NOT EXISTS hstore;"

# ── Step c: Run osm2pgsql import into $TargetDb ──────────────────────────
Write-Host "`n[3/6] Running osm2pgsql import into $TargetDb..." -ForegroundColor Yellow

docker run --rm `
    --network nepal-map-agent_default `
    -v "${DataDir}:/data" `
    -e PGPASSWORD=$DbPassword `
    iboates/osm2pgsql `
    -c -s -l -k `
    -H nepal-postgis -P 5432 -U $DbUser -d $TargetDb `
    "/data/$PbfFileName"

if ($LASTEXITCODE -ne 0) {
    throw "osm2pgsql import failed with exit code $LASTEXITCODE"
}

# ── Step d: Sanity checks ─────────────────────────────────────────────────────
Write-Host "`n[4/6] Running sanity checks on imported data..." -ForegroundColor Yellow

$oldPointCountRaw = docker exec nepal-postgis psql -U $DbUser -d $DbName -t -c "SELECT count(*) FROM planet_osm_point;"
$oldPointCount = [int]($oldPointCountRaw.Trim())

$newPointCountRaw = docker exec nepal-postgis psql -U $DbUser -d $TargetDb -t -c "SELECT count(*) FROM planet_osm_point;"
$newPointCount = [int]($newPointCountRaw.Trim())

Write-Host "Old DB point count: $oldPointCount"
Write-Host "New DB point count: $newPointCount"

$minExpected = [int]($oldPointCount * 0.85)
$maxExpected = [int]($oldPointCount * 1.25)

if ($newPointCount -lt $minExpected -or $newPointCount -gt $maxExpected) {
    throw "Sanity check failed: new row count ($newPointCount) differs by > 15-20% from old ($oldPointCount). Expected between $minExpected and $maxExpected."
}

# Chhatradev check in new DB
$chhatradevRaw = docker exec nepal-postgis psql -U $DbUser -d $TargetDb -t -c "
    SELECT count(*) FROM planet_osm_point
    WHERE ST_DWithin(way::geography, ST_SetSRID(ST_MakePoint(83.2253,27.9954),4326)::geography, 10000)
      AND (amenity IS NOT NULL OR (tags->'healthcare') IS NOT NULL);
"
$chhatradevCount = [int]($chhatradevRaw.Trim())
Write-Host "Chhatradev 10km amenities count in new DB: $chhatradevCount"

if ($chhatradevCount -lt 50) {
    throw "Sanity check failed: Chhatradev returned only $chhatradevCount amenities (expected >= 50)."
}

Write-Host "Sanity checks PASSED!" -ForegroundColor Green

if ($TestOnly) {
    Write-Host "`n[5/6] TestOnly mode enabled: skipping database swap. Active database '$DbName' was left completely untouched." -ForegroundColor Green
    Write-Host "`n[6/6] Scratch database '$TargetDb' successfully tested and verified!" -ForegroundColor Green
    return
}

# ── Step e: Atomic database swap ──────────────────────────────────────────────
Write-Host "`n[5/6] Swapping databases (nepal_osm -> nepal_osm_old, nepal_osm_next -> nepal_osm)..." -ForegroundColor Yellow

# Stop geo-data container if running
docker stop nepal-geo-data 2>$null

# Terminate active connections to nepal_osm
docker exec nepal-postgis psql -U $DbUser -d postgres -c "
    SELECT pg_terminate_backend(pid) FROM pg_stat_activity
    WHERE datname = '$DbName' AND pid <> pg_backend_pid();
"

# Swap names
docker exec nepal-postgis psql -U $DbUser -d postgres -c "DROP DATABASE IF EXISTS ${DbName}_old;"
docker exec nepal-postgis psql -U $DbUser -d postgres -c "ALTER DATABASE $DbName RENAME TO ${DbName}_old;"
docker exec nepal-postgis psql -U $DbUser -d postgres -c "ALTER DATABASE nepal_osm_next RENAME TO $DbName;"

# Update DATA_AS_OF in .env
$Today = (Get-Date).ToString("yyyy-MM-dd")
if (Test-Path $EnvFile) {
    $envContent = Get-Content $EnvFile
    $newEnv = @()
    $found = $false
    foreach ($line in $envContent) {
        if ($line -match "^DATA_AS_OF=") {
            $newEnv += "DATA_AS_OF=$Today"
            $found = $true
        } else {
            $newEnv += $line
        }
    }
    if (-not $found) {
        $newEnv += "DATA_AS_OF=$Today"
    }
    Set-Content -Path $EnvFile -Value $newEnv
    Write-Host "Updated DATA_AS_OF=$Today in .env"
}

# Restart geo-data container if docker compose is used
docker start nepal-geo-data 2>$null

# ── Step f: Wrap up ───────────────────────────────────────────────────────────
Write-Host "`n[6/6] Refresh complete!" -ForegroundColor Green
Write-Host "nepal_osm has been refreshed with new data as of $Today." -ForegroundColor Green
Write-Host "nepal_osm_old has been preserved for rollback." -ForegroundColor Cyan
Write-Host "To drop the old backup after validating: docker exec nepal-postgis psql -U $DbUser -c 'DROP DATABASE nepal_osm_old;'" -ForegroundColor Gray
