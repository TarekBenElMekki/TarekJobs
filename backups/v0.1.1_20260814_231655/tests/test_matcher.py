import json
from pathlib import Path

import yaml

from src.matcher import score_job
from src.models import JobRecord

ROOT = Path(__file__).resolve().parents[1]


def load():
    profile = json.loads((ROOT / "config" / "profile.json").read_text(encoding="utf-8"))
    settings = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    return profile, settings


def test_good_local_junior_role_scores_well():
    profile, settings = load()
    job = JobRecord(
        title="Junior Embedded Software Engineer",
        company="Example",
        location="Ariana",
        description="Python C++ embedded systems testing. 1 year experience.",
        employment_type="full-time",
        remote=False,
        source_name="test",
        source_url="https://example.com/1",
    )
    result = score_job(job, profile, settings)
    assert result.technical >= 50
    assert result.location == 100
    assert result.experience == 100
    assert result.overall >= 70


def test_internship_is_not_eligible():
    profile, settings = load()
    job = JobRecord(
        title="Software Engineering Intern",
        company="Example",
        location="Tunis",
        description="Python internship",
        employment_type="internship",
        source_name="test",
        source_url="https://example.com/2",
    )
    result = score_job(job, profile, settings)
    assert result.eligibility == 0


def test_senior_role_is_penalized_but_kept():
    profile, settings = load()
    job = JobRecord(
        title="Senior Software Engineer",
        company="Example",
        location="Worldwide",
        description="Python C++ 5 years experience",
        remote=True,
        source_name="test",
        source_url="https://example.com/3",
    )
    result = score_job(job, profile, settings)
    assert result.experience == 0
    assert result.eligibility <= 25