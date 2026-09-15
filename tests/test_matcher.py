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
    assert result.location >= 85
    assert result.experience == 100
    assert result.overall >= 65
    assert result.eligibility_status == "eligible"


def test_internship_is_blocked_but_fit_is_not_artificially_capped():
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
    assert result.eligibility_status == "blocked"
    assert result.verdict == "blocked"
    assert result.overall >= 0  # fit remains independent from the hard gate


def test_senior_title_is_blocked_by_eligibility_not_score_cap():
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
    assert result.experience <= 25
    assert result.visa <= 15
    assert result.verdict == "blocked"


def test_manager_title_is_blocked():
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
    assert result.verdict == "blocked"


def test_tier1_international_onsite_role_is_now_targeted():
    profile, settings = load()
    job = JobRecord(
        title="Junior Embedded Software Engineer",
        company="Example",
        location="Berlin, Germany",
        description="C++ embedded Linux ROS2. 1 year experience.",
        remote=False,
        source_name="test",
        source_url="https://example.com/5",
    )
    result = score_job(job, profile, settings)
    assert result.location == 100
    assert 40 <= result.visa < 100  # unknown sponsorship is a verification state
    assert result.eligibility_status == "needs_verification"
    assert result.overall >= 65


def test_explicit_sponsorship_makes_tier1_role_eligible():
    profile, settings = load()
    job = JobRecord(
        title="Junior Robotics Engineer",
        company="Example",
        location="Eindhoven, Netherlands",
        description="ROS2 C++ robotics and sensors. Visa sponsorship provided.",
        remote=False,
        visa_sponsorship=True,
        source_name="test",
        source_url="https://example.com/6",
    )
    result = score_job(job, profile, settings)
    assert result.location == 100
    assert result.visa == 100
    assert result.eligibility_status == "eligible"


def test_region_restricted_remote_role_is_blocked():
    profile, settings = load()
    job = JobRecord(
        title="Junior Software Engineer",
        company="Example",
        location="USA",
        remote=True,
        description="Python. 1 year experience.",
        source_name="test",
        source_url="https://example.com/7",
    )
    result = score_job(job, profile, settings)
    assert result.location <= 15
    assert result.visa <= 5
    assert result.verdict == "blocked"


def test_non_target_graphic_designer_is_blocked_without_destroying_fit_model():
    profile, settings = load()
    job = JobRecord(
        title="Senior Graphic Designer",
        company="Example",
        location="Worldwide",
        remote=True,
        description="Uses software and works with product engineering teams.",
        source_name="test",
        source_url="https://example.com/8",
    )
    result = score_job(job, profile, settings)
    assert result.technical <= 15
    assert result.domain <= 10
    assert result.verdict == "blocked"
