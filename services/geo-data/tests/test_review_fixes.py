"""
Regression tests for the problems found in the code review.

Most of these are pure unit tests (no DB, no network).  The few that use the
`client` fixture go through the FastAPI app with the heavy parts patched.
"""
import math
from unittest.mock import AsyncMock, patch

import pytest

from app.amenity_utils import classify_element, deduplicate, to_amenity_dict
from app.db_client import _bbox, _parse_hstore


# ═══════════════════════════════════════════════════════════════════════════════
# Classification: emergency=* and water
# ═══════════════════════════════════════════════════════════════════════════════

class TestClassificationFixes:
    def test_assembly_point_is_an_evacuation_site(self):
        assert classify_element({"emergency": "assembly_point"}) == "evacuation_sites"

    def test_other_emergency_equipment_is_emergency_services(self):
        assert classify_element({"emergency": "fire_hydrant"}) == "emergency_services"
        assert classify_element({"emergency": "defibrillator"}) == "emergency_services"

    def test_emergency_no_is_ignored(self):
        assert classify_element({"emergency": "no"}) == "other"

    def test_hospital_with_emergency_yes_stays_health(self):
        assert classify_element({"amenity": "hospital", "emergency": "yes"}) == "health"

    def test_water_points_are_food_and_water(self):
        assert classify_element({"amenity": "water_point"}) == "food_and_water"
        assert classify_element({"amenity": "drinking_water"}) == "food_and_water"

    def test_fuel_is_still_infrastructure(self):
        assert classify_element({"amenity": "fuel"}) == "infrastructure"

    def test_type_label_uses_emergency_tag(self):
        el = {"type": "node", "lat": 27.7, "lon": 85.3,
              "tags": {"emergency": "assembly_point"}}
        out = to_amenity_dict(el)
        assert out["type"] == "assembly_point"
        assert out["category"] == "evacuation_sites"


# ═══════════════════════════════════════════════════════════════════════════════
# Deduplication: tag merge, category awareness
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeduplicateFixes:
    def test_tags_of_dropped_duplicate_are_merged(self):
        node = {"type": "node", "lat": 27.7000, "lon": 85.3000,
                "tags": {"name": "Test Hospital", "amenity": "hospital"}}
        way = {"type": "way", "center": {"lat": 27.7001, "lon": 85.3001},
               "tags": {"name": "Test Hospital", "amenity": "hospital", "beds": "50"}}
        out = deduplicate([node, way])
        assert len(out) == 1
        assert out[0]["tags"]["beds"] == "50"
        # the caller's input must not be mutated
        assert "beds" not in node["tags"]

    def test_kept_elements_values_win_on_conflict(self):
        a = {"type": "node", "lat": 27.7, "lon": 85.3,
             "tags": {"name": "X", "amenity": "clinic", "operator": "first"}}
        b = {"type": "way", "center": {"lat": 27.7, "lon": 85.3},
             "tags": {"name": "X", "amenity": "clinic", "operator": "second"}}
        out = deduplicate([a, b])
        assert out[0]["tags"]["operator"] == "first"

    def test_same_name_different_category_is_not_merged(self):
        hospital = {"type": "node", "lat": 27.7000, "lon": 85.3000,
                    "tags": {"name": "Central", "amenity": "hospital"}}
        fuel = {"type": "node", "lat": 27.7001, "lon": 85.3001,
                "tags": {"name": "Central", "amenity": "fuel"}}
        assert len(deduplicate([hospital, fuel])) == 2

    def test_large_input_is_fast(self):
        import time
        big = [
            {"type": "node", "lat": 27.0 + i * 1e-4, "lon": 85.0 + i * 1e-4,
             "tags": {"name": f"n{i % 50}", "amenity": "school"}}
            for i in range(20000)
        ]
        t0 = time.monotonic()
        deduplicate(big)
        assert time.monotonic() - t0 < 15  # O(n^2) version needed minutes here


# ═══════════════════════════════════════════════════════════════════════════════
# db_client helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestBbox:
    def test_box_contains_the_whole_circle_east_west(self):
        lat, lon, r = 27.9954, 83.2253, 10_000
        xmin, ymin, xmax, ymax = _bbox(lat, lon, r)
        east_deg = r / (111_320 * math.cos(math.radians(lat)))
        north_deg = r / 111_320
        assert lon + east_deg <= xmax
        assert lon - east_deg >= xmin
        assert lat + north_deg <= ymax
        assert lat - north_deg >= ymin

    def test_longitude_is_wider_than_latitude_away_from_equator(self):
        xmin, ymin, xmax, ymax = _bbox(28.0, 84.0, 5_000)
        assert (xmax - xmin) > (ymax - ymin) * 1.1


class TestHstoreFallbackParser:
    def test_basic_pairs_and_null(self):
        assert _parse_hstore('"beds"=>"50","x"=>NULL') == {"beds": "50"}

    def test_escaped_quote_and_backslash(self):
        assert _parse_hstore(r'"k"=>"a\"b"') == {"k": 'a"b'}
        assert _parse_hstore(r'"k"=>"a\\b"') == {"k": "a\\b"}

    def test_backslash_followed_by_quote(self):
        # value is:  a\"b   (backslash then quote) -> hstore text  a\\\"b
        assert _parse_hstore(r'"k"=>"a\\\"b"') == {"k": 'a\\"b'}

    def test_empty(self):
        assert _parse_hstore("") == {}


# ── fake pool plumbing ───────────────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, log, fetchone_value=None):
        self._log = log
        self._fetchone_value = fetchone_value

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self._log.append((" ".join(sql.split()), params))

    async def fetchall(self):
        return []

    async def fetchone(self):
        return self._fetchone_value


class _FakeConn:
    def __init__(self, log, fetchone_value=None):
        self._log = log
        self._fetchone_value = fetchone_value

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._log, self._fetchone_value)


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def connection(self, timeout=None):
        return self._conn


class TestDbClientFixes:
    async def test_area_timeout_is_transaction_local(self):
        """SET (session-wide) leaks onto the pooled connection; set_config(..., true) does not."""
        from app import db_client

        log: list = []
        pool = _FakePool(_FakeConn(log))
        with patch("app.db_client.get_pool", new=AsyncMock(return_value=pool)):
            await db_client.fetch_amenities_area("{}", timeout_seconds=10.0)

        first_sql, first_params = log[0]
        assert "set_config" in first_sql
        assert "statement_timeout" in first_sql
        assert first_sql.rstrip(";").endswith("true)")  # is_local = true
        assert first_params == ("10000",)
        assert not any(sql.upper().startswith("SET ") for sql, _ in log)

    async def test_radius_queries_pass_a_bounding_box(self):
        from app import db_client

        log: list = []
        pool = _FakePool(_FakeConn(log))
        lat, lon = 27.7040, 85.3070
        with patch("app.db_client.get_pool", new=AsyncMock(return_value=pool)):
            await db_client.fetch_amenities(lat, lon, 5000)

        assert len(log) == 2  # points + polygons
        for sql, params in log:
            assert "&& ST_MakeEnvelope" in sql
            assert len(params) == 7
            xmin, ymin, xmax, ymax, p_lon, p_lat, radius = params
            assert xmin < lon < xmax and ymin < lat < ymax
            assert (p_lon, p_lat, radius) == (lon, lat, 5000)

    async def test_data_as_of_reads_dict_rows(self, monkeypatch):
        """Rows are dicts (dict_row), so row[0] raised KeyError and the DB date was never used."""
        from app import db_client

        monkeypatch.setattr(db_client, "_data_as_of", None)
        log: list = []
        pool = _FakePool(_FakeConn(log, fetchone_value={"value": "2026-11-01T03:00:00Z"}))
        with patch("app.db_client.get_pool", new=AsyncMock(return_value=pool)):
            assert await db_client.get_data_as_of() == "2026-11-01"

    async def test_data_as_of_failure_is_not_cached(self, monkeypatch):
        from app import db_client

        monkeypatch.setattr(db_client, "_data_as_of", None)
        with patch("app.db_client.get_pool", new=AsyncMock(side_effect=OSError("db down"))):
            value = await db_client.get_data_as_of()
        assert value  # falls back to a usable value
        assert db_client._data_as_of is None  # ...but a transient failure is not remembered


# ═══════════════════════════════════════════════════════════════════════════════
# main.py: health, /amenities/area guards
# ═══════════════════════════════════════════════════════════════════════════════

_SMALL_KATHMANDU_POLY = {
    "type": "Polygon",
    "coordinates": [[
        [85.30, 27.70], [85.32, 27.70], [85.32, 27.72],
        [85.30, 27.72], [85.30, 27.70],
    ]],
}


class TestHealthWhenDbDown:
    async def test_health_does_not_wait_on_the_db_for_data_as_of(self, client):
        boom = AsyncMock(side_effect=AssertionError("must not query the DB when it is down"))
        with patch("app.main.check_db_health", new=AsyncMock(return_value=False)), \
             patch("app.main.get_data_as_of", new=boom):
            resp = await client.get("/health")
        assert resp.status_code == 503
        body = resp.json()
        assert body["database"] == "down"
        assert body["data_as_of"]
        boom.assert_not_called()


class TestAreaGuards:
    async def test_area_calculation_failure_fails_closed(self, client):
        """Before the fix a failing area calc set area_km2 = 0 and skipped the size limit."""
        import pyproj
        fetch = AsyncMock(return_value=[])
        with patch.object(pyproj.Geod, "geometry_area_perimeter",
                          side_effect=RuntimeError("boom")), \
             patch("app.main.fetch_amenities_area", new=fetch):
            resp = await client.post("/amenities/area", json={"geometry": _SMALL_KATHMANDU_POLY})
        assert resp.status_code == 422
        fetch.assert_not_called()

    async def test_vertex_limit(self, client):
        fetch = AsyncMock(return_value=[])
        with patch("app.main.MAX_GEOMETRY_VERTICES", 3), \
             patch("app.main.fetch_amenities_area", new=fetch):
            resp = await client.post("/amenities/area", json={"geometry": _SMALL_KATHMANDU_POLY})
        assert resp.status_code == 422
        assert "vertices" in resp.json()["detail"].lower()
        fetch.assert_not_called()

    def test_polygonal_only_drops_stray_lines(self):
        from shapely.geometry import GeometryCollection, LineString, Polygon
        from app.main import _polygonal_only

        poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 0)])
        gc = GeometryCollection([poly, LineString([(0, 0), (2, 2)])])
        out = _polygonal_only(gc)
        assert out.geom_type in ("Polygon", "MultiPolygon")
        assert out.area == pytest.approx(poly.area)

    def test_polygonal_only_rejects_geometry_without_polygons(self):
        from shapely.geometry import GeometryCollection, LineString
        from app.main import _polygonal_only

        with pytest.raises(ValueError):
            _polygonal_only(GeometryCollection([LineString([(0, 0), (1, 1)])]))


# ═══════════════════════════════════════════════════════════════════════════════
# admin_matcher: duplicate names must not silently pick the wrong polygon
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdminMatcherDuplicateNames:
    @staticmethod
    def _gdf():
        import pandas as pd
        from shapely.geometry import Point
        return pd.DataFrame([
            {"name_en": "Dup", "district": "Alpha", "geometry": Point(0, 0)},
            {"name_en": "Dup", "district": "Beta", "geometry": Point(1, 1)},
            {"name_en": "Unique", "district": "Alpha", "geometry": Point(2, 2)},
        ])

    def test_exact_name_match_is_case_insensitive_and_not_regex(self):
        from app.admin_matcher import _name_matches
        gdf = self._gdf()
        assert len(_name_matches(gdf, "dup")) == 2
        assert len(_name_matches(gdf, "Du")) == 0      # no substring matching
        assert len(_name_matches(gdf, "Dup(")) == 0    # regex metacharacters are harmless

    def test_ambiguous_name_raises_instead_of_picking_first(self):
        from app.admin_matcher import _name_matches, _pick_single_row
        with pytest.raises(ValueError, match="Ambiguous"):
            _pick_single_row(_name_matches(self._gdf(), "Dup"), "Dup")

    def test_ambiguity_is_resolved_by_district_when_known(self):
        from app.admin_matcher import _name_matches, _pick_single_row
        row = _pick_single_row(_name_matches(self._gdf(), "Dup"), "Dup", {"district": "Beta"})
        assert row["district"] == "Beta"

    def test_unique_name_still_works(self):
        from app.admin_matcher import _name_matches, _pick_single_row
        row = _pick_single_row(_name_matches(self._gdf(), "Unique"), "Unique")
        assert row["district"] == "Alpha"


# ═══════════════════════════════════════════════════════════════════════════════
# resolve_client: ranking, malformed rows, tier-2 ambiguity
# ═══════════════════════════════════════════════════════════════════════════════

def _raw(importance, osm_class, osm_type, name):
    return {"lat": "27.7", "lon": "85.3", "importance": importance,
            "class": osm_class, "type": osm_type, "display_name": name}


class TestResolveClientFixes:
    def test_similar_importance_prefers_geography_over_shop(self):
        from app.resolve_client import parse_candidates
        out = parse_candidates([_raw(0.52, "shop", "supermarket", "Shop"),
                                _raw(0.51, "place", "city", "City")])
        assert out[0]["osm_class"] == "place"

    def test_clearly_more_important_result_is_not_demoted(self):
        from app.resolve_client import parse_candidates
        out = parse_candidates([_raw(0.80, "shop", "x", "Shop"),
                                _raw(0.40, "place", "city", "City")])
        assert out[0]["osm_class"] == "shop"

    def test_hospital_outranks_generic_amenity_at_equal_importance(self):
        from app.resolve_client import parse_candidates
        out = parse_candidates([_raw(0.50, "amenity", "cafe", "Cafe"),
                                _raw(0.50, "amenity", "hospital", "Hospital")])
        assert out[0]["type"] == "hospital"

    def test_malformed_row_is_skipped_not_fatal(self):
        from app.resolve_client import parse_candidates
        out = parse_candidates([{"display_name": "no coordinates"},
                                _raw(0.3, "place", "city", "OK")])
        assert len(out) == 1
        assert out[0]["name"] == "OK"

    async def test_cache_is_bounded(self):
        from unittest.mock import MagicMock
        from app import resolve_client as rc

        rc._nominatim_cache.clear()
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = []
        with patch.object(rc, "NOMINATIM_CACHE_MAX", 3), \
             patch.object(rc._rate_limiter, "acquire", new=AsyncMock()), \
             patch("httpx.AsyncClient.get", new=AsyncMock(return_value=resp)):
            for i in range(10):
                await rc.resolve_place(f"query-{i}")
        assert len(rc._nominatim_cache) <= 3
        rc._nominatim_cache.clear()

    async def test_same_name_at_two_admin_levels_is_ambiguous(self, client):
        def _unit(kind):
            return {
                "name": f"Zzzambig {kind}, Nepal", "short_name": "Zzzambig",
                "type": kind, "osm_class": "boundary", "lat": 27.67, "lon": 85.43,
                "importance": 0.95, "source": "tlf-geo",
                "address": {"country": "Nepal"},
            }

        with patch("app.resolve_client.match_admin_units",
                   return_value=[_unit("district"), _unit("municipality")]):
            resp = await client.get("/resolve?q=Zzzambig")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ambiguous"] is True
        assert data["best_match"] is None
        assert len(data["candidates"]) == 2
        assert len(data["options"]) == 2
