"""
Amenity classification and deduplication utilities.

All logic for turning raw Overpass elements into structured, categorised
amenity lists lives here. Keeping it separate means it can be tested
without starting the full FastAPI app.
"""
from math import atan2, cos, radians, sin, sqrt
from typing import Optional

# ── category membership sets ─────────────────────────────────────────────────

HEALTH_AMENITIES = {
    "hospital", "clinic", "doctors", "pharmacy", "dentist",
    "health_post", "nursing_home", "birthing_centre",
}

EMERGENCY_AMENITIES = {
    "police", "fire_station", "ambulance_station",
}

EVACUATION_AMENITIES = {
    "school", "college", "university",
    "community_centre", "social_facility",
    "shelter", "place_of_worship",
}

# helipads are aeroway=helipad, not amenity= ; handled separately in classify
INFRASTRUCTURE_AMENITIES = {
    "fuel", "water_point",
}

FOOD_WATER_SHOPS = {
    "supermarket", "convenience", "general",
}

GOVERNMENT_OFFICES = {
    "government", "administrative",
}


def classify_element(tags: dict) -> str:
    """Return the category string for a set of OSM tags."""
    amenity   = tags.get("amenity", "")
    healthcare = tags.get("healthcare", "")
    aeroway   = tags.get("aeroway", "")
    office    = tags.get("office", "")
    shop      = tags.get("shop", "")

    if amenity in HEALTH_AMENITIES or (healthcare and healthcare != "no"):
        return "health"
    if amenity in EMERGENCY_AMENITIES:
        return "emergency_services"
    if amenity in EVACUATION_AMENITIES:
        return "evacuation_sites"
    if aeroway == "helipad" or amenity in INFRASTRUCTURE_AMENITIES:
        return "infrastructure"
    if shop in FOOD_WATER_SHOPS:
        return "food_and_water"
    if office in GOVERNMENT_OFFICES or amenity == "townhall":
        return "government"
    return "other"


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in metres."""
    R = 6_371_000.0
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = radians(lat2 - lat1)
    d_lam = radians(lon2 - lon1)
    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lam / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def extract_coords(element: dict) -> Optional[tuple[float, float]]:
    """
    Pull (lat, lon) from an Overpass element.
    Nodes carry lat/lon directly; ways and relations carry a center object
    when queried with 'out center'.
    """
    if element.get("type") == "node":
        if "lat" in element and "lon" in element:
            return element["lat"], element["lon"]
    center = element.get("center")
    if center and "lat" in center and "lon" in center:
        return center["lat"], center["lon"]
    return None


def deduplicate(elements: list[dict]) -> list[dict]:
    """
    Remove duplicate OSM elements — the same facility often appears as both
    a node and a way. Two elements are considered the same when:
      • they share a non-empty name (case-insensitive), AND
      • they are within 100 m of each other.
    Unnamed elements are never merged (we cannot know they are the same).
    """
    seen: list[dict] = []   # {"name": str, "lat": float, "lon": float}
    result: list[dict] = []

    for el in elements:
        coords = extract_coords(el)
        if coords is None:
            continue  # cannot place it on the map; drop it

        lat, lon = coords
        name = el.get("tags", {}).get("name", "").lower().strip()

        is_dup = False
        if name:  # only attempt dedup for named elements
            for seen_el in seen:
                if (seen_el["name"] == name
                        and _haversine_m(lat, lon, seen_el["lat"], seen_el["lon"]) < 100):
                    is_dup = True
                    break

        if not is_dup:
            seen.append({"name": name, "lat": lat, "lon": lon})
            result.append(el)

    return result


def to_amenity_dict(element: dict) -> Optional[dict]:
    """
    Convert a raw (already-deduped) Overpass element into the dict shape
    that the /amenities endpoint returns.  Returns None if coordinates
    cannot be extracted.
    """
    coords = extract_coords(element)
    if coords is None:
        return None

    lat, lon = coords
    tags = element.get("tags", {})
    category = classify_element(tags)

    # Best human-readable type label: prefer amenity, then fall back through
    # the other primary tags we query on.
    type_label = (
        tags.get("amenity")
        or tags.get("healthcare")
        or tags.get("aeroway")
        or tags.get("office")
        or tags.get("shop")
        or "unknown"
    )

    return {
        "name": tags.get("name") or tags.get("name:en") or None,
        "type": type_label,
        "category": category,
        "lat": lat,
        "lon": lon,
        # Keep the full tag set so the LLM can see emergency=, beds=, etc.
        # Missing tags mean unknown, not zero.
        "tags": dict(tags),
    }
