# geo-data microservice

Microservice providing grounded administrative geography and disaster-relevant amenity queries for Nepal. Backed by a local **PostGIS** database (OpenStreetMap data) and **tlf-geo** administrative boundaries.

---

## 1. Architecture & Design Rules

- **Microservices Boundary**: Standalone service; owns its database. All external access is through HTTP endpoints.
- **Local PostGIS**: Replaces the slow and rate-limited public Overpass API with indexed spatial queries (bounding-box prefilter + GiST index).
- **"Unknown ≠ None" Principle**: Missing OSM tags (e.g. `beds=`, `ward=`) surface as `null`/`unknown` with grounding warnings, never assumed zero or nonexistent.
- **Multi-tier Resolver**:
  1. *Curated*: ~50 famous landmarks (EBC, ABC, Boudhanath, etc.)
  2. *Administrative*: 77 districts, 753 local levels (tlf-geo offline boundaries)
  3. *Nominatim*: Fallback geocoder with disaster-aware category reranking

---

## 2. Prerequisites

- **Docker Desktop** (running on Windows)
- **Python 3.11+** (for local development)
- **PowerShell 7+** or standard Windows PowerShell

---

## 3. Starting the Database

From the repository root (`nepal-map-agent/`):

```powershell
# Start the PostGIS container
docker compose up -d postgis
```

PostGIS runs on:
- **Host**: `localhost:5433` (mapped from container `5432` to avoid conflicting with existing Postgres instances)
- **Inside Docker network**: `nepal-postgis:5432`
- **Database**: `nepal_osm`
- **User**: `map-osm`

---

## 4. Importing OpenStreetMap Data

Download the latest Nepal PBF extract from Geofabrik:

```powershell
curl.exe -L -o data/nepal-latest.osm.pbf https://download.geofabrik.de/asia/nepal-latest.osm.pbf
```

Enable the extensions osm2pgsql needs (the PostGIS image does not create `hstore` for you; `-k` fails without it). `scripts/refresh_osm.ps1` does this automatically, but a manual first import must do it once:

```powershell
docker exec nepal-postgis psql -U map-osm -d nepal_osm -c "CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS hstore;"
```

Import into PostGIS using the `iboates/osm2pgsql` container:

```powershell
docker run --rm `
    --network nepal-map-agent_default `
    -v "${PWD}\data:/data" `
    -e PGPASSWORD=$env:POSTGRES_PASSWORD `
    iboates/osm2pgsql `
    --create --slim --drop --hstore-all --latlong --cache 1000 `
    -H nepal-postgis -P 5432 -U map-osm -d nepal_osm `
    /data/nepal-latest.osm.pbf
```

*Flags explained:*
- `--create` (`-c`): Create/overwrite tables
- `--slim` (`-s`): Slim mode required to parse nodes, ways, and relations from PBF
- `--drop`: Drop temporary slim tables (planet_osm_nodes/ways/rels) after import; cuts database size by ~40-50% since monthly swap imports do not need differential updates
- `--hstore-all`: Put all OSM tags into the `tags` hstore column (not just non-standard overflow tags), ensuring full access to all metadata
- `--latlong` (`-l`): Store geometries directly in EPSG:4326 (WGS84 lat/lon)
- `--cache 1000`: Allocate 1,000 MB RAM for node caching during import to speed up processing

---

## 5. Running the Service

### Option A: Local Virtual Environment (Fast dev loop)

```powershell
cd services/geo-data
.\mapenv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8001
```

*Note on Windows*: The service automatically configures `WindowsSelectorEventLoopPolicy` for `psycopg` async compatibility.

### Option B: Docker Compose

```powershell
# Starts PostGIS and geo-data together
docker compose up --build -d
```

Service is available at `http://localhost:8001`. Interactive OpenAPI docs are at `http://localhost:8001/docs`.

---

## 6. Running Tests

```powershell
cd services/geo-data
.\mapenv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\mapenv\Scripts\python.exe -m pytest tests/ -v
```

- **Unit tests** run completely offline with mocked database and resolver calls.
- **Integration tests** automatically run against the local PostGIS container if reachable (e.g. testing Chhatradev radius queries), and skip cleanly if the DB is down.

---

## 7. Monthly Data Refresh (database swap, brief downtime)

A PowerShell script automates the monthly sync:

```powershell
# From project root:
powershell -ExecutionPolicy Bypass -File scripts/refresh_osm.ps1
```

Workflow:
1. Downloads `nepal-latest.osm.pbf` into `data/`
2. Creates temporary database `nepal_osm_next` with PostGIS and hstore extensions
3. Imports data into `nepal_osm_next`
4. Runs sanity checks:
   - Row count matches old database within -15% / +25% (0.85x to 1.25x of old count)
   - Chhatradev query returns >= 50 amenities
5. Stops `geo-data` and swaps databases (a few seconds of downtime; every
   native command's exit code is checked and a failed swap is rolled back):
   - Renames `nepal_osm` -> `nepal_osm_old_<timestamp>` (only the newest backup is kept)
   - Renames `nepal_osm_next` -> `nepal_osm`
6. Updates `DATA_AS_OF` in `.env` and recreates `geo-data` so it picks it up

> [!WARNING]
> **Do NOT register this scheduled task yet.** Do not run `register_refresh_task.ps1` until `scripts/refresh_osm.ps1` has had at least one real, verified successful run end-to-end.

To automate on Windows Task Scheduler (runs on the 1st of every month at 02:00), run once as Administrator:
```powershell
powershell -ExecutionPolicy Bypass -File scripts/register_refresh_task.ps1
```

---

## 8. API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Service health status and OSM `data_as_of` date |
| `GET` | `/resolve?q=...` | 3-tier place resolver to coordinates with ambiguity flags |
| `GET` | `/profile?lat=...&lon=...` | Administrative hierarchy (Province down to Ward) and 2021 demographics |
| `GET` | `/amenities?lat=...&lon=...&radius_m=...` | PostGIS disaster amenities grouped by 7 categories; named node+way duplicates (same category, <100 m) merged |
| `POST` | `/amenities/area` | Amenities inside a GeoJSON Polygon/MultiPolygon or a `local_level_code` (max 8,500 km², 200k vertices) |
