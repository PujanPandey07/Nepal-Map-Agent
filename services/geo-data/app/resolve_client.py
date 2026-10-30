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
import logging
from typing import Optional

import httpx

from app.admin_matcher import match_admin_unit
from app.curated_destinations import match_destination

logger = logging.getLogger("geo-data.resolve")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {
    "User-Agent": "nepal-map-agent/0.1 (disaster+tourism assistant; github:PujanPandey07)"
}

_AMBIGUITY_GAP = 0.15

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
    Returns raw Nominatim JSON list.
    """
    params = {
        "q": q,
        "countrycodes": "np",
        "format": "json",
        "limit": limit,
        "addressdetails": 1,
        "extratags": 1,
        "namedetails": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
            resp = await client.get(NOMINATIM_URL, params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error("Nominatim status %s: %s", e.response.status_code, e.response.text[:200])
        raise ResolveError(f"Nominatim returned status {e.response.status_code}") from e
    except httpx.RequestError as e:
        logger.error("Could not reach Nominatim: %r", e)
        raise ResolveError("Could not reach Nominatim") from e


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
    dest = match_destination(clean_q)
    if dest:
        candidate = {
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
        return {
            "query": q,
            "ambiguous": False,
            "best_match": candidate,
            "candidates": [candidate],
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
