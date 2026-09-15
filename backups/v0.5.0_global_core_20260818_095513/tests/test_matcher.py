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


def test_internship_is_not_eligible_and_cannot_rank_high():
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
    assert result.overall <= 20


def test_senior_title_overrides_associate_in_description():
    profile, settings = load()
    job = JobRecord(
        title="Sr Customer Success Manager",
        company="Example",
        location="USA",
        description="Works with associate teams and software customers. 5 years experience.",
        remote=True,
        source_name="test",
        source_url="https://example.com/3",
    )
    result = score_job(job, profile, settings)
    assert job.seniority == "senior"
    assert result.experience == 0
    assert result.eligibility <= 10
    assert result.location <= 10
    assert result.overall <= 30


def test_manager_title_cannot_score_100():
    profile, settings = load()
    job = JobRecord(
        title="Enterprise Customer Success Manager",
        company="Example",
        location="Anywhere",
        description="Technical SaaS software support with associate stakeholders.",
        remote=True,
        source_name="test",
        source_url="https://example.com/4",
    )
    result = score_job(job, profile, settings)
    assert job.seniority == "manager"
    assert result.overall <= 30


def test_international_onsite_role_is_retained_but_ranked_low():
    profile, settings = load()
    job = JobRecord(
        title="Data Analyst",
        company="Example",
        location="Berlin, Germany",
        description="Python SQL data analysis. 1 year experience.",
        remote=False,
        source_name="test",
        source_url="https://example.com/5",
    )
    result = score_job(job, profile, settings)
    assert result.location == 0
    assert result.overall <= 15


def test_region_restricted_remote_role_is_ranked_low():
    profile, settings = load()
    job = JobRecord(
        title="Junior Software Engineer",
        company="Example",
        location="USA",
        remote=True,
        description="Python. 1 year experience.",
        source_name="test",
        source_url="https://example.com/6",
    )
    result = score_job(job, profile, settings)
    assert result.location <= 10
    assert result.overall <= 30


def test_non_target_graphic_designer_is_kept_but_capped():
    profile, settings = load()
    job = JobRecord(
        title="Senior Graphic Designer",
        company="Example",
        location="Worldwide",
        remote=True,
        description="Uses software and works with product engineering teams.",
        source_name="test",
        source_url="https://example.com/7",
    )
    result = score_job(job, profile, settings)
    assert result.technical <= 15
    assert result.overall <= 25
