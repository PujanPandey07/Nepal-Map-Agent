# services/geo-data/app/config.py
"""
Central configuration module.

All settings come from environment variables (or the .env file next to
docker-compose.yml, loaded once at startup).  Nothing is hard-coded here.
"""
import os
import sys
import asyncio
from pathlib import Path

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

from dotenv import load_dotenv

# Walk up from this file to find the project-root .env, then the service-level
# .env (if it exists), giving the service-level file higher priority.
_parents = Path(__file__).resolve().parents
if len(_parents) > 3:
    _project_root = _parents[3]  # nepal-map-agent/
    _service_root = _parents[2]  # services/geo-data/
    load_dotenv(_project_root / ".env")            # project root .env (lower priority)
    load_dotenv(_service_root / ".env", override=True)  # service .env (higher priority)
elif len(_parents) > 1:
    load_dotenv(_parents[1] / ".env")


# ── Database ──────────────────────────────────────────────────────────────────

DB_HOST: str = os.getenv("DB_HOST", "127.0.0.1")
if DB_HOST == "localhost":
    # On Windows, docker binds to 127.0.0.1; resolving localhost to IPv6 ::1 causes connection hangs
    DB_HOST = "127.0.0.1"
DB_PORT: int = int(os.getenv("DB_PORT", "5433"))   # 5433 outside Docker, 5432 inside
DB_NAME: str = os.getenv("DB_NAME", "nepal_osm")
DB_USER: str = os.getenv("DB_USER", "map-osm")
DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")    # must be set; never default to a real password

DB_DSN: str = (
    f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} "
    f"user={DB_USER} password={DB_PASSWORD}"
)

# ── Data freshness ────────────────────────────────────────────────────────────

# Set this to the date encoded in the .pbf filename, e.g. "2026-10-29"
# (from nepal-261029.osm.pbf → year 2026, month 10, day 29).
DATA_AS_OF: str = os.getenv("DATA_AS_OF", "2026-10-29")
