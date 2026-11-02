# services/geo-data/app/db_client.py
"""
PostGIS database client for the geo-data service.

Replaces the live Overpass API with a local PostGIS database backed by
a downloaded Nepal OSM .pbf file (osm2pgsql --hstore import).

Public API (identical contract to osm_client.py):
    fetch_amenities(lat, lon, radius_m) -> list[dict]
    DBError                             (exception)

Each returned dict has the same shape as an Overpass element processed
through amenity_utils.to_amenity_dict():
    {name, type, category, lat, lon, tags}

This means amenity_utils.classify_element and deduplicate work unchanged.
"""
import logging
import re
from typing import Any

import psycopg
import psycopg.errors
from psycopg.types import TypeInfo
from psycopg.types.hstore import register_hstore
from psycopg_pool import AsyncConnectionPool

from app import config
from app.amenity_utils import classify_element, deduplicate, to_amenity_dict

logger = logging.getLogger("geo-data.db")

_HSTORE_RE = re.compile(r'"((?:[^"\\]|\\.)*)"\s*=>\s*(?:NULL|"((?:[^"\\]|\\.)*)")')


def _parse_hstore(text: str) -> dict[str, str]:
    """Parse raw hstore string representation as fallback."""
    if not text:
        return {}
    res: dict[str, str] = {}
    for m in _HSTORE_RE.finditer(text):
        k = m.group(1).replace(r'\"', '"').replace(r'\\', '\\')
        v = m.group(2)
        if v is not None:
            res[k] = v.replace(r'\"', '"').replace(r'\\', '\\')
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
# Filter: at least one relevant tag must be non-null.  Matching mirrors
# the Overpass query categories in osm_client.build_amenities_query().

_AMENITY_FILTER = """
    amenity IN (
        'hospital','clinic','doctors','pharmacy','dentist',
        'health_post','nursing_home','birthing_centre',
        'police','fire_station','ambulance_station',
        'school','college','university','community_centre',
        'social_facility','shelter','place_of_worship',
        'fuel','water_point','townhall'
    )
    OR (tags->'healthcare') IS NOT NULL
    OR (tags->'emergency')  IS NOT NULL
    OR aeroway = 'helipad'
    OR shop  IN ('supermarket','convenience','general')
    OR office IN ('government','administrative')
"""

# Points: way is geometry(Point,4326) — no transform needed.
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
    ST_DWithin(
        way::geography,
        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
        %s
    )
    AND ({_AMENITY_FILTER})
"""

# Polygons: use ST_PointOnSurface to get a representative interior point.
# Bounding-box pre-filter (&&) makes use of the GiST index before the
# exact geography check.
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
    way && ST_Expand(
        ST_SetSRID(ST_MakePoint(%s, %s), 4326),
        %s / 111320.0          -- rough degree-equivalent of radius_m
    )
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


# ── connection pool ──────────────────────────────────────────────────────────

_pool: AsyncConnectionPool | None = None
_data_as_of: str | None = None


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
        _pool = AsyncConnectionPool(
            conninfo=config.DB_DSN,
            min_size=1,
            max_size=10,
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


async def get_data_as_of() -> str:
    """
    Return data snapshot date from osm2pgsql_properties table if present,
    otherwise fallback to config.DATA_AS_OF.
    """
    global _data_as_of
    if _data_as_of is not None:
        return _data_as_of
    try:
        pool = await get_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT value FROM osm2pgsql_properties WHERE property = 'replication_timestamp' OR property = 'import_timestamp' ORDER BY property DESC LIMIT 1;"
                )
                row = await cur.fetchone()
                if row and row[0]:
                    val = str(row[0]).strip()
                    date_part = val.split("T")[0]
                    _data_as_of = date_part
                    return _data_as_of
    except Exception as e:
        logger.debug("Could not read osm2pgsql_properties: %s", e)
    _data_as_of = config.DATA_AS_OF
    return _data_as_of


# ── main public function ──────────────────────────────────────────────────────

async def fetch_amenities(lat: float, lon: float, radius_m: int) -> list[dict]:
    """
    Fetch, clean and categorise all disaster-relevant amenities near a point.

    Returns a list of amenity dicts (see amenity_utils.to_amenity_dict).
    Raises DBError if the database query fails.
    """
    try:
        pool = await get_pool()
        async with pool.connection() as conn:
            # Points
            async with conn.cursor() as cur:
                await cur.execute(_POINTS_SQL, (lon, lat, radius_m))
                point_rows = await cur.fetchall()

            # Polygons
            async with conn.cursor() as cur:
                await cur.execute(
                    _POLYGONS_SQL,
                    (lon, lat, radius_m, lon, lat, radius_m),
                )
                poly_rows = await cur.fetchall()

    except (psycopg.Error, Exception) as exc:
        logger.error("Database error: %s", exc)
        raise DBError(f"PostGIS query failed: {exc}") from exc

    # Convert to Overpass-compatible elements
    elements: list[dict] = []
    for row in point_rows:
        elements.append(_row_to_element(row, "node"))
    for row in poly_rows:
        elements.append(_row_to_element(row, "way"))

    logger.info(
        "DB returned %d points + %d polygons before dedup",
        len(point_rows), len(poly_rows),
    )

    # Dedup and classify — identical pipeline as the Overpass path
    deduped = deduplicate(elements)
    amenities = [a for el in deduped if (a := to_amenity_dict(el)) is not None]

    logger.info(
        "After dedup: %d amenities (%d raw elements)",
        len(amenities), len(elements),
    )
    return amenities


async def fetch_amenities_area(geojson_geom_str: str, timeout_seconds: float = 10.0) -> list[dict]:
    """
    Fetch, clean and categorise all disaster-relevant amenities intersecting a GeoJSON area.
    Sets a 10s statement timeout to avoid DB locks from large polygons.
    """
    timeout_ms = int(timeout_seconds * 1000)
    try:
        pool = await get_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"SET statement_timeout = {timeout_ms};")
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

    elements: list[dict] = []
    for row in point_rows:
        elements.append(_row_to_element(row, "node"))
    for row in poly_rows:
        elements.append(_row_to_element(row, "way"))

    logger.info(
        "Area DB returned %d points + %d polygons before dedup",
        len(point_rows), len(poly_rows),
    )

    deduped = deduplicate(elements)
    amenities = [a for el in deduped if (a := to_amenity_dict(el)) is not None]
    return amenities
