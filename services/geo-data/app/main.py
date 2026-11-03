# services/geo-data/app/main.py
import asyncio
import json
import logging
import math
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
import pyproj
from shapely.geometry import mapping, shape
from shapely.validation import make_valid
from tlf_geo_profiler import profile_dict

from app import config
from app.admin_matcher import get_polygon_for_local_level
from app.db_client import DBError, close_pool, fetch_amenities, fetch_amenities_area, get_data_as_of, init_pool
from app.models import (
    AmenitiesResponse,
    AreaAmenitiesRequest,
    AreaAmenitiesResponse,
    AreaSummary,
    HealthResponse,
    ProfileResponse,
    ResolveResponse,
)
from app.resolve_client import ResolveError, parse_candidates, pick_best, resolve_place, resolve_query

# psycopg (async) requires SelectorEventLoop on Windows; uvicorn defaults to
# ProactorEventLoop which is incompatible.  Switch early before the loop starts.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

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
    try:
        await init_pool()
    except Exception as e:
        logger.warning("Database pool could not be initialized at startup: %s", e)
    yield
    await close_pool()


app = FastAPI(title="geo-data service", lifespan=lifespan)

# Restrict CORS to configured origins (default localhost dev ports)
_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:8000,http://127.0.0.1:3000,http://127.0.0.1:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── validation helper ────────────────────────────────────────────────────────

def validate_query_params(request: Request, allowed: set[str]) -> None:
    """Ensure no unexpected query parameters were passed (e.g. typos)."""
    unknown = set(request.query_params.keys()) - allowed
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown query parameter(s): {', '.join(sorted(unknown))}",
        )


# ── testable helper (makes mocking profile_dict easy in tests) ───────────────

async def _call_profile_dict(lat: float, lon: float) -> dict:
    """Thin async wrapper so tests can patch this without touching asyncio."""
    return await asyncio.to_thread(profile_dict, lat, lon)


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health(request: Request):
    validate_query_params(request, set())
    data_as_of = await get_data_as_of()
    return {"status": "ok", "data_as_of": data_as_of}


@app.get("/resolve", response_model=ResolveResponse)
async def get_resolve(
    request: Request,
    q: str = Query(..., min_length=1, description="Nepal place name to look up"),
):
    """
    Resolve a Nepal place name to coordinates.

    Uses a 3-tier lookup:
      1. Curated popular destinations (EBC, ABC, Boudhanath Stupa, etc.)
      2. Administrative geography (77 districts, 753 local levels via tlf-geo boundaries)
      3. Nominatim geocoding with category reranking
    """
    validate_query_params(request, {"q"})
    try:
        res = await resolve_query(q)
        res["data_as_of"] = await get_data_as_of()
        return res
    except ResolveError as e:
        logger.error("Resolve error: %s", e)
        raise HTTPException(status_code=502, detail="Geocoding service unavailable")


@app.get("/profile", response_model=ProfileResponse)
async def get_profile(
    request: Request,
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
    validate_query_params(request, {"lat", "lon"})
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


@app.get("/amenities", response_model=AmenitiesResponse)
async def get_amenities(
    request: Request,
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
    validate_query_params(request, {"lat", "lon", "radius_m"})
    try:
        amenities = await fetch_amenities(lat, lon, radius_m)
    except DBError as e:
        logger.error("Database error in /amenities: %s", e)
        raise HTTPException(status_code=502, detail="Spatial database query failed")

    # Group into ordered categories
    categories: dict[str, list] = {cat: [] for cat in CATEGORY_ORDER}
    for item in amenities:
        cat = item.get("category", "other")
        if cat not in categories:
            cat = "other"
        categories[cat].append(item)

    totals = {cat: len(items) for cat, items in categories.items()}
    data_as_of = await get_data_as_of()

    return {
        "source": "postgis",
        "data_as_of": data_as_of,
        "radius_m": radius_m,
        "data_warning": DATA_WARNING,
        "categories": categories,
        "totals": totals,
    }


def _haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two coordinates."""
    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


@app.post("/amenities/area", response_model=AreaAmenitiesResponse)
async def post_amenities_area(request: Request, body: AreaAmenitiesRequest):
    """
    All disaster-relevant amenities within a polygon or local level boundary.

    Accepts either an explicit GeoJSON Polygon/MultiPolygon or a local_level_code.
    Returns category summary, feature type breakdown, area info, and amenities.
    Supports top_n limiting per category and proximity sorting via near_lat / near_lon.
    """
    validate_query_params(request, set())

    # Resolve geometry from local_level_code if requested
    geom_dict = body.geometry
    if body.local_level_code:
        try:
            geom_dict = get_polygon_for_local_level(body.local_level_code)
        except ValueError:
            raise HTTPException(
                status_code=404,
                detail=f"Local level code '{body.local_level_code}' not found",
            )

    # Validate and repair geometry
    try:
        s_geom = shape(geom_dict)
        if not s_geom.is_valid:
            s_geom = make_valid(s_geom)
            if not s_geom.is_valid:
                raise ValueError()
        minx, miny, maxx, maxy = s_geom.bounds
        if minx < -180 or maxx > 180 or miny < -90 or maxy > 90:
            raise HTTPException(status_code=422, detail="Coordinates must be within EPSG:4326")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid polygon geometry")

    # Reject geometry outside Nepal's bounding box
    nepal_min_lon = 80.0
    nepal_max_lon = 88.3
    nepal_min_lat = 26.3
    nepal_max_lat = 30.5
    if (
        minx < nepal_min_lon
        or maxx > nepal_max_lon
        or miny < nepal_min_lat
        or maxy > nepal_max_lat
    ):
        raise HTTPException(
            status_code=422,
            detail="Geometry is outside Nepal's bounding box",
        )

    # Calculate geodesic area in square kilometres
    try:
        geod = pyproj.Geod(ellps="WGS84")
        area_m2, _ = geod.geometry_area_perimeter(s_geom)
        area_km2 = round(abs(area_m2) / 1_000_000.0, 2)
    except Exception:
        area_km2 = 0.0

    if area_km2 > 5000.0:
        raise HTTPException(
            status_code=422,
            detail="Geometry area exceeds maximum limit of 5,000 km2",
        )

    # Send repaired geometry to the database
    geojson_str = json.dumps(mapping(s_geom))

    try:
        amenities = await fetch_amenities_area(geojson_str, timeout_seconds=10.0)
    except TimeoutError as exc:
        logger.error("Area amenities timeout: %s", exc)
        raise HTTPException(status_code=504, detail="Area query timed out after 10s")
    except DBError as exc:
        logger.error("Area amenities database error: %s", exc)
        raise HTTPException(status_code=502, detail="Spatial database query failed")

    # Aggregate area statistics
    by_category: dict[str, int] = {cat: 0 for cat in CATEGORY_ORDER}
    by_type: dict[str, int] = {}
    for item in amenities:
        cat = item.get("category", "other")
        if cat not in by_category:
            cat = "other"
        by_category[cat] = by_category.get(cat, 0) + 1
        t = item.get("type", "other")
        by_type[t] = by_type.get(t, 0) + 1

    summary = {
        "total": len(amenities),
        "by_category": by_category,
        "by_type": by_type,
    }

    # Centroid and reference coordinate for distance sorting
    centroid = {
        "lat": round(s_geom.centroid.y, 6),
        "lon": round(s_geom.centroid.x, 6),
    }
    ref_lat = body.near_lat if body.near_lat is not None else centroid["lat"]
    ref_lon = body.near_lon if body.near_lon is not None else centroid["lon"]

    # Top_n per category and truncated flag per category
    categories: dict[str, list] = {cat: [] for cat in CATEGORY_ORDER}
    truncated: dict[str, bool] = {cat: False for cat in CATEGORY_ORDER}

    for cat in CATEGORY_ORDER:
        cat_items = [a for a in amenities if a.get("category", "other") == cat]
        cat_items.sort(
            key=lambda a: _haversine_distance_m(ref_lat, ref_lon, a["lat"], a["lon"])
        )
        if len(cat_items) > body.top_n:
            truncated[cat] = True
        categories[cat] = cat_items[: body.top_n]

    items = [item for cat in CATEGORY_ORDER for item in categories[cat]]
    data_as_of = await get_data_as_of()

    return {
        "source": "postgis",
        "data_as_of": data_as_of,
        "data_warning": DATA_WARNING,
        "area": {
            "area_km2": area_km2,
            "centroid": centroid,
        },
        "summary": summary,
        "categories": categories,
        "truncated": truncated,
        "items": items,
        "top_n": body.top_n,
        "near_lat": body.near_lat,
        "near_lon": body.near_lon,
    }
