"""
Administrative matcher using tlf-geo GeoResolver and tlf-geo-profiler boundary polygons.

Provides 100% offline, exact administrative resolution for:
  - 7 Provinces
  - 77 Districts
  - 753 Local Levels (Municipalities, Metropolitan Cities, Rural Municipalities)

Computes geometric centroid coordinates directly from GeoPandas boundaries,
completely eliminating reliance on external geocoders for administrative geography.
"""
import logging
from typing import Optional

import pandas as pd
import shapely.geometry
import tlf_geo
from tlf_geo_profiler import boundaries

logger = logging.getLogger("geo-data.admin")


def _clean_val(val):
    """Return None if val is missing (pandas NaN/NA/None), otherwise val."""
    if pd.isna(val):
        return None
    return val

_resolver: Optional[tlf_geo.GeoResolver] = None
_districts_gdf = None
_local_levels_gdf = None
_provinces_gdf = None


def _get_resolver() -> tlf_geo.GeoResolver:
    global _resolver
    if _resolver is None:
        _resolver = tlf_geo.GeoResolver()
    return _resolver


def _get_gdf(layer_name: str):
    global _districts_gdf, _local_levels_gdf, _provinces_gdf
    if layer_name == "districts":
        if _districts_gdf is None:
            _districts_gdf = boundaries._load("districts")
        return _districts_gdf
    elif layer_name == "local_levels":
        if _local_levels_gdf is None:
            _local_levels_gdf = boundaries._load("local_levels")
        return _local_levels_gdf
    elif layer_name == "provinces":
        if _provinces_gdf is None:
            _provinces_gdf = boundaries._load("provinces")
        return _provinces_gdf
    return None

_nepal_boundary = None

def get_nepal_boundary():
    global _nepal_boundary
    if _nepal_boundary is None:
        provs = _get_gdf('provinces')
        if hasattr(provs, 'union_all'):
            _nepal_boundary = provs.union_all()
        else:
            import shapely.ops
            _nepal_boundary = shapely.ops.unary_union(provs.geometry)
    return _nepal_boundary

def match_admin_unit(query: str) -> Optional[dict]:
    """
    Check if query refers to a known administrative unit in Nepal.
    If matched with high confidence, returns a candidate dict with centroid coords.
    """
    clean_q = query.strip()
    r = _get_resolver()

    # Search returns list of (label, score), best first.
    # Score 100.0 means exact or clean suffix match.
    search_results = r.search(clean_q, limit=3)
    if not search_results:
        return None

    top_label, score = search_results[0]
    # We only auto-match if confidence is strong (score >= 85)
    if score < 85.0:
        return None

    # Determine level and canonical key
    # label shape is e.g. "sindhupalchok (district)" or "pokhara (metropolitan_city)"
    try:
        if "(" in top_label and top_label.endswith(")"):
            canonical_key = top_label.split("(")[0].strip()
            level_str = top_label.split("(")[1].rstrip(")").strip()
        else:
            canonical_key = top_label.strip()
            level_str = ""
    except Exception:
        canonical_key = top_label
        level_str = ""

    # Look up centroid from polygon boundaries
    coords = None
    display_name = canonical_key.title()
    osm_type = "administrative"
    address_info = {"country": "Nepal"}

    if "district" in level_str:
        gdf = _get_gdf("districts")
        # Match by name_en (case-insensitive) or canonical
        matches = gdf[gdf["name_en"].str.lower() == canonical_key.lower()]
        if not matches.empty:
            row = matches.iloc[0]
            geom = row["geometry"]
            c = geom.centroid
            coords = (float(c.y), float(c.x))
            district_name = _clean_val(row.get("name_en"))
            display_name = f"{district_name} District, Nepal" if district_name else f"{canonical_key.title()} District, Nepal"
            address_info["district"] = district_name
            address_info["name_ne"] = _clean_val(row.get("name_ne"))

    elif any(k in level_str for k in ("municipality", "gaunpalika", "metropolitan")):
        gdf = _get_gdf("local_levels")
        matches = gdf[gdf["name_en"].str.lower() == canonical_key.lower()]
        if not matches.empty:
            row = matches.iloc[0]
            geom = row["geometry"]
            c = geom.centroid
            coords = (float(c.y), float(c.x))
            city_name = _clean_val(row.get("name_en"))
            display_name = f"{city_name}, Nepal" if city_name else f"{canonical_key.title()}, Nepal"
            address_info["city"] = city_name
            address_info["type"] = _clean_val(row.get("kind"))
            address_info["name_ne"] = _clean_val(row.get("name_ne"))

    elif "province" in level_str:
        gdf = _get_gdf("provinces")
        matches = gdf[gdf["name_en"].str.lower().str.contains(canonical_key.lower())]
        if not matches.empty:
            row = matches.iloc[0]
            geom = row["geometry"]
            c = geom.centroid
            coords = (float(c.y), float(c.x))
            state_name = _clean_val(row.get("name_en"))
            display_name = f"{state_name}, Nepal" if state_name else f"{canonical_key.title()}, Nepal"
            address_info["state"] = state_name
            address_info["name_ne"] = _clean_val(row.get("name_ne"))

    if coords is None:
        return None

    return {
        "name": display_name,
        "short_name": canonical_key.title(),
        "type": level_str or "administrative",
        "osm_class": "boundary",
        "lat": coords[0],
        "lon": coords[1],
        "importance": 0.95,
        "source": "tlf-geo",
        "address": address_info,
    }


def get_polygon_for_local_level(code: str) -> dict:
    """
    Look up local level polygon boundary by official CBS/LGD code.
    Returns GeoJSON geometry dict.
    Raises ValueError if code cannot be resolved or geometry is not found.
    """
    r = _get_resolver()
    info = r.get_by_code(code)
    if not info:
        raise ValueError(f"Unknown local level code: {code}")
    name = info.get("name")
    if not name:
        raise ValueError(f"Could not determine name for local level code: {code}")

    gdf = _get_gdf("local_levels")
    matches = gdf[gdf["name_en"].str.lower() == str(name).lower()]
    if matches.empty:
        matches = gdf[gdf["name_en"].str.lower().str.contains(str(name).lower())]
    if matches.empty:
        raise ValueError(f"No boundary polygon found for local level: {name}")

    geom = matches.iloc[0]["geometry"]
    return shapely.geometry.mapping(geom)
