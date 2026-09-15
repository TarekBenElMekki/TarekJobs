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


def make_part_time(salary=None):
    return JobRecord(
        title="Junior Python Developer",
        company="Test Company",
        location="Tunis, Tunisie",
        description="Python developer. Entry level. REST API. Git.",
        employment_type="part-time",
        remote=False,
        salary_min=salary,
        salary_max=salary,
        salary_currency="TND" if salary is not None else None,
        salary_period="monthly" if salary is not None else None,
        source_name="test",
        source_url="https://example.com/job",
    )


def test_unknown_salary_does_not_create_verification_rule():
    result = score_job(make_part_time(None), PROFILE, SETTINGS)
    assert not any("salary" in reason.lower() for reason in result.explanations)


def test_low_salary_does_not_block_or_cap_job():
    no_salary = score_job(make_part_time(None), PROFILE, SETTINGS)
    low_salary = score_job(make_part_time(500), PROFILE, SETTINGS)

    assert low_salary.eligibility == no_salary.eligibility
    assert low_salary.overall == no_salary.overall
    assert low_salary.verdict == no_salary.verdict


def test_profile_salary_preference_is_not_needed_for_matching():
    profile = json.loads(json.dumps(PROFILE))
    profile.pop("salary_preferences", None)
    result = score_job(make_part_time(None), profile, SETTINGS)
    assert result.eligibility > 0
