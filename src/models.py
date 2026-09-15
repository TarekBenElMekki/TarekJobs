from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class JobRecord(BaseModel):
    # V0.5 keeps the historical JobRecord name for collector compatibility,
    # while allowing the record to represent either a job or a funded PhD.
    opportunity_type: str = "job"

    title: str
    company: str = "Unknown"
    location: str = ""
    country: str = ""
    city: str = ""
    description: str = ""
    employment_type: str = ""
    work_mode: str = ""
    remote: bool = False
    remote_region: str = ""
    seniority: str = ""
    min_years: int | None = None
    max_years: int | None = None

    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None

    posted_at: datetime | None = None
    deadline: datetime | None = None
    last_verified_at: datetime | None = None
    is_open: bool = True

    education_requirement: str = ""
    visa_status: str = "unknown"
    visa_sponsorship: bool | None = None
    international_candidates: bool | None = None
    relocation_support: bool | None = None

    funding_status: str = ""
    tuition_covered: bool | None = None
    housing_support: bool | None = None
    insurance_support: bool | None = None
    travel_support: bool | None = None
    benefits: dict[str, Any] = Field(default_factory=dict)

    source_name: str
    source_url: str
    external_id: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class ScoreResult(BaseModel):
    # All component scores are percentages (0-100). Overall is the weighted
    # TarekFit score on a 0-100 scale.
    technical: int
    eligibility: int
    location: int
    experience: int
    education: int = 0
    domain: int = 0
    visa: int = 0
    salary: int = 0
    language: int = 0
    overall: int

    priority: str = "discard"
    eligibility_status: str = "needs_verification"
    verdict: str = "needs_verification"

    explanations: list[str] = Field(default_factory=list)
    requirements: dict[str, Any] = Field(default_factory=dict)
    confidence: dict[str, Any] = Field(default_factory=dict)
