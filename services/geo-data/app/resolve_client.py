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
        - Among results of similar importance, prefers natural/place/boundary
          entities over shops/restaurants (health and emergency amenities are
          lifted to the same level as tourism, since this is a disaster tool).
"""
import asyncio
import logging
import math
import time
from typing import Any, Optional

import httpx
import pandas as pd
from tlf_geo_profiler import profile_dict

from app.admin_matcher import match_admin_units
from app.curated_destinations import match_destination, match_destinations

logger = logging.getLogger("geo-data.resolve")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {
    "User-Agent": "nepal-map-agent/0.1 (disaster+tourism assistant; github:PujanPandey07)"
}

NOMINATIM_TIMEOUT = 5.0        # Hard timeout in seconds
NOMINATIM_CACHE_TTL = 600.0    # 10 minutes cache TTL
NOMINATIM_CACHE_MAX = 512      # bound memory: user-supplied queries are the keys
_AMBIGUITY_GAP = 0.15
_IMPORTANCE_BIN = 0.05         # importance values closer than this count as "similar"

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

# amenity types that matter for disaster response: ranked like tourism rather
# than like a generic shop or restaurant.
DISASTER_AMENITY_TYPES = {
    "hospital", "clinic", "police", "fire_station", "ambulance_station",
    "shelter", "community_centre", "school", "college", "university",
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

    # 4. Cache update (bounded: purge expired, then evict oldest if still full)
    async with _cache_lock:
        now = time.monotonic()
        if len(_nominatim_cache) >= NOMINATIM_CACHE_MAX:
            for k in [k for k, (ts, _) in _nominatim_cache.items()
                      if now - ts >= NOMINATIM_CACHE_TTL]:
                del _nominatim_cache[k]
            while len(_nominatim_cache) >= NOMINATIM_CACHE_MAX:
                oldest = min(_nominatim_cache, key=lambda k: _nominatim_cache[k][0])
                del _nominatim_cache[oldest]
        _nominatim_cache[cache_key] = (now, data)

    return data


def _sanitize_val(v: Any) -> Any:
    """Safely return None if v is None, NaN or empty."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _clean_str(v: Any) -> Optional[str]:
    v = _sanitize_val(v)
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _clean_int(v: Any) -> Optional[int]:
    v = _sanitize_val(v)
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def enrich_candidate(cand: dict) -> dict:
    """
    Enrich a candidate dict with admin context from the offline profiler:
    province, district, local_level, local_level_type, ward.
    None-safe: NaN/missing values become None (never JSON NaN).
    """
    cand.setdefault("province", None)
    cand.setdefault("district", None)
    cand.setdefault("local_level", None)
    cand.setdefault("local_level_type", None)
    cand.setdefault("ward", None)

    # Sanitize address dictionary
    if "address" in cand and isinstance(cand["address"], dict):
        cand["address"] = {
            k: _clean_str(v)
            for k, v in cand["address"].items()
        }

    lat = cand.get("lat")
    lon = cand.get("lon")
    if lat is None or lon is None:
        return cand

    try:
        f_lat = float(lat)
        f_lon = float(lon)
        prof = profile_dict(f_lat, f_lon)
        if prof and isinstance(prof, dict):
            admin = prof.get("admin_unit") or {}
            cand["province"] = _clean_str(admin.get("province")) or cand.get("province")
            cand["district"] = _clean_str(admin.get("district")) or cand.get("district")
            cand["local_level"] = _clean_str(admin.get("local_level")) or cand.get("local_level")
            cand["local_level_type"] = _clean_str(admin.get("local_level_type")) or cand.get("local_level_type")
            cand["ward"] = _clean_int(admin.get("ward")) if cand.get("ward") is None else cand.get("ward")
    except Exception as exc:
        logger.debug("Profile enrichment error for (%s, %s): %s", lat, lon, exc)

    return cand


def _enrich_all(cands: list[dict]) -> list[dict]:
    """Enrich several candidates (CPU-bound; call via asyncio.to_thread)."""
    return [enrich_candidate(c) for c in cands]


def make_candidate_label(cand: dict) -> str:
    """
    Construct a human-readable disambiguation label,
    e.g. "Basantapur, Rapti rural municipality, Dang".
    """
    name = cand.get("short_name") or cand.get("name") or "Unknown"
    parts = [str(name).strip()]
    loc = cand.get("local_level")
    loc_type = cand.get("local_level_type")
    dist = cand.get("district") or (cand.get("address") or {}).get("district")

    if loc:
        loc_str = str(loc).strip()
        if loc_type:
            loc_type_str = str(loc_type).replace("_", " ").strip()
            if loc_type_str.lower() not in loc_str.lower():
                parts.append(f"{loc_str} {loc_type_str}")
            else:
                parts.append(loc_str)
        else:
            parts.append(loc_str)

    if dist:
        dist_str = str(dist).strip()
        if dist_str.lower() not in parts[0].lower() and (len(parts) < 2 or dist_str.lower() not in parts[1].lower()):
            parts.append(dist_str)

    return ", ".join(parts)


def parse_candidates(raw: list[dict]) -> list[dict]:
    """
    Convert raw Nominatim results into candidate shapes and rerank them:
    Prioritize geographic entities (boundary, place, natural) over businesses/restaurants.
    """
    candidates = []
    for r in raw:
        try:
            f_lat = float(r["lat"])
            f_lon = float(r["lon"])
            f_importance = float(r.get("importance", 0.0))
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping malformed Nominatim result: %.200r", r)
            continue
        addr = r.get("address", {}) or {}
        name_details = r.get("namedetails", {}) or {}
        short_name = (
            _clean_str(name_details.get("name:en"))
            or _clean_str(name_details.get("name"))
            or _clean_str(r.get("name"))
            or ""
        )
        candidates.append({
            "name": _clean_str(r.get("display_name")) or "",
            "short_name": short_name,
            "type": _clean_str(r.get("type")) or "",
            "osm_class": _clean_str(r.get("class")) or "",
            "lat": f_lat,
            "lon": f_lon,
            "importance": f_importance,
            "source": "nominatim",
            "address": {
                k: _clean_str(v)
                for k, v in addr.items()
                if k in ("state", "district", "city", "town",
                         "village", "suburb", "road", "postcode", "country")
            },
        })

    # Rank: bucket importance into coarse bins, then class priority inside a
    # bin, then exact importance.  (The old key was (importance, priority), so
    # priority only mattered for exactly equal floats and reranking never
    # happened.)  Results whose importance differs by more than one bin keep
    # their Nominatim order.
    def _rank_key(c):
        osm_class = c.get("osm_class", "")
        priority = CLASS_PRIORITY.get(osm_class, 3)
        if osm_class == "amenity" and c.get("type") in DISASTER_AMENITY_TYPES:
            priority = max(priority, CLASS_PRIORITY["tourism"])
        return (math.floor(c["importance"] / _IMPORTANCE_BIN), priority, c["importance"])

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
        raw_candidates = [
            ({
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
            })
            for dest in dests
        ]
        candidates = await asyncio.to_thread(_enrich_all, raw_candidates)
        is_ambiguous = len(candidates) > 1
        options = (
            [
                {
                    "id": str(idx + 1),
                    "label": make_candidate_label(c),
                    "lat": c["lat"],
                    "lon": c["lon"],
                }
                for idx, c in enumerate(candidates)
            ]
            if is_ambiguous
            else []
        )
        return {
            "query": q,
            "ambiguous": is_ambiguous,
            "best_match": None if is_ambiguous else candidates[0],
            "candidates": candidates,
            "clarification_needed": is_ambiguous,
            "options": options,
        }

    # ── Tier 2: Administrative Geography (tlf-geo + boundaries) ──────────────
    # (pandas + polygon work: keep it off the event loop)
    admins = await asyncio.to_thread(match_admin_units, clean_q)
    if admins:
        candidates = await asyncio.to_thread(_enrich_all, admins)
        # Same name at several levels (e.g. a district and a municipality):
        # ask the user instead of silently picking one.
        is_ambiguous = len(candidates) > 1
        options = (
            [
                {
                    "id": str(idx + 1),
                    "label": f"{c.get('short_name')} ({c.get('type')})",
                    "lat": c["lat"],
                    "lon": c["lon"],
                }
                for idx, c in enumerate(candidates)
            ]
            if is_ambiguous
            else []
        )
        return {
            "query": q,
            "ambiguous": is_ambiguous,
            "best_match": None if is_ambiguous else candidates[0],
            "candidates": candidates,
            "clarification_needed": is_ambiguous,
            "options": options,
        }

    # ── Tier 3: Nominatim Live Geocoding ─────────────────────────────────────
    raw = await resolve_place(clean_q)
    candidates = await asyncio.to_thread(_enrich_all, parse_candidates(raw))
    best, ambiguous = pick_best(candidates)

    options = (
        [
            {
                "id": str(idx + 1),
                "label": make_candidate_label(c),
                "lat": c["lat"],
                "lon": c["lon"],
            }
            for idx, c in enumerate(candidates)
        ]
        if ambiguous
        else []
    )

    return {
        "query": q,
        "ambiguous": ambiguous,
        "best_match": None if ambiguous else best,
        "candidates": candidates,
        "clarification_needed": ambiguous,
        "options": options,
    }
