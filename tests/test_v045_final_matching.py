from __future__ import annotations

import json
from pathlib import Path

import yaml

from src.matcher import score_job
from src.models import JobRecord

ROOT = Path(__file__).resolve().parents[1]
PROFILE = json.loads(
    (ROOT / "config" / "profile.json").read_text(encoding="utf-8")
)
SETTINGS = yaml.safe_load(
    (ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
)


def make_job(title: str, description: str = "", location: str = "Worldwide"):
    return JobRecord(
        title=title,
        company="Test",
        location=location,
        description=description,
        employment_type="full-time",
        remote=location == "Worldwide",
        remote_region="Worldwide" if location == "Worldwide" else None,
        source_name="test",
        source_url="https://example.com/job",
    )


def test_bulgarian_language_in_title_is_mandatory_and_blocking():
    result = score_job(
        make_job(
            "Customer Support Team Leader - Bulgarian Language",
            "International customer support role.",
        ),
        PROFILE,
        SETTINGS,
    )
    assert result.verdict == "blocked"
    assert any("Bulgarian" in x for x in result.explanations)


def test_team_leader_title_is_lead_level():
    result = score_job(
        make_job(
            "Customer Support Team Leader",
            "Customer support.",
        ),
        PROFILE,
        SETTINGS,
    )
    assert result.verdict == "blocked"
    assert any("lead" in x.lower() for x in result.explanations)


def test_inside_sales_is_non_target():
    result = score_job(
        make_job(
            "Inside Sales Contractor",
            "Business-to-business sales.",
        ),
        PROFILE,
        SETTINGS,
    )
    assert result.verdict == "blocked"
    assert any("non-target role family" in x.lower() for x in result.explanations)


def test_business_development_representative_is_non_target():
    result = score_job(
        make_job(
            "Business Development Representative",
            "Sales pipeline generation.",
        ),
        PROFILE,
        SETTINGS,
    )
    assert result.verdict == "blocked"
    assert any("non-target role family" in x.lower() for x in result.explanations)


def test_supported_french_title_language_remains_allowed():
    result = score_job(
        make_job(
            "Technical Support Specialist - French",
            "French is required. Technical support and APIs.",
        ),
        PROFILE,
        SETTINGS,
    )
    assert not any("Unsupported mandatory language" in x for x in result.explanations)
