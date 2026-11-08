# scripts/refresh_osm.ps1
# Automates monthly refresh of OpenStreetMap data for Nepal PostGIS.
#
# Steps executed:
#   a. Download new nepal-latest.osm.pbf into data/ (to a .part file first)
#   b. Create temporary database nepal_osm_next with PostGIS & hstore
#   c. Run osm2pgsql import (--slim --latlong --hstore) into nepal_osm_next
#   d. Sanity check: row count within range of old database, Chhatradev sanity
#   e. Swap: stop geo-data, rename nepal_osm -> nepal_osm_old_<timestamp>,
#      nepal_osm_next -> nepal_osm (rolled back if the second rename fails),
#      then recreate geo-data.  geo-data is DOWN for the few seconds this takes.
#   f. Keep only the newest nepal_osm_old_* database for rollback
#
# Every native command (docker, psql, curl) has its exit code checked:
# $ErrorActionPreference = "Stop" does NOT cover failing native commands.

param(
    [string]$DownloadUrl = "https://download.geofabrik.de/asia/nepal-latest.osm.pbf",
    [switch]$SkipDownload = $false,
    [string]$TargetDb = "nepal_osm_next",
    [switch]$TestOnly = $false,
    # LiveDb parameter allows testing rename swap on scratch databases without touching live nepal_osm
    [string]$LiveDb = ""
)

$ErrorActionPreference = "Stop"

if (-not $SkipDownload -and -not ($DownloadUrl -match "^https?://")) {
    throw "Security validation failed: Download URL must start with http:// or https://"
}
if ($TargetDb -notmatch "^[A-Za-z0-9_]+$") {
    throw "TargetDb must contain only letters, digits and underscores"
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
$EnvDbName = "nepal_osm"

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
                if ($key -eq "POSTGRES_DB" -or $key -eq "DB_NAME") { $EnvDbName = $val }
            }
        }
    }
}

# LiveDb defaults to the database name from .env if not explicitly passed.
# This enables testing the sanity checks and swap logic against scratch databases
# (e.g. -LiveDb nepal_osm_refresh_test) while keeping the live database safe.
if ([string]::IsNullOrWhiteSpace($LiveDb)) {
    $LiveDb = $EnvDbName
}
$DbName = $LiveDb

if ($DbName -notmatch "^[A-Za-z0-9_]+$") {
    throw "LiveDb must contain only letters, digits and underscores"
}

# --- helpers --------------------------------------------------------------------

function Assert-LastExit([string]$What) {
    if ($LASTEXITCODE -ne 0) {
        throw "$What failed (exit code $LASTEXITCODE)"
    }
}

# Run one SQL statement through psql inside the postgis container.
# Throws on a non-zero exit code; returns trimmed output (use -t -A: bare values).
function Invoke-Psql([string]$Db, [string]$Sql) {
    $out = docker exec nepal-postgis psql -U $DbUser -d $Db -v ON_ERROR_STOP=1 -t -A -c $Sql
    Assert-LastExit "psql on database '$Db'"
    return (($out | Out-String).Trim())
}

function Get-PsqlCount([string]$Db, [string]$Sql) {
    $raw = Invoke-Psql $Db $Sql
    $n = 0
    if (-not [int]::TryParse($raw, [ref]$n)) {
        throw "Expected a number from psql on '$Db' but got: '$raw'"
    }
    return $n
}

function Test-GeoDataContainer {
    $names = docker ps -a --filter "name=^nepal-geo-data$" --format "{{.Names}}"
    return [bool]$names
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  Nepal OSM Data Refresh: database swap (brief downtime)  " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# --- Step a: Download latest PBF ------------------------------------------------
$PbfFile = Join-Path $DataDir "nepal-latest.osm.pbf"
if (-not $SkipDownload) {
    Write-Host "`n[1/6] Downloading latest Nepal OSM PBF..." -ForegroundColor Yellow
    Write-Host "Source: $DownloadUrl"
    Write-Host "Destination: $PbfFile"
    # Download to a temp file; --fail makes HTTP errors (404, 500) a non-zero
    # exit code instead of saving an error page as the PBF.
    $PbfTmp = "$PbfFile.part"
    curl.exe -L --fail -o $PbfTmp $DownloadUrl
    if ($LASTEXITCODE -ne 0) {
        Remove-Item $PbfTmp -ErrorAction SilentlyContinue
        throw "Failed to download $DownloadUrl (curl exit code $LASTEXITCODE)"
    }
    Move-Item -Force $PbfTmp $PbfFile
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

# --- Step b: Create temporary database $TargetDb --------------------------------
Write-Host "`n[2/6] Preparing $TargetDb database..." -ForegroundColor Yellow

$null = Invoke-Psql "postgres" "DROP DATABASE IF EXISTS $TargetDb;"
$null = Invoke-Psql "postgres" "CREATE DATABASE $TargetDb;"
$null = Invoke-Psql $TargetDb "CREATE EXTENSION IF NOT EXISTS postgis;"
$null = Invoke-Psql $TargetDb "CREATE EXTENSION IF NOT EXISTS hstore;"

# --- Step c: Run osm2pgsql import into $TargetDb --------------------------------
Write-Host "`n[3/6] Running osm2pgsql import into $TargetDb..." -ForegroundColor Yellow

# The compose project is named "nepal-map-agent" (see docker-compose.yml), so
# the network name does not depend on the folder name.
# Flags match manual import: --create, --slim, --drop (clean temp tables), --hstore-all, --latlong, --cache 1000
docker run --rm `
    --network nepal-map-agent_default `
    -v "${DataDir}:/data" `
    -e PGPASSWORD=$DbPassword `
    iboates/osm2pgsql `
    --create --slim --drop --hstore-all --latlong --cache 1000 `
    -H nepal-postgis -P 5432 -U $DbUser -d $TargetDb `
    "/data/$PbfFileName"

if ($LASTEXITCODE -ne 0) {
    throw "osm2pgsql import failed with exit code $LASTEXITCODE"
}

# --- Step d: Sanity checks ------------------------------------------------------
Write-Host "`n[4/6] Running sanity checks on imported data..." -ForegroundColor Yellow

$newPointCount = Get-PsqlCount $TargetDb "SELECT count(*) FROM planet_osm_point;"
Write-Host "New DB point count: $newPointCount"

$oldPointCount = $null
try {
    $oldPointCount = Get-PsqlCount $DbName "SELECT count(*) FROM planet_osm_point;"
} catch {
    Write-Warning "Could not read the existing '$DbName' database ($($_.Exception.Message)); skipping the row-count comparison."
}

if ($null -ne $oldPointCount) {
    Write-Host "Old DB point count: $oldPointCount"
    $minExpected = [int]($oldPointCount * 0.85)
    $maxExpected = [int]($oldPointCount * 1.25)
    if ($newPointCount -lt $minExpected -or $newPointCount -gt $maxExpected) {
        throw "Sanity check failed: new row count ($newPointCount) differs by > 15-25% from old ($oldPointCount). Expected between $minExpected and $maxExpected."
    }
}

# Chhatradev check in new DB (bounding-box prefilter so the spatial index is used)
$chhatradevCount = Get-PsqlCount $TargetDb @"
SELECT count(*) FROM planet_osm_point
WHERE way && ST_MakeEnvelope(83.10, 27.90, 83.34, 28.09, 4326)
  AND ST_DWithin(way::geography, ST_SetSRID(ST_MakePoint(83.2253,27.9954),4326)::geography, 10000)
  AND (amenity IS NOT NULL OR (tags->'healthcare') IS NOT NULL);
"@
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

# --- Step e: Database swap ------------------------------------------------------
$Stamp = (Get-Date).ToString("yyyyMMddHHmmss")
$OldDb = "${DbName}_old_$Stamp"
Write-Host "`n[5/6] Swapping databases ($DbName -> $OldDb, $TargetDb -> $DbName)..." -ForegroundColor Yellow

$hadContainer = Test-GeoDataContainer
if ($hadContainer) {
    docker stop nepal-geo-data
    Assert-LastExit "docker stop nepal-geo-data"
}

$swapped = $false
try {
    # Terminate remaining connections so the renames are allowed.
    $null = Invoke-Psql "postgres" "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('$DbName', '$TargetDb') AND pid <> pg_backend_pid();"

    # No DROP before the swap: the current database is renamed to a timestamped
    # backup, so a failure here can never leave you without a database.
    $null = Invoke-Psql "postgres" "ALTER DATABASE $DbName RENAME TO $OldDb;"
    try {
        $null = Invoke-Psql "postgres" "ALTER DATABASE $TargetDb RENAME TO $DbName;"
        $swapped = $true
    } catch {
        Write-Warning "Second rename failed; rolling the first rename back."
        $null = Invoke-Psql "postgres" "ALTER DATABASE $OldDb RENAME TO $DbName;"
        throw
    }
} catch {
    if ($hadContainer) {
        Write-Warning "Swap failed; restarting geo-data against the previous database."
        docker start nepal-geo-data | Out-Null
    }
    throw
}

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

# Recreate (not just start) geo-data so it picks up the new DATA_AS_OF from .env.
if ($hadContainer) {
    docker compose --project-directory $ProjectRoot up -d --no-deps --force-recreate geo-data
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "docker compose recreate failed; starting the existing container instead."
        docker start nepal-geo-data | Out-Null
        Assert-LastExit "docker start nepal-geo-data"
    }
}

# Keep only the newest backup database.
$oldDbs = Invoke-Psql "postgres" "SELECT datname FROM pg_database WHERE datname LIKE '${DbName}\_old\_%' ORDER BY datname DESC OFFSET 1;"
foreach ($db in ($oldDbs -split "`r?`n" | Where-Object { $_ -match "^[A-Za-z0-9_]+$" })) {
    Write-Host "Dropping older backup database $db"
    $null = Invoke-Psql "postgres" "DROP DATABASE IF EXISTS $db;"
}

# --- Step f: Wrap up ------------------------------------------------------------
Write-Host "`n[6/6] Refresh complete!" -ForegroundColor Green
Write-Host "$DbName has been refreshed with new data as of $Today." -ForegroundColor Green
Write-Host "$OldDb has been preserved for rollback." -ForegroundColor Cyan
Write-Host "To drop it after validating: docker exec nepal-postgis psql -U $DbUser -d postgres -c 'DROP DATABASE $OldDb;'" -ForegroundColor Gray
