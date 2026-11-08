# services/geo-data/app/db_client.py
"""
PostGIS database client for the geo-data service.

Replaces the live Overpass API with a local PostGIS database backed by
a downloaded Nepal OSM .pbf file (osm2pgsql --hstore import).

Public API:
    fetch_amenities(lat, lon, radius_m) -> list[dict]
    DBError                             (exception)

Each returned dict has the same shape as an Overpass element processed
through amenity_utils.to_amenity_dict():
    {name, type, category, lat, lon, tags}

This means amenity_utils.classify_element and deduplicate work unchanged.
"""
import asyncio
import logging
import math
import re
import time
from typing import Any

import psycopg
import psycopg.errors
import psycopg.rows
from psycopg.conninfo import make_conninfo          # used to inject connect_timeout
from psycopg.types import TypeInfo
from psycopg.types.hstore import register_hstore
from psycopg_pool import AsyncConnectionPool

from app import config
from app.amenity_utils import classify_element, deduplicate, to_amenity_dict

logger = logging.getLogger("geo-data.db")

_HSTORE_RE = re.compile(r'"((?:[^"\\]|\\.)*)"\s*=>\s*(?:NULL|"((?:[^"\\]|\\.)*)")')


# Single-pass unescape: backslash + any char -> that char.  (Chained .replace()
# calls mis-handle sequences such as  \\"  .)
_HSTORE_UNESCAPE_RE = re.compile(r"\\(.)")


def _parse_hstore(text: str) -> dict[str, str]:
    """Parse raw hstore string representation as fallback."""
    if not text:
        return {}
    res: dict[str, str] = {}
    for m in _HSTORE_RE.finditer(text):
        k = _HSTORE_UNESCAPE_RE.sub(r"\1", m.group(1))
        v = m.group(2)
        if v is not None:
            res[k] = _HSTORE_UNESCAPE_RE.sub(r"\1", v)
    return res


class DBError(Exception):
    """Raised when the database query fails."""


# ── SQL queries ───────────────────────────────────────────────────────────────
#
# osm2pgsql splits columns it knows about into dedicated text columns.
# Tags not in that standard list land in the hstore `tags` column.
# `healthcare` is one of those overflow tags — we read it from `tags->'healthcare'`.
#
# Column layout used here:
#   amenity, aeroway, office, shop  — standard columns (directly queryable)
#   healthcare, emergency           — hstore tags column  (tags->'key')
#   name                            — standard column
#   osm_id                          — standard column
#   way                             — geometry already in EPSG:4326
#
# Filter: at least one relevant tag must be non-null.  Keep this in sync with
# the category sets in amenity_utils.py.

_AMENITY_FILTER = """
    amenity IN (
        'hospital','clinic','doctors','pharmacy','dentist',
        'health_post','nursing_home','birthing_centre',
        'police','fire_station','ambulance_station',
        'school','college','university','community_centre',
        'social_facility','shelter','place_of_worship',
        'fuel','water_point','drinking_water','townhall'
    )
    OR (tags->'healthcare') IS NOT NULL
    OR (tags->'emergency')  IS NOT NULL
    OR aeroway = 'helipad'
    OR shop  IN ('supermarket','convenience','general')
    OR office IN ('government','administrative')
"""

# Points: way is geometry(Point,4326) — no transform needed.
# The `way && envelope` prefilter is what lets PostGIS use the GiST index;
# casting `way::geography` alone defeats it and forces a full table scan.
# Params: (xmin, ymin, xmax, ymax, lon, lat, radius_m)
_POINTS_SQL = f"""
SELECT
    osm_id,
    ST_Y(way)          AS lat,
    ST_X(way)          AS lon,
    amenity,
    aeroway,
    office,
    shop,
    name,
    tags->'healthcare' AS healthcare,
    tags->'emergency'  AS emergency,
    tags               AS extra_tags
FROM planet_osm_point
WHERE
    way && ST_MakeEnvelope(%s, %s, %s, %s, 4326)
    AND ST_DWithin(
        way::geography,
        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
        %s
    )
    AND ({_AMENITY_FILTER})
"""

# Polygons: use ST_PointOnSurface to get a representative interior point.
# Bounding-box pre-filter (&&) makes use of the GiST index before the
# exact geography check.  The envelope is computed in Python (_bbox) because
# a degree of longitude is shorter than a degree of latitude away from the
# equator.
# Params: (xmin, ymin, xmax, ymax, lon, lat, radius_m)
_POLYGONS_SQL = f"""
SELECT
    osm_id,
    ST_Y(ST_PointOnSurface(way)) AS lat,
    ST_X(ST_PointOnSurface(way)) AS lon,
    amenity,
    aeroway,
    office,
    shop,
    name,
    tags->'healthcare' AS healthcare,
    tags->'emergency'  AS emergency,
    tags               AS extra_tags
FROM planet_osm_polygon
WHERE
    way && ST_MakeEnvelope(%s, %s, %s, %s, 4326)
    AND ST_DWithin(
        ST_PointOnSurface(way)::geography,
        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
        %s
    )
    AND ({_AMENITY_FILTER})
"""

# Area queries: bounding-box (&&) first, then exact ST_Intersects
_POINTS_AREA_SQL = f"""
SELECT
    osm_id,
    ST_Y(way)          AS lat,
    ST_X(way)          AS lon,
    amenity,
    aeroway,
    office,
    shop,
    name,
    tags->'healthcare' AS healthcare,
    tags->'emergency'  AS emergency,
    tags               AS extra_tags
FROM planet_osm_point
WHERE
    way && ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)
    AND ST_Intersects(way, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))
    AND ({_AMENITY_FILTER})
"""

_POLYGONS_AREA_SQL = f"""
SELECT
    osm_id,
    ST_Y(ST_PointOnSurface(way)) AS lat,
    ST_X(ST_PointOnSurface(way)) AS lon,
    amenity,
    aeroway,
    office,
    shop,
    name,
    tags->'healthcare' AS healthcare,
    tags->'emergency'  AS emergency,
    tags               AS extra_tags
FROM planet_osm_polygon
WHERE
    way && ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)
    AND ST_Intersects(way, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))
    AND ({_AMENITY_FILTER})
"""


# ── row → amenity dict conversion ────────────────────────────────────────────

def _row_to_element(row: dict[str, Any], element_type: str) -> dict:
    """
    Convert a raw DB row into the dict shape that amenity_utils expects.

    We reconstruct the same {type, id, lat, lon, tags} envelope that
    Overpass returns so that classify_element / deduplicate / to_amenity_dict
    all work unchanged on the output of fetch_amenities.
    """
    tags: dict[str, str] = {}

    # Standard columns → tags dict
    for col in ("amenity", "aeroway", "office", "shop"):
        val = row.get(col)
        if val:
            tags[col] = val

    # Healthcare / emergency come from the hstore overflow column
    for col in ("healthcare", "emergency"):
        val = row.get(col)
        if val:
            tags[col] = val

    if row.get("name"):
        tags["name"] = row["name"]

    # Merge any remaining hstore extras (beds=, capacity=, operator=, etc.)
    # psycopg returns hstore as a plain dict when the hstore extension is
    # registered; without registration it comes back as a raw string like
    # '"key"=>"value",...'.  Handle both defensively.
    extra = row.get("extra_tags") or {}
    if isinstance(extra, str):
        extra = _parse_hstore(extra)
    for k, v in extra.items():
        if v and k not in tags:
            tags[k] = v

    lat = float(row["lat"])
    lon = float(row["lon"])

    return {
        # Overpass-compatible envelope
        "type": element_type,          # "node" or "way"
        "id": row["osm_id"],
        "lat": lat,
        "lon": lon,
        "tags": tags,
        # to_amenity_dict reads lat/lon from extract_coords which checks
        # element["type"] == "node"; for ways it falls back to element["center"].
        # We inject a center so ways are handled correctly.
        "center": {"lat": lat, "lon": lon},
    }


# ── helpers ──────────────────────────────────────────────────────────────────

_METRES_PER_DEG_LAT = 111_320.0


def _bbox(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    """
    Return (xmin, ymin, xmax, ymax) in degrees that fully contains a circle of
    radius_m around (lat, lon).  Longitude is widened by 1/cos(lat) so the box
    is not too narrow east-west; a 2% margin absorbs geodesic-vs-planar error.
    """
    margin = 1.02
    dlat = radius_m / _METRES_PER_DEG_LAT * margin
    cos_lat = max(math.cos(math.radians(lat)), 0.01)   # guard near the poles
    dlon = radius_m / (_METRES_PER_DEG_LAT * cos_lat) * margin
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def _postprocess(point_rows: list, poly_rows: list) -> list[dict]:
    """Row conversion + dedup + classification.  CPU-bound: run in a thread."""
    elements: list[dict] = []
    for row in point_rows:
        elements.append(_row_to_element(row, "node"))
    for row in poly_rows:
        elements.append(_row_to_element(row, "way"))
    deduped = deduplicate(elements)
    return [a for el in deduped if (a := to_amenity_dict(el)) is not None]


# ── connection pool ──────────────────────────────────────────────────────────

_pool: AsyncConnectionPool | None = None
_data_as_of: str | None = None
_data_as_of_ts: float = 0.0
_DATA_AS_OF_TTL_SECONDS = 3600.0

# How long (seconds) a single libpq connection attempt is allowed to run.
# Without this, a down DB causes the OS TCP timeout (~2 min) on each attempt,
# making every test that lazily touches the pool hang for 2+ minutes.
# 5 s is long enough for a healthy local container; short enough to fail fast.
_POOL_CONNECT_TIMEOUT = 5


async def _configure_connection(conn: psycopg.AsyncConnection) -> None:
    """Configures each connection upon creation: sets dict_row and registers hstore."""
    conn.row_factory = psycopg.rows.dict_row
    hstore_info = await TypeInfo.fetch(conn, "hstore")
    if hstore_info:
        register_hstore(hstore_info, conn)


async def init_pool() -> AsyncConnectionPool:
    """Initialize and open the database connection pool."""
    global _pool
    if _pool is None:
        # Merge connect_timeout into the DSN so that each individual libpq
        # connection attempt aborts after _POOL_CONNECT_TIMEOUT seconds.
        # This prevents a down DB from hanging the pool open() call (and by
        # extension, any test that lazily triggers pool creation) for the full
        # OS TCP timeout (~2 minutes).
        conninfo = make_conninfo(config.DB_DSN, connect_timeout=_POOL_CONNECT_TIMEOUT)
        _pool = AsyncConnectionPool(
            conninfo=conninfo,
            min_size=1,
            max_size=10,
            # Set default pool acquisition timeout to 5.0 seconds so all pool.connection()
            # callers fail fast with PoolTimeout instead of waiting for the 30s default
            # when the database container is unreachable or down.
            timeout=5.0,
            open=False,
            configure=_configure_connection,
        )
        await _pool.open()
    return _pool


async def close_pool() -> None:
    """Close the database connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def get_pool() -> AsyncConnectionPool:
    """Get active connection pool, initializing lazily if not already open."""
    global _pool
    if _pool is None:
        return await init_pool()
    return _pool


async def check_db_health(timeout_seconds: float = 2.0) -> bool:
    """Return True if a test query against PostGIS succeeds, False otherwise."""
    try:
        pool = await get_pool()
        async with pool.connection(timeout=timeout_seconds) as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1;")
                return True
    except Exception as exc:
        logger.warning("Database health check failed: %s", exc)
        return False


def get_cached_data_as_of() -> str:
    """Last known snapshot date without touching the DB (safe when DB is down)."""
    return _data_as_of or config.DATA_AS_OF


async def get_data_as_of() -> str:
    """
    Return data snapshot date from osm2pgsql_properties table if present,
    otherwise fall back to config.DATA_AS_OF.

    The value is cached for an hour (so a monthly refresh is picked up without
    a restart).  A transient DB failure is NOT cached as the fallback value.
    """
    global _data_as_of, _data_as_of_ts
    if _data_as_of is not None and (time.monotonic() - _data_as_of_ts) < _DATA_AS_OF_TTL_SECONDS:
        return _data_as_of
    try:
        pool = await get_pool()
        async with pool.connection(timeout=3.0) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT value FROM osm2pgsql_properties "
                    "WHERE property IN ('replication_timestamp', 'import_timestamp') "
                    "ORDER BY property DESC LIMIT 1;"
                )
                row = await cur.fetchone()
                if row and row["value"]:
                    _data_as_of = str(row["value"]).strip().split("T")[0]
                    _data_as_of_ts = time.monotonic()
                    return _data_as_of
    except psycopg.errors.UndefinedTable:
        # Table genuinely absent (legacy import) -> the fallback is the answer.
        logger.debug("osm2pgsql_properties not present; using configured DATA_AS_OF")
        _data_as_of = config.DATA_AS_OF
        _data_as_of_ts = time.monotonic()
        return _data_as_of
    except Exception as e:
        # Transient failure: answer with the best known value but retry next time.
        logger.debug("Could not read osm2pgsql_properties: %s", e)
        return get_cached_data_as_of()
    _data_as_of = config.DATA_AS_OF
    _data_as_of_ts = time.monotonic()
    return _data_as_of


# ── main public function ──────────────────────────────────────────────────────

async def fetch_amenities(lat: float, lon: float, radius_m: int) -> list[dict]:
    """
    Fetch, clean and categorise all disaster-relevant amenities near a point.

    Returns a list of amenity dicts (see amenity_utils.to_amenity_dict).
    Raises DBError if the database query fails.
    """
    try:
        xmin, ymin, xmax, ymax = _bbox(lat, lon, radius_m)
        pool = await get_pool()
        async with pool.connection() as conn:
            # Points
            async with conn.cursor() as cur:
                await cur.execute(
                    _POINTS_SQL,
                    (xmin, ymin, xmax, ymax, lon, lat, radius_m),
                )
                point_rows = await cur.fetchall()

            # Polygons
            async with conn.cursor() as cur:
                await cur.execute(
                    _POLYGONS_SQL,
                    (xmin, ymin, xmax, ymax, lon, lat, radius_m),
                )
                poly_rows = await cur.fetchall()

    except Exception as exc:
        logger.error("Database error: %s", exc)
        raise DBError(f"PostGIS query failed: {exc}") from exc

    logger.info(
        "DB returned %d points + %d polygons before dedup",
        len(point_rows), len(poly_rows),
    )

    # Dedup + classify off the event loop (pure-Python, can be heavy).
    amenities = await asyncio.to_thread(_postprocess, point_rows, poly_rows)

    logger.info(
        "After dedup: %d amenities (%d raw rows)",
        len(amenities), len(point_rows) + len(poly_rows),
    )
    return amenities


async def fetch_amenities_area(geojson_geom_str: str, timeout_seconds: float = 10.0) -> list[dict]:
    """
    Fetch, clean and categorise all disaster-relevant amenities intersecting a GeoJSON area.
    Applies a statement timeout (default 10s) to avoid DB locks from large
    polygons.  The timeout is transaction-scoped (set_config(..., is_local=true))
    so it can never leak onto the pooled connection's next user.
    """
    timeout_ms = int(timeout_seconds * 1000)
    try:
        pool = await get_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT set_config('statement_timeout', %s, true);",
                    (str(timeout_ms),),
                )
                # Points
                await cur.execute(_POINTS_AREA_SQL, (geojson_geom_str, geojson_geom_str))
                point_rows = await cur.fetchall()

                # Polygons
                await cur.execute(_POLYGONS_AREA_SQL, (geojson_geom_str, geojson_geom_str))
                poly_rows = await cur.fetchall()

    except psycopg.errors.QueryCanceled as exc:
        logger.error("Database statement timeout in area query: %s", exc)
        raise TimeoutError("Area query timed out") from exc
    except Exception as exc:
        if getattr(exc, "sqlstate", None) == "57014" or "timeout" in str(exc).lower():
            logger.error("Database timeout in area query: %s", exc)
            raise TimeoutError("Area query timed out") from exc
        logger.error("Database error in area query: %s", exc)
        raise DBError(f"PostGIS area query failed: {exc}") from exc

    logger.info(
        "Area DB returned %d points + %d polygons before dedup",
        len(point_rows), len(poly_rows),
    )

    return await asyncio.to_thread(_postprocess, point_rows, poly_rows)
