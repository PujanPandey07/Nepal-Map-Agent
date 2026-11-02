"""
Tests for the geo-data service.

All external calls (Overpass, Nominatim, tlf-geo-profiler profile_dict) are
mocked — the suite runs offline and fast.

Structure
---------
Unit tests:   amenity_utils  (classify, dedup, extract_coords, to_amenity_dict)
              resolve_client (parse_candidates, pick_best)
Endpoint tests: /health  /resolve  /profile  /amenities
"""
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.amenity_utils import (
    classify_element,
    deduplicate,
    extract_coords,
    to_amenity_dict,
)
from app.resolve_client import parse_candidates, pick_best

# ── shared sample data ────────────────────────────────────────────────────────

SAMPLE_PROFILE = {
    "admin_unit": {
        "province": "Bagamati Province",
        "district": "Kathmandu",
        "local_level": "Kathmandu Metropolitan City",
        "ward": 24,
        "postal_code": "44600",
    },
    "demographics": {"population": 4529, "households": 1174},
}

PARTIAL_PROFILE = {
    "admin_unit": {
        "province": "Lumbini Province",
        "district": "Nawalparasi",
        "local_level": None,
        "ward": None,
        "postal_code": None,
    },
    "demographics": {"population": None, "households": None},
}

SAMPLE_AMENITIES = [
    {
        "name": "Tribhuvan University Teaching Hospital",
        "type": "hospital",
        "category": "health",
        "lat": 27.7044,
        "lon": 85.3123,
        "tags": {"amenity": "hospital",
                 "name": "Tribhuvan University Teaching Hospital"},
    },
    {
        "name": "Maharajgunj Police Post",
        "type": "police",
        "category": "emergency_services",
        "lat": 27.7312,
        "lon": 85.329,
        "tags": {"amenity": "police", "name": "Maharajgunj Police Post"},
    },
    {
        "name": "Tribhuvan University Campus School",
        "type": "school",
        "category": "evacuation_sites",
        "lat": 27.7055,
        "lon": 85.31,
        "tags": {"amenity": "school",
                 "name": "Tribhuvan University Campus School"},
    },
]

# Single unambiguous Nominatim result
NOMINATIM_SINGLE = [
    {
        "place_id": 12345,
        "lat": "27.7172",
        "lon": "85.3240",
        "display_name": "Kathmandu, Bagmati Province, Nepal",
        "name": "Kathmandu",
        "type": "city",
        "class": "place",
        "importance": 0.7854,
        "address": {"city": "Kathmandu", "state": "Bagmati Province",
                    "country": "Nepal"},
        "namedetails": {"name": "Kathmandu", "name:en": "Kathmandu"},
    }
]

# Two closely-scored results → ambiguous
NOMINATIM_AMBIGUOUS = [
    {
        "place_id": 111,
        "lat": "28.2096",
        "lon": "83.9856",
        "display_name": "Pokhara Metropolitan City, Gandaki Province, Nepal",
        "name": "Pokhara",
        "type": "city",
        "class": "place",
        "importance": 0.55,
        "address": {},
        "namedetails": {"name": "Pokhara"},
    },
    {
        "place_id": 222,
        "lat": "28.10",
        "lon": "83.90",
        "display_name": "Pokhara Rural Municipality, Gandaki Province, Nepal",
        "name": "Pokhara",
        "type": "administrative",
        "class": "boundary",
        "importance": 0.54,   # gap = 0.01, below _AMBIGUITY_GAP of 0.15
        "address": {},
        "namedetails": {"name": "Pokhara"},
    },
]

# Two results with a large gap → top is auto-picked
NOMINATIM_CLEAR_WINNER = [
    {
        "place_id": 333,
        "lat": "27.7172",
        "lon": "85.3240",
        "display_name": "Kathmandu, Nepal",
        "name": "Kathmandu",
        "type": "city",
        "class": "place",
        "importance": 0.90,
        "address": {},
        "namedetails": {"name": "Kathmandu"},
    },
    {
        "place_id": 444,
        "lat": "27.70",
        "lon": "85.31",
        "display_name": "Kathmandu District, Nepal",
        "name": "Kathmandu",
        "type": "administrative",
        "class": "boundary",
        "importance": 0.60,   # gap = 0.30, above _AMBIGUITY_GAP of 0.15
        "address": {},
        "namedetails": {"name": "Kathmandu"},
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# Unit tests — amenity_utils
# ═══════════════════════════════════════════════════════════════════════════════

class TestClassifyElement:
    # health
    def test_hospital_amenity(self):
        assert classify_element({"amenity": "hospital"}) == "health"

    def test_clinic_amenity(self):
        assert classify_element({"amenity": "clinic"}) == "health"

    def test_pharmacy_amenity(self):
        assert classify_element({"amenity": "pharmacy"}) == "health"

    def test_healthcare_tag(self):
        assert classify_element({"healthcare": "clinic"}) == "health"

    def test_healthcare_no_is_not_health(self):
        # healthcare=no should not be classified as health
        result = classify_element({"healthcare": "no"})
        assert result != "health"

    # emergency services
    def test_police(self):
        assert classify_element({"amenity": "police"}) == "emergency_services"

    def test_fire_station(self):
        assert classify_element({"amenity": "fire_station"}) == "emergency_services"

    def test_ambulance_station(self):
        assert classify_element({"amenity": "ambulance_station"}) == "emergency_services"

    # evacuation sites
    def test_school(self):
        assert classify_element({"amenity": "school"}) == "evacuation_sites"

    def test_community_centre(self):
        assert classify_element({"amenity": "community_centre"}) == "evacuation_sites"

    def test_place_of_worship(self):
        assert classify_element({"amenity": "place_of_worship"}) == "evacuation_sites"

    def test_shelter(self):
        assert classify_element({"amenity": "shelter"}) == "evacuation_sites"

    # infrastructure
    def test_helipad(self):
        assert classify_element({"aeroway": "helipad"}) == "infrastructure"

    def test_fuel_station(self):
        assert classify_element({"amenity": "fuel"}) == "infrastructure"

    # food and water
    def test_supermarket(self):
        assert classify_element({"shop": "supermarket"}) == "food_and_water"

    def test_convenience(self):
        assert classify_element({"shop": "convenience"}) == "food_and_water"

    # government
    def test_government_office(self):
        assert classify_element({"office": "government"}) == "government"

    def test_townhall(self):
        assert classify_element({"amenity": "townhall"}) == "government"

    # fallback
    def test_unknown_tag_is_other(self):
        assert classify_element({"amenity": "bench"}) == "other"

    def test_empty_tags_is_other(self):
        assert classify_element({}) == "other"


class TestExtractCoords:
    def test_node_returns_lat_lon(self):
        el = {"type": "node", "lat": 27.7, "lon": 85.3, "tags": {}}
        assert extract_coords(el) == (27.7, 85.3)

    def test_way_with_center(self):
        el = {"type": "way", "center": {"lat": 27.7, "lon": 85.3}, "tags": {}}
        assert extract_coords(el) == (27.7, 85.3)

    def test_relation_with_center(self):
        el = {"type": "relation", "center": {"lat": 27.5, "lon": 84.0}, "tags": {}}
        assert extract_coords(el) == (27.5, 84.0)

    def test_way_without_center_returns_none(self):
        el = {"type": "way", "tags": {}}
        assert extract_coords(el) is None

    def test_node_missing_coords_returns_none(self):
        el = {"type": "node", "tags": {}}
        assert extract_coords(el) is None


class TestDeduplicate:
    def test_removes_close_same_name_duplicate(self):
        # Two nodes with the same name ~12 m apart
        elements = [
            {"type": "node", "lat": 27.7000, "lon": 85.3000,
             "tags": {"name": "Test Hospital"}},
            {"type": "node", "lat": 27.7001, "lon": 85.3001,
             "tags": {"name": "Test Hospital"}},
        ]
        assert len(deduplicate(elements)) == 1

    def test_keeps_different_names(self):
        elements = [
            {"type": "node", "lat": 27.7, "lon": 85.3,
             "tags": {"name": "Hospital A"}},
            {"type": "node", "lat": 27.7001, "lon": 85.3001,
             "tags": {"name": "Hospital B"}},
        ]
        assert len(deduplicate(elements)) == 2

    def test_keeps_same_name_far_apart(self):
        # Two "Community Centre"s in different towns — >100 m apart
        elements = [
            {"type": "node", "lat": 27.7, "lon": 85.3,
             "tags": {"name": "Community Centre"}},
            {"type": "node", "lat": 27.9, "lon": 85.5,
             "tags": {"name": "Community Centre"}},
        ]
        assert len(deduplicate(elements)) == 2

    def test_drops_elements_without_coords(self):
        elements = [
            {"type": "way", "tags": {"name": "Unnamed Way"}},  # no center
        ]
        assert len(deduplicate(elements)) == 0

    def test_keeps_multiple_unnamed_elements(self):
        # Unnamed elements are never considered duplicates of each other
        elements = [
            {"type": "node", "lat": 27.7000, "lon": 85.3000, "tags": {}},
            {"type": "node", "lat": 27.7001, "lon": 85.3001, "tags": {}},
        ]
        assert len(deduplicate(elements)) == 2


class TestToAmenityDict:
    def test_node_becomes_amenity_dict(self):
        el = {
            "type": "node",
            "lat": 27.7,
            "lon": 85.3,
            "tags": {"amenity": "hospital", "name": "Test Hospital"},
        }
        result = to_amenity_dict(el)
        assert result is not None
        assert result["category"] == "health"
        assert result["type"] == "hospital"
        assert result["name"] == "Test Hospital"
        assert result["lat"] == 27.7
        assert result["lon"] == 85.3

    def test_element_without_coords_returns_none(self):
        el = {"type": "way", "tags": {"amenity": "school"}}
        assert to_amenity_dict(el) is None

    def test_name_is_none_when_absent(self):
        el = {
            "type": "node", "lat": 27.7, "lon": 85.3,
            "tags": {"amenity": "police"},
        }
        result = to_amenity_dict(el)
        assert result is not None
        assert result["name"] is None

    def test_english_name_preferred(self):
        el = {
            "type": "node", "lat": 27.7, "lon": 85.3,
            "tags": {"amenity": "school", "name:en": "English School"},
        }
        result = to_amenity_dict(el)
        assert result is not None
        assert result["name"] == "English School"


# ═══════════════════════════════════════════════════════════════════════════════
# Unit tests — resolve_client
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseCandidates:
    def test_parses_single_result(self):
        candidates = parse_candidates(NOMINATIM_SINGLE)
        assert len(candidates) == 1
        c = candidates[0]
        assert c["short_name"] == "Kathmandu"
        assert c["lat"] == pytest.approx(27.7172)
        assert c["lon"] == pytest.approx(85.3240)
        assert c["importance"] == pytest.approx(0.7854)
        assert c["source"] == "nominatim"

    def test_parse_empty_returns_empty_list(self):
        assert parse_candidates([]) == []

    def test_address_fields_trimmed(self):
        candidates = parse_candidates(NOMINATIM_SINGLE)
        addr = candidates[0]["address"]
        # Only the fields we care about should survive
        assert "country" not in addr or addr.get("country") is not None
        # Nominatim might return 'state' for a province — that's fine
        unexpected = set(addr.keys()) - {
            "state", "district", "city", "town", "village",
            "suburb", "road", "postcode", "country"
        }
        assert unexpected == set()


class TestPickBest:
    def test_single_candidate_not_ambiguous(self):
        candidates = parse_candidates(NOMINATIM_SINGLE)
        best, ambiguous = pick_best(candidates)
        assert best is not None
        assert ambiguous is False
        assert best["short_name"] == "Kathmandu"

    def test_close_scores_are_ambiguous(self):
        candidates = parse_candidates(NOMINATIM_AMBIGUOUS)
        best, ambiguous = pick_best(candidates)
        assert ambiguous is True
        assert best is not None   # best_match still set to top candidate

    def test_large_gap_auto_picks(self):
        candidates = parse_candidates(NOMINATIM_CLEAR_WINNER)
        best, ambiguous = pick_best(candidates)
        assert ambiguous is False
        assert best["importance"] == pytest.approx(0.90)

    def test_empty_returns_none(self):
        best, ambiguous = pick_best([])
        assert best is None
        assert ambiguous is False


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoint tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthEndpoint:
    async def test_returns_ok(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "data_as_of" in data  # date of the imported OSM data


class TestProfileEndpoint:
    async def test_valid_coordinate_returns_profile(self, client):
        with patch("app.main._call_profile_dict",
                   new=AsyncMock(return_value=SAMPLE_PROFILE)):
            resp = await client.get("/profile?lat=27.7040&lon=85.3070")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "tlf-geo-profiler"
        assert data["partial"] is False
        assert data["notes"] == []
        assert data["admin_unit"]["district"] == "Kathmandu"
        assert data["demographics"]["population"] == 4529

    async def test_outside_nepal_returns_404(self, client):
        with patch("app.main._call_profile_dict",
                   new=AsyncMock(side_effect=ValueError("outside Nepal"))):
            resp = await client.get("/profile?lat=28.61&lon=77.21")
        assert resp.status_code == 404

    async def test_partial_profile_sets_partial_true(self, client):
        with patch("app.main._call_profile_dict",
                   new=AsyncMock(return_value=PARTIAL_PROFILE)):
            resp = await client.get("/profile?lat=27.5&lon=83.9")
        assert resp.status_code == 200
        data = resp.json()
        assert data["partial"] is True
        # All three missing fields should appear in notes
        notes_text = " ".join(data["notes"]).lower()
        assert "ward" in notes_text
        assert "local level" in notes_text
        assert "demographic" in notes_text

    async def test_invalid_lat_returns_422(self, client):
        resp = await client.get("/profile?lat=999&lon=85.3")
        assert resp.status_code == 422

    async def test_missing_lat_returns_422(self, client):
        resp = await client.get("/profile?lon=85.3")
        assert resp.status_code == 422


class TestAmenitiesEndpoint:
    async def test_grouped_by_category(self, client):
        with patch("app.main.fetch_amenities",
                   new=AsyncMock(return_value=SAMPLE_AMENITIES)):
            resp = await client.get(
                "/amenities?lat=27.7040&lon=85.3070&radius_m=5000")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "postgis"
        assert data["radius_m"] == 5000
        assert "data_warning" in data
        assert data["totals"]["health"] == 1
        assert data["totals"]["emergency_services"] == 1
        assert data["totals"]["evacuation_sites"] == 1

    async def test_all_category_keys_present_even_when_empty(self, client):
        with patch("app.main.fetch_amenities",
                   new=AsyncMock(return_value=[])):
            resp = await client.get("/amenities?lat=27.7040&lon=85.3070")
        data = resp.json()
        for cat in ["health", "emergency_services", "evacuation_sites",
                    "infrastructure", "food_and_water", "government", "other"]:
            assert cat in data["categories"], f"Missing category: {cat}"

    async def test_db_failure_returns_502(self, client):
        from app.db_client import DBError
        with patch("app.main.fetch_amenities",
                   new=AsyncMock(side_effect=DBError("connection refused"))):
            resp = await client.get("/amenities?lat=27.7040&lon=85.3070")
        assert resp.status_code == 502

    async def test_radius_above_cap_returns_422(self, client):
        resp = await client.get(
            "/amenities?lat=27.7040&lon=85.3070&radius_m=99999")
        assert resp.status_code == 422

    async def test_radius_below_floor_returns_422(self, client):
        resp = await client.get(
            "/amenities?lat=27.7040&lon=85.3070&radius_m=100")
        assert resp.status_code == 422

    async def test_data_warning_always_present(self, client):
        with patch("app.main.fetch_amenities",
                   new=AsyncMock(return_value=[])):
            resp = await client.get("/amenities?lat=27.7040&lon=85.3070")
        data = resp.json()
        assert len(data["data_warning"]) > 0


class TestResolveEndpoint:
    async def test_single_result_not_ambiguous(self, client):
        with patch("app.resolve_client.resolve_place",
                   new=AsyncMock(return_value=NOMINATIM_SINGLE)):
            resp = await client.get("/resolve?q=UnknownPlaceXYZ")
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "UnknownPlaceXYZ"
        assert data["ambiguous"] is False
        assert data["best_match"] is not None
        assert data["best_match"]["short_name"] == "Kathmandu"
        assert len(data["candidates"]) == 1

    async def test_close_scores_are_ambiguous(self, client):
        with patch("app.resolve_client.resolve_place",
                   new=AsyncMock(return_value=NOMINATIM_AMBIGUOUS)):
            resp = await client.get("/resolve?q=UnknownPlaceXYZ")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ambiguous"] is True
        assert len(data["candidates"]) == 2

    async def test_no_results_returns_empty(self, client):
        with patch("app.resolve_client.resolve_place",
                   new=AsyncMock(return_value=[])):
            resp = await client.get("/resolve?q=xyznonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["candidates"] == []
        assert data["best_match"] is None
        assert data["ambiguous"] is False

    async def test_empty_query_returns_422(self, client):
        resp = await client.get("/resolve?q=")
        assert resp.status_code == 422

    async def test_nominatim_failure_returns_502(self, client):
        from app.resolve_client import ResolveError
        with patch("app.resolve_client.resolve_place",
                   new=AsyncMock(side_effect=ResolveError("cannot reach Nominatim"))):
            resp = await client.get("/resolve?q=UnknownPlaceXYZ")
        assert resp.status_code == 502


class TestNominatimFallbackSafety:
    async def test_nominatim_timeout_raises_resolve_error(self):
        from app.resolve_client import resolve_place, ResolveError
        import httpx
        with patch("httpx.AsyncClient.get", side_effect=httpx.TimeoutException("Read timed out")):
            with pytest.raises(ResolveError, match="timed out"):
                await resolve_place("SomeRemoteVillageInTheHills")

    async def test_nominatim_cache_hit_avoids_second_network_call(self):
        from unittest.mock import MagicMock
        from app.resolve_client import resolve_place, _nominatim_cache
        _nominatim_cache.clear()
        mock_resp = [{"display_name": "Test Place", "lat": "27.5", "lon": "85.5", "importance": 0.5}]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_resp
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_response)) as mock_get:
            # First call triggers network request
            res1 = await resolve_place("TestCacheQuery123")
            assert len(res1) == 1
            assert mock_get.call_count == 1

            # Second call with same / normalised query — should hit cache
            res2 = await resolve_place("  testcachequery123  ")
            assert len(res2) == 1
            assert mock_get.call_count == 1  # Still 1! No new network call

    async def test_nominatim_rate_limiter_enforces_spacing(self):
        from app.resolve_client import NominatimRateLimiter
        import time
        limiter = NominatimRateLimiter(min_interval=0.05)
        t0 = time.monotonic()
        await limiter.acquire()
        await limiter.acquire()
        t1 = time.monotonic()
        assert (t1 - t0) >= 0.045

    async def test_clear_winner_not_ambiguous(self, client):
        with patch("app.resolve_client.resolve_place",
                   new=AsyncMock(return_value=NOMINATIM_CLEAR_WINNER)):
            resp = await client.get("/resolve?q=UnknownPlaceXYZ")
        data = resp.json()
        assert data["ambiguous"] is False
        assert data["best_match"]["importance"] == pytest.approx(0.90)

    async def test_curated_destination_lookup(self, client):
        resp = await client.get("/resolve?q=EBC")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ambiguous"] is False
        assert data["best_match"]["short_name"] == "Everest Base Camp"
        assert data["best_match"]["source"] == "curated"

    async def test_durbar_square_returns_ambiguous_three_candidates(self, client):
        resp = await client.get("/resolve?q=Durbar Square")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ambiguous"] is True
        assert len(data["candidates"]) == 3
        names = {c["short_name"] for c in data["candidates"]}
        assert "Kathmandu Durbar Square" in names
        assert "Patan Durbar Square" in names
        assert "Bhaktapur Durbar Square" in names
        for c in data["candidates"]:
            assert c["source"] == "curated"

    async def test_admin_unit_offline_lookup(self, client):
        resp = await client.get("/resolve?q=Gorkha")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ambiguous"] is False
        assert data["best_match"]["short_name"] == "Gorkha"
        assert data["best_match"]["source"] == "tlf-geo"

    async def test_admin_unit_chhatradev_nan_name_ne_returns_null(self, client):
        resp = await client.get("/resolve?q=Chhatradev")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ambiguous"] is False
        assert data["best_match"]["short_name"] == "Chhatradev"
        assert data["best_match"]["address"]["name_ne"] is None

    def test_admin_matcher_handles_nan_in_row(self):
        import numpy as np
        import pandas as pd
        from shapely.geometry import Point
        from unittest.mock import patch, MagicMock
        from app.admin_matcher import match_admin_unit

        fake_gdf = pd.DataFrame([{
            "name_en": "TestPlace",
            "name_ne": np.nan,
            "kind": np.nan,
            "geometry": Point(85.3, 27.7),
        }])

        mock_resolver = MagicMock()
        mock_resolver.search.return_value = [("testplace (municipality)", 100.0)]

        with patch("app.admin_matcher._get_resolver", return_value=mock_resolver), \
             patch("app.admin_matcher._get_gdf", return_value=fake_gdf):
            result = match_admin_unit("TestPlace")
            assert result is not None
            assert result["address"]["name_ne"] is None
            assert result["address"]["type"] is None
            assert result["address"]["city"] == "TestPlace"


# ═══════════════════════════════════════════════════════════════════════════════
# db_client unit tests (mock the psycopg connection)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDbClientRowConversion:
    """Unit-test _row_to_element without touching the database."""

    def test_point_row_maps_to_node(self):
        from app.db_client import _row_to_element
        row = {
            "osm_id": 12345,
            "lat": 27.70,
            "lon": 85.31,
            "amenity": "hospital",
            "aeroway": None,
            "office": None,
            "shop": None,
            "name": "City Hospital",
            "healthcare": None,
            "emergency": None,
            "extra_tags": {},
        }
        el = _row_to_element(row, "node")
        assert el["type"] == "node"
        assert el["id"] == 12345
        assert el["lat"] == 27.70
        assert el["lon"] == 85.31
        assert el["tags"]["amenity"] == "hospital"
        assert el["tags"]["name"] == "City Hospital"

    def test_polygon_row_maps_to_way(self):
        from app.db_client import _row_to_element
        row = {
            "osm_id": 99,
            "lat": 27.5,
            "lon": 83.9,
            "amenity": "school",
            "aeroway": None,
            "office": None,
            "shop": None,
            "name": None,
            "healthcare": None,
            "emergency": None,
            "extra_tags": {"capacity": "500"},
        }
        el = _row_to_element(row, "way")
        assert el["type"] == "way"
        assert el["tags"]["amenity"] == "school"
        assert el["tags"]["capacity"] == "500"
        # way element must have a center so extract_coords finds it
        assert el["center"]["lat"] == 27.5
        assert el["center"]["lon"] == 83.9

    def test_healthcare_from_hstore_tag(self):
        from app.db_client import _row_to_element
        row = {
            "osm_id": 1,
            "lat": 27.7, "lon": 85.3,
            "amenity": None, "aeroway": None, "office": None, "shop": None,
            "name": "Health Post",
            "healthcare": "health_post",
            "emergency": None,
            "extra_tags": {},
        }
        el = _row_to_element(row, "node")
        assert el["tags"]["healthcare"] == "health_post"
        # classify_element should map this to health category
        from app.amenity_utils import classify_element
        assert classify_element(el["tags"]) == "health"

    def test_row_to_element_feeds_deduplicate(self):
        """Two rows with the same name close together should dedup to one."""
        from app.db_client import _row_to_element
        from app.amenity_utils import deduplicate
        base = {
            "amenity": "school", "aeroway": None, "office": None, "shop": None,
            "healthcare": None, "emergency": None, "extra_tags": {},
        }
        row_a = {**base, "osm_id": 1, "lat": 27.7000, "lon": 85.3000, "name": "Test School"}
        row_b = {**base, "osm_id": 2, "lat": 27.7001, "lon": 85.3001, "name": "Test School"}
        elements = [_row_to_element(row_a, "node"), _row_to_element(row_b, "way")]
        deduped = deduplicate(elements)
        assert len(deduped) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Integration test — real PostGIS database (skipped when DB is not reachable)
# ═══════════════════════════════════════════════════════════════════════════════

import os as _os


def _db_available() -> bool:
    """Return True if the PostGIS container is reachable."""
    import socket
    host = _os.getenv("DB_HOST", "localhost")
    port = int(_os.getenv("DB_PORT", "5433"))
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


_DB_SKIP = pytest.mark.skipif(
    not _db_available(),
    reason="PostGIS not reachable (set DB_HOST/DB_PORT or start the container)",
)


class TestDbClientIntegration:
    """Integration tests against the real local PostGIS database."""

    @_DB_SKIP
    async def test_chhatradev_returns_amenities(self):
        """
        Chhatradev Rural Municipality (Arghakhanchi) at 10 km radius.
        Expect at least some evacuation sites and health facilities.
        The exact numbers depend on the OSM import; we just verify the
        plumbing works end-to-end.
        """
        from app.db_client import fetch_amenities
        amenities = await fetch_amenities(27.9954, 83.2253, 10000)
        assert len(amenities) > 0, "Expected at least some amenities near Chhatradev"

        cats = {a["category"] for a in amenities}
        assert "evacuation_sites" in cats, "Expected evacuation sites (schools, temples, etc.)"
        assert "health" in cats, "Expected at least one health facility"

        # Every returned item must have lat/lon
        for a in amenities:
            assert isinstance(a["lat"], float)
            assert isinstance(a["lon"], float)

    @_DB_SKIP
    async def test_radius_is_respected(self):
        """A small radius should return fewer amenities than a large one."""
        from app.db_client import fetch_amenities
        small = await fetch_amenities(27.9954, 83.2253, 1000)
        large = await fetch_amenities(27.9954, 83.2253, 10000)
        assert len(large) >= len(small), (
            f"Large radius ({len(large)}) should have >= results as small ({len(small)})"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 1: Contract Fixes Tests (T1 - T5)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhase1ContractFixes:
    async def test_ambiguous_durbar_square_contract(self, client):
        resp = await client.get("/resolve?q=Durbar Square")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ambiguous"] is True
        assert data["best_match"] is None
        assert data["clarification_needed"] is True
        assert "options" in data
        assert len(data["options"]) == 3
        assert "data_as_of" in data
        for opt in data["options"]:
            assert "id" in opt
            assert "label" in opt
            assert "lat" in opt
            assert "lon" in opt
            assert len(opt["label"]) > 5

    async def test_non_ambiguous_ebc_contract(self, client):
        resp = await client.get("/resolve?q=EBC")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ambiguous"] is False
        assert data["best_match"] is not None
        assert data["clarification_needed"] is False
        assert data["options"] == []
        assert "data_as_of" in data
        # Check admin context enrichment
        c = data["best_match"]
        assert c.get("district") is not None
        assert c.get("province") is not None

    async def test_mocked_nominatim_with_nan_fields_enrichment(self, client):
        import numpy as np
        raw_nominatim_with_nan = [
            {
                "display_name": "Test Village, Nepal",
                "lat": "27.7041",
                "lon": "85.3075",
                "type": "village",
                "class": "place",
                "importance": 0.5,
                "address": {
                    "village": "Test Village",
                    "district": np.nan,
                    "country": "Nepal",
                },
                "namedetails": {"name": "Test Village"},
            },
            {
                "display_name": "Second Village, Nepal",
                "lat": "27.6734",
                "lon": "85.3250",
                "type": "village",
                "class": "place",
                "importance": 0.49,
                "address": {"village": "Second Village"},
                "namedetails": {"name": "Second Village"},
            },
        ]
        with patch("app.resolve_client.resolve_place", new=AsyncMock(return_value=raw_nominatim_with_nan)):
            resp = await client.get("/resolve?q=TestAmbiguousQueryXYZ")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ambiguous"] is True
        assert data["best_match"] is None
        assert data["clarification_needed"] is True
        assert len(data["options"]) == 2

    async def test_unknown_query_param_amenities_returns_422(self, client):
        resp = await client.get("/amenities?lat=27.704&lon=85.307&radius=5000")
        assert resp.status_code == 422
        assert "radius" in resp.json()["detail"].lower()

    async def test_unknown_query_param_resolve_returns_422(self, client):
        resp = await client.get("/resolve?q=Kathmandu&extra_param=val")
        assert resp.status_code == 422
        assert "extra_param" in resp.json()["detail"].lower()

    async def test_unknown_query_param_profile_returns_422(self, client):
        resp = await client.get("/profile?lat=27.704&lon=85.307&invalid_arg=xyz")
        assert resp.status_code == 422
        assert "invalid_arg" in resp.json()["detail"].lower()


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2: Area-based Amenities Tests (T6)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhase2AmenitiesArea:
    sample_poly = {
        "type": "Polygon",
        "coordinates": [
            [
                [85.300, 27.700],
                [85.315, 27.700],
                [85.315, 27.715],
                [85.300, 27.715],
                [85.300, 27.700],
            ]
        ],
    }

    async def test_amenities_area_valid_polygon(self, client):
        payload = {"geometry": self.sample_poly}
        resp = await client.post("/amenities/area", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "postgis"
        assert "summary" in data
        assert "by_category" in data["summary"]
        assert "by_type" in data["summary"]
        assert "total" in data["summary"]
        assert "items" in data
        assert isinstance(data["items"], list)
        assert "data_as_of" in data

    async def test_amenities_area_by_local_level_code(self, client):
        # 55102 is Chhatradev rural municipality
        payload = {"local_level_code": "55102"}
        resp = await client.post("/amenities/area", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total"] > 0
        assert "health" in data["summary"]["by_category"]
        assert "evacuation_sites" in data["summary"]["by_category"]

    async def test_amenities_area_invalid_polygon(self, client):
        # Invalid geometry type (Point instead of Polygon)
        payload = {"geometry": {"type": "Point", "coordinates": [85.30, 27.70]}}
        resp = await client.post("/amenities/area", json=payload)
        assert resp.status_code == 422

    async def test_amenities_area_empty_result(self, client):
        # Small polygon in uninhabited coordinates
        empty_poly = {
            "type": "Polygon",
            "coordinates": [
                [
                    [86.990, 27.990],
                    [86.991, 27.990],
                    [86.991, 27.991],
                    [86.990, 27.991],
                    [86.990, 27.990],
                ]
            ],
        }
        resp = await client.post("/amenities/area", json={"geometry": empty_poly})
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total"] == 0
        assert data["items"] == []

    async def test_amenities_area_top_n_with_proximity(self, client):
        payload = {
            "geometry": self.sample_poly,
            "top_n": 3,
            "near_lat": 27.7041,
            "near_lon": 85.3075,
        }
        resp = await client.post("/amenities/area", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) <= 3
        # Summary total should reflect the overall area, not just top_n
        assert data["summary"]["total"] >= len(data["items"])

    async def test_amenities_area_statement_timeout_simulated(self, client):
        with patch("app.main.fetch_amenities_area", side_effect=TimeoutError("Statement timed out")):
            resp = await client.post("/amenities/area", json={"geometry": self.sample_poly})
            assert resp.status_code in (504, 502)

