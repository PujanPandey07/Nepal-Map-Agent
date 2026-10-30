# services/geo-data/app/main.py
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from tlf_geo_profiler import profile_dict

from app.osm_client import OverpassError, fetch_amenities
from app.resolve_client import ResolveError, parse_candidates, pick_best, resolve_place, resolve_query

logger = logging.getLogger("geo-data")

DATA_WARNING = (
    "OSM coverage in rural Nepal is incomplete. "
    "Absence of a facility does not mean it does not exist."
)

# Category keys in the order we want them in the response
CATEGORY_ORDER = [
    "health",
    "emergency_services",
    "evacuation_sites",
    "infrastructure",
    "food_and_water",
    "government",
    "other",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm-up: make the package load its boundary data now, not on the first request
    await asyncio.to_thread(profile_dict, 27.7040, 85.3070)
    yield


app = FastAPI(title="geo-data service", lifespan=lifespan)


# ── testable helper (makes mocking profile_dict easy in tests) ───────────────

async def _call_profile_dict(lat: float, lon: float) -> dict:
    """Thin async wrapper so tests can patch this without touching asyncio."""
    return await asyncio.to_thread(profile_dict, lat, lon)


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/resolve")
async def get_resolve(
    q: str = Query(..., min_length=1, description="Nepal place name to look up"),
):
    """
    Resolve a Nepal place name to coordinates.

    Uses a 3-tier lookup:
      1. Curated popular destinations (EBC, ABC, Boudhanath Stupa, etc.)
      2. Administrative geography (77 districts, 753 local levels via tlf-geo boundaries)
      3. Nominatim geocoding with category reranking
    """
    try:
        return await resolve_query(q)
    except ResolveError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/profile")
async def get_profile(
    lat: float = Query(..., ge=-90, le=90, description="Latitude"),
    lon: float = Query(..., ge=-180, le=180, description="Longitude"),
):
    """
    Administrative profile for a coordinate.

    Returns province, district, local level, ward, postal code and 2021
    census demographics. *partial* is true when some fields are unknown
    (no ward polygon, unresolved local level, or no demographics); *notes*
    explains what is missing.  Missing means unknown, not zero.
    """
    try:
        data = await _call_profile_dict(lat, lon)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail="Coordinate is outside Nepal's known boundaries",
        )

    notes = []
    admin = data["admin_unit"]
    if admin.get("ward") is None:
        notes.append("No ward polygon found here; ward is unknown.")
    if admin.get("local_level") is None:
        notes.append("Local level could not be resolved; it is unknown.")
    if data["demographics"].get("population") is None:
        notes.append("No demographic data; population is unknown, not zero.")

    return {
        "source": "tlf-geo-profiler",
        "partial": bool(notes),
        "notes": notes,
        **data,
    }


@app.get("/amenities")
async def get_amenities(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius_m: int = Query(
        5000, ge=500, le=25000,
        description="Search radius in metres (500 – 25 000)",
    ),
):
    """
    All disaster-relevant amenities near a point, grouped by category.

    Categories returned:
      health            hospitals, clinics, pharmacies, health posts
      emergency_services  police, fire stations, ambulance stations
      evacuation_sites  schools, community centres, shelters, places of worship
      infrastructure    helipads, fuel stations
      food_and_water    supermarkets, water points
      government        government offices, town halls
      other             anything else matched by the query

    Results are deduplicated (node + way for the same building counted once)
    and cached for 10 minutes.  *data_warning* must be surfaced to users.
    """
    try:
        amenities = await fetch_amenities(lat, lon, radius_m)
    except OverpassError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Group into ordered categories
    categories: dict[str, list] = {cat: [] for cat in CATEGORY_ORDER}
    for item in amenities:
        cat = item.get("category", "other")
        if cat not in categories:
            cat = "other"
        categories[cat].append(item)

    totals = {cat: len(items) for cat, items in categories.items()}

    return {
        "source": "overpass",
        "radius_m": radius_m,
        "data_warning": DATA_WARNING,
        "categories": categories,
        "totals": totals,
    }
