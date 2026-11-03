# services/geo-data/app/models.py
"""
Pydantic schemas for the geo-data service response models.
Ensures clean, self-documenting OpenAPI schemas at /docs.
"""
from typing import Any, Optional
from pydantic import BaseModel, Field, model_validator


# ── Health ────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = Field("ok", description="Service status")
    data_as_of: str = Field(..., description="Date of the imported OSM data snapshot (YYYY-MM-DD)")


# ── Resolve ───────────────────────────────────────────────────────────────────

class CandidateAddress(BaseModel):
    name_en: Optional[str] = None
    name_ne: Optional[str] = None
    type: Optional[str] = None
    village: Optional[str] = None
    town: Optional[str] = None
    city: Optional[str] = None
    municipality: Optional[str] = None
    county: Optional[str] = None
    district: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    postcode: Optional[str] = None
    country_code: Optional[str] = None

    model_config = {"extra": "allow"}


class Candidate(BaseModel):
    name: str = Field(..., description="Full display name")
    short_name: str = Field(..., description="Short place name")
    lat: float = Field(..., description="Latitude")
    lon: float = Field(..., description="Longitude")
    type: str = Field(..., description="Feature type (e.g. administrative, hospital, peak)")
    importance: float = Field(..., description="Relevance score (0.0 to 1.0)")
    address: CandidateAddress = Field(default_factory=CandidateAddress, description="Address breakdown")
    source: Optional[str] = Field(None, description="Resolver tier source: curated, tlf-geo, or nominatim")
    province: Optional[str] = Field(None, description="Province name")
    district: Optional[str] = Field(None, description="District name")
    local_level: Optional[str] = Field(None, description="Local level name")
    local_level_type: Optional[str] = Field(None, description="Local level type")
    ward: Optional[int] = Field(None, description="Ward number")

    model_config = {"extra": "allow"}


class ResolveOption(BaseModel):
    id: str = Field(..., description="Option identifier")
    label: str = Field(..., description="Human-readable place label with administrative context")
    lat: float = Field(..., description="Latitude")
    lon: float = Field(..., description="Longitude")


class ResolveResponse(BaseModel):
    query: str = Field(..., description="Search query string")
    ambiguous: bool = Field(..., description="True if top candidates had similar scores and user should disambiguate")
    best_match: Optional[Candidate] = Field(None, description="Top ranked candidate, or null if ambiguous or no matches")
    candidates: list[Candidate] = Field(default_factory=list, description="All matching candidates")
    clarification_needed: bool = Field(False, description="True if ambiguity requires user clarification")
    options: list[ResolveOption] = Field(default_factory=list, description="Options for disambiguation")
    data_as_of: Optional[str] = Field(None, description="Date of the imported OSM data snapshot (YYYY-MM-DD)")


# ── Profile ───────────────────────────────────────────────────────────────────

class AdminUnit(BaseModel):
    province: Optional[str] = None
    district: Optional[str] = None
    local_level: Optional[str] = None
    ward: Optional[int] = None
    postal_code: Optional[str] = None

    model_config = {"extra": "allow"}


class Demographics(BaseModel):
    population: Optional[int] = None
    households: Optional[int] = None

    model_config = {"extra": "allow"}


class ProfileResponse(BaseModel):
    source: str = Field("tlf-geo-profiler", description="Data source")
    partial: bool = Field(..., description="True if some administrative fields could not be resolved")
    notes: list[str] = Field(default_factory=list, description="Explanations for any unresolved fields")
    admin_unit: AdminUnit = Field(..., description="Administrative geography hierarchy")
    demographics: Demographics = Field(..., description="Census demographics")

    model_config = {"extra": "allow"}


# ── Amenities ─────────────────────────────────────────────────────────────────

class AmenityItem(BaseModel):
    name: Optional[str] = Field(None, description="Facility name (English preferred)")
    type: str = Field(..., description="Amenity/healthcare/aeroway tag value")
    category: str = Field(..., description="Standard disaster category")
    lat: float = Field(..., description="Latitude")
    lon: float = Field(..., description="Longitude")
    tags: dict[str, Any] = Field(default_factory=dict, description="Raw OSM tags")


class AmenitiesResponse(BaseModel):
    source: str = Field("postgis", description="Data source")
    data_as_of: str = Field(..., description="Date of the imported OSM snapshot")
    radius_m: int = Field(..., description="Search radius in metres")
    data_warning: str = Field(..., description="Grounding warning regarding rural OSM completeness")
    categories: dict[str, list[AmenityItem]] = Field(..., description="Amenities grouped by category")
    totals: dict[str, int] = Field(..., description="Count of amenities in each category")


# ── Area Amenities ────────────────────────────────────────────────────────────

class AreaCentroid(BaseModel):
    lat: float = Field(..., description="Latitude of polygon centroid")
    lon: float = Field(..., description="Longitude of polygon centroid")


class AreaInfo(BaseModel):
    area_km2: float = Field(..., description="Geodesic surface area in square kilometres")
    centroid: AreaCentroid = Field(..., description="Centroid of the requested area")


class AreaAmenitiesRequest(BaseModel):
    geometry: Optional[dict[str, Any]] = Field(None, description="GeoJSON Polygon or MultiPolygon in EPSG:4326")
    local_level_code: Optional[str] = Field(None, description="Official local level code (e.g. '55102')")
    top_n: int = Field(10, ge=1, le=50, description="Max amenities returned per category (default 10, max 50)")
    near_lat: Optional[float] = Field(None, ge=-90, le=90, description="Latitude to sort results by proximity")
    near_lon: Optional[float] = Field(None, ge=-180, le=180, description="Longitude to sort results by proximity")

    @model_validator(mode="after")
    def validate_one_of(self):
        if not self.geometry and not self.local_level_code:
            raise ValueError("Must provide either 'geometry' or 'local_level_code'")
        if self.geometry and self.local_level_code:
            raise ValueError("Must provide either 'geometry' or 'local_level_code', not both")
        if self.geometry:
            g_type = self.geometry.get("type")
            if g_type not in ("Polygon", "MultiPolygon"):
                raise ValueError("Geometry must be a Polygon or MultiPolygon")
            if "coordinates" not in self.geometry or not self.geometry["coordinates"]:
                raise ValueError("Geometry must contain coordinates")
        return self


class AreaSummary(BaseModel):
    total: int = Field(..., description="Total count of amenities in the area")
    by_category: dict[str, int] = Field(..., description="Count of amenities per disaster category")
    by_type: dict[str, int] = Field(..., description="Count of amenities per specific feature type")


class AreaAmenitiesResponse(BaseModel):
    source: str = Field("postgis", description="Data source")
    data_as_of: str = Field(..., description="Date of the imported OSM snapshot")
    data_warning: str = Field(..., description="Grounding warning regarding rural OSM completeness")
    area: AreaInfo = Field(..., description="Geodesic area metadata and centroid")
    summary: AreaSummary = Field(..., description="Summary statistics of amenities in the area")
    categories: dict[str, list[AmenityItem]] = Field(..., description="Amenities grouped by category, up to top_n per category")
    truncated: dict[str, bool] = Field(..., description="Whether each category was truncated by top_n")
    items: list[AmenityItem] = Field(..., description="List of amenities matching the area")
    top_n: int = Field(10, description="Requested top_n limit per category")
    near_lat: Optional[float] = Field(None, description="Proximity sort center latitude, if provided")
    near_lon: Optional[float] = Field(None, description="Proximity sort center longitude, if provided")
