"""
Async Overpass client with:
  - all disaster-relevant amenity categories (not just health)
  - multi-server failover: tries each server in turn, moves on after timeout
  - simple in-memory TTL cache (10 min) keyed on rounded coordinates
  - deduplication and category classification via amenity_utils

External callers only need:
    from app.osm_client import OverpassError, fetch_amenities
"""
import logging
import time

import httpx

from app.amenity_utils import deduplicate, to_amenity_dict

logger = logging.getLogger("geo-data.osm")

HEADERS = {
    "User-Agent": "nepal-map-agent/0.1 (disaster+tourism assistant; github:PujanPandey07)"
}

# Three public Overpass servers tried in order. If one times out or errors,
# the next is attempted. Order chosen by observed reliability from Nepal.
OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# Each server gets this many seconds before we give up on it and try the next.
PER_SERVER_TIMEOUT = 35


class OverpassError(Exception):
    """Raised when every Overpass server fails."""


# ── in-memory TTL cache ───────────────────────────────────────────────────────

_cache: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 600  # 10 minutes; amenity data changes slowly


def _cache_key(lat: float, lon: float, radius_m: int) -> str:
    # Round to 4 decimal places (~11 m) so nearby repeated queries share a slot
    return f"{lat:.4f}:{lon:.4f}:{radius_m}"


def _cache_get(key: str) -> list[dict] | None:
    entry = _cache.get(key)
    if entry is not None:
        ts, value = entry
        if time.time() - ts < _CACHE_TTL:
            return value
        del _cache[key]
    return None


def _cache_set(key: str, value: list[dict]) -> None:
    _cache[key] = (time.time(), value)


# ── Overpass query ────────────────────────────────────────────────────────────

def build_amenities_query(lat: float, lon: float, radius_m: int) -> str:
    """
    Build an OverpassQL query for all disaster-relevant amenities.

    Categories covered:
      health            hospitals, clinics, pharmacies, health posts, ...
      emergency         police, fire stations, ambulance stations
      evacuation        schools, community centres, shelters, places of worship
      infrastructure    helipads (critical in Nepal), fuel stations
      food_and_water    supermarkets, water points
      government        government / administrative offices, town halls
    """
    r, la, lo = radius_m, lat, lon
    return f"""
[out:json][timeout:30];
(
  nwr["amenity"~"^(hospital|clinic|doctors|pharmacy|dentist|health_post|nursing_home|birthing_centre)$"](around:{r},{la},{lo});
  nwr["healthcare"](around:{r},{la},{lo});
  nwr["amenity"~"^(police|fire_station|ambulance_station)$"](around:{r},{la},{lo});
  nwr["amenity"~"^(school|college|university|community_centre|social_facility|shelter|place_of_worship)$"](around:{r},{la},{lo});
  nwr["aeroway"="helipad"](around:{r},{la},{lo});
  nwr["amenity"~"^(fuel|water_point)$"](around:{r},{la},{lo});
  nwr["shop"~"^(supermarket|convenience|general)$"](around:{r},{la},{lo});
  nwr["office"~"^(government|administrative)$"](around:{r},{la},{lo});
  nwr["amenity"="townhall"](around:{r},{la},{lo});
);
out center tags;
"""


# ── main public function ──────────────────────────────────────────────────────

async def fetch_amenities(lat: float, lon: float, radius_m: int) -> list[dict]:
    """
    Fetch, clean and categorise all disaster-relevant amenities near a point.

    Returns a list of amenity dicts (see amenity_utils.to_amenity_dict).
    Results are cached for 10 minutes.
    Raises OverpassError if every server fails.
    """
    key = _cache_key(lat, lon, radius_m)
    cached = _cache_get(key)
    if cached is not None:
        logger.info("Cache hit for key %s", key)
        return cached

    query = build_amenities_query(lat, lon, radius_m)
    last_error: Exception | None = None

    for server_url in OVERPASS_SERVERS:
        try:
            logger.info("Querying Overpass server: %s", server_url)
            async with httpx.AsyncClient(
                timeout=PER_SERVER_TIMEOUT, headers=HEADERS
            ) as client:
                resp = await client.post(server_url, data={"data": query})
                resp.raise_for_status()
                raw_elements = resp.json()["elements"]

            deduped = deduplicate(raw_elements)
            amenities = [
                a for el in deduped if (a := to_amenity_dict(el)) is not None
            ]
            _cache_set(key, amenities)
            logger.info(
                "Fetched %d amenities (%d raw, %d after dedup) from %s",
                len(amenities), len(raw_elements), len(deduped), server_url,
            )
            return amenities

        except httpx.HTTPStatusError as e:
            logger.warning(
                "Overpass %s → HTTP %s: %s",
                server_url, e.response.status_code, e.response.text[:200],
            )
            last_error = e
        except httpx.RequestError as e:
            logger.warning("Overpass %s → request error: %r", server_url, e)
            last_error = e

    raise OverpassError(
        f"All Overpass servers failed. Last error: {last_error!r}"
    ) from last_error
