# services/geo-data/app/models.py
"""
Pydantic schemas for the geo-data service response models.
Ensures clean, self-documenting OpenAPI schemas at /docs.
"""
from typing import Any, Optional
from pydantic import BaseModel, Field


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


class ResolveResponse(BaseModel):
    query: str = Field(..., description="Search query string")
    ambiguous: bool = Field(..., description="True if top candidates had similar scores and user should disambiguate")
    best_match: Optional[Candidate] = Field(None, description="Top ranked candidate, or null if no matches")
    candidates: list[Candidate] = Field(default_factory=list, description="All matching candidates")


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
