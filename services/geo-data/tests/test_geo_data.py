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
        assert resp.json() == {"status": "ok"}


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
        assert data["source"] == "overpass"
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

    async def test_overpass_failure_returns_502(self, client):
        from app.osm_client import OverpassError
        with patch("app.main.fetch_amenities",
                   new=AsyncMock(side_effect=OverpassError("all servers down"))):
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

    async def test_admin_unit_offline_lookup(self, client):
        resp = await client.get("/resolve?q=Gorkha")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ambiguous"] is False
        assert data["best_match"]["short_name"] == "Gorkha"
        assert data["best_match"]["source"] == "tlf-geo"
