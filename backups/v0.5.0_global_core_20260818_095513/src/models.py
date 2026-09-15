from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class JobRecord(BaseModel):
    title: str
    company: str = "Unknown"
    location: str = ""
    description: str = ""
    employment_type: str = ""
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
    source_name: str
    source_url: str
    external_id: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class ScoreResult(BaseModel):
    technical: int
    eligibility: int
    location: int
    experience: int
    overall: int
    verdict: str = "needs_verification"
    explanations: list[str] = Field(default_factory=list)
    requirements: dict[str, Any] = Field(default_factory=dict)
    confidence: dict[str, Any] = Field(default_factory=dict)