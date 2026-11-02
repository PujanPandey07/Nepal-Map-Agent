"""
Tiered place-name resolver for Nepal:

Tier 1: Curated popular destinations & aliases (instant, offline)
        - Prevents "Boudhanath" resolving to a restaurant
        - Resolves "EBC", "ABC", "KTM", "PKR"
        - Points "Lumbini" to the sacred birthplace garden rather than the province
Tier 2: Administrative matching via tlf-geo + boundary centroids (instant, offline)
        - Covers all 7 Provinces, 77 Districts, 753 Local Levels
        - 100% offline, zero latency, no rate limits
Tier 3: Live Nominatim (OSM geocoder) with category reranking
        - Reranks results so natural/place/boundaries beat amenities/shops/restaurants.
"""
import asyncio
import logging
import time
from typing import Optional

import httpx

from app.admin_matcher import match_admin_unit
from app.curated_destinations import match_destination, match_destinations

logger = logging.getLogger("geo-data.resolve")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {
    "User-Agent": "nepal-map-agent/0.1 (disaster+tourism assistant; github:PujanPandey07)"
}

NOMINATIM_TIMEOUT = 5.0        # Hard timeout in seconds
NOMINATIM_CACHE_TTL = 600.0    # 10 minutes cache TTL
_AMBIGUITY_GAP = 0.15

# In-memory TTL cache: normalized_query -> (monotonic_timestamp, results)
_nominatim_cache: dict[str, tuple[float, list[dict]]] = {}
_cache_lock = asyncio.Lock()


class NominatimRateLimiter:
    """Enforces at most 1 request per second to respect OSM Nominatim usage policy."""

    def __init__(self, min_interval: float = 1.0):
        self.min_interval = min_interval
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self.min_interval:
                await asyncio.sleep(self.min_interval - elapsed)
            self._last_call = time.monotonic()


_rate_limiter = NominatimRateLimiter()

# OSM classes in priority order: geography / landmarks > commercial services
CLASS_PRIORITY = {
    "boundary": 10,
    "place": 9,
    "natural": 8,
    "historic": 7,
    "tourism": 6,
    "leisure": 5,
    "landuse": 4,
    "amenity": 2,
    "shop": 1,
}


class ResolveError(Exception):
    """Raised when Nominatim is unreachable or returns an error status."""


async def resolve_place(q: str, limit: int = 5) -> list[dict]:
    """
    Query Nominatim for *q*, restricted to Nepal.
    Enforces a hard timeout, in-process rate limiting (1 req/sec),
    and in-memory caching.
    """
    cache_key = q.strip().lower()

    # 1. Cache lookup
    async with _cache_lock:
        entry = _nominatim_cache.get(cache_key)
        if entry is not None:
            ts, cached_data = entry
            if time.monotonic() - ts < NOMINATIM_CACHE_TTL:
                logger.debug("Nominatim cache hit for: %s", cache_key)
                return cached_data
            del _nominatim_cache[cache_key]

    # 2. Rate limiter
    await _rate_limiter.acquire()

    # 3. HTTP Request with hard timeout
    params = {
        "q": q,
        "countrycodes": "np",
        "format": "json",
        "limit": limit,
        "addressdetails": 1,
        "extratags": 1,
        "namedetails": 1,
    }
    timeout_cfg = httpx.Timeout(NOMINATIM_TIMEOUT, connect=2.0)
    try:
        async with httpx.AsyncClient(timeout=timeout_cfg, headers=HEADERS) as client:
            resp = await client.get(NOMINATIM_URL, params=params)
            if resp.status_code == 429:
                logger.warning("Nominatim rate limit reached (HTTP 429)")
                raise ResolveError("Geocoding rate limit reached; please retry later")
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException as e:
        logger.error("Nominatim request timed out: %r", e)
        raise ResolveError("Geocoding service timed out") from e
    except httpx.HTTPStatusError as e:
        logger.error("Nominatim status %s: %s", e.response.status_code, e.response.text[:200])
        raise ResolveError("Geocoding service unavailable") from e
    except httpx.RequestError as e:
        logger.error("Could not reach Nominatim: %r", e)
        raise ResolveError("Could not reach Nominatim") from e

    # 4. Cache update
    async with _cache_lock:
        _nominatim_cache[cache_key] = (time.monotonic(), data)

    return data


def parse_candidates(raw: list[dict]) -> list[dict]:
    """
    Convert raw Nominatim results into candidate shapes and rerank them:
    Prioritize geographic entities (boundary, place, natural) over businesses/restaurants.
    """
    candidates = []
    for r in raw:
        addr = r.get("address", {})
        name_details = r.get("namedetails", {})
        short_name = (
            name_details.get("name:en")
            or name_details.get("name")
            or r.get("name", "")
        )
        candidates.append({
            "name": r.get("display_name", ""),
            "short_name": short_name,
            "type": r.get("type", ""),
            "osm_class": r.get("class", ""),
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "importance": float(r.get("importance", 0.0)),
            "source": "nominatim",
            "address": {
                k: v for k, v in addr.items()
                if k in ("state", "district", "city", "town",
                         "village", "suburb", "road", "postcode", "country")
            },
        })

    # Secondary sort: class priority then importance score
    def _rank_key(c):
        priority = CLASS_PRIORITY.get(c.get("osm_class", ""), 3)
        return (c["importance"], priority)

    candidates.sort(key=_rank_key, reverse=True)
    return candidates


def pick_best(candidates: list[dict]) -> tuple[Optional[dict], bool]:
    """
    Choose best candidate or flag ambiguous.
    """
    if not candidates:
        return None, False
    if len(candidates) == 1:
        return candidates[0], False

    top, second = candidates[0], candidates[1]
    gap = top["importance"] - second["importance"]
    if gap > _AMBIGUITY_GAP:
        return top, False

    return top, True


async def resolve_query(q: str) -> dict:
    """
    Full 3-tier resolution pipeline:
      1. Curated Destinations & Aliases
      2. Administrative Geography (tlf-geo + boundaries)
      3. Nominatim with type reranking
    """
    clean_q = q.strip()

    # ── Tier 1: Curated Famous Destinations ──────────────────────────────────
    dests = match_destinations(clean_q)
    if dests:
        candidates = [
            {
                "name": f"{dest['short_name']}, {dest.get('district', '')}, Nepal".strip(", "),
                "short_name": dest["short_name"],
                "type": dest["type"],
                "osm_class": "curated",
                "lat": dest["lat"],
                "lon": dest["lon"],
                "importance": 1.0,
                "source": "curated",
                "address": {
                    "district": dest.get("district"),
                    "country": "Nepal",
                },
            }
            for dest in dests
        ]
        is_ambiguous = len(candidates) > 1
        return {
            "query": q,
            "ambiguous": is_ambiguous,
            "best_match": candidates[0],
            "candidates": candidates,
        }

    # ── Tier 2: Administrative Geography (tlf-geo + boundaries) ──────────────
    admin = match_admin_unit(clean_q)
    if admin:
        return {
            "query": q,
            "ambiguous": False,
            "best_match": admin,
            "candidates": [admin],
        }

    # ── Tier 3: Nominatim Live Geocoding ─────────────────────────────────────
    raw = await resolve_place(clean_q)
    candidates = parse_candidates(raw)
    best, ambiguous = pick_best(candidates)

    return {
        "query": q,
        "ambiguous": ambiguous,
        "best_match": best,
        "candidates": candidates,
    }
