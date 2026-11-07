# nepal-map-agent

Grounded Nepal geography and disaster-relevant amenity data for an AI map agent.

- `services/geo-data/` - FastAPI + PostGIS microservice (place resolver, admin profile, amenity queries). See its [README](services/geo-data/README.md).
- `docker-compose.yml` - PostGIS + geo-data (project name `nepal-map-agent`).
- `scripts/` - monthly OSM refresh, scheduled-task registration, smoke test (`verify_geo_data.ps1`, port 8001).

Quick start: copy `.env.example` to `.env`, then `docker compose up -d postgis`, import OSM data (service README section 4), then `docker compose up --build -d`.

Migrating from an older copy of the project: see `migrate_from_old.ps1` (copies `.env`, `data/`, `.git`, rebuilds the venv).
