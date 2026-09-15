from __future__ import annotations

import json
from pathlib import Path

import yaml
from sqlalchemy import inspect

from src.database import get_engine, init_db
from src.matcher import score_job
from src.models import JobRecord

ROOT = Path(__file__).resolve().parents[1]
PROFILE = json.loads((ROOT / "config" / "profile.json").read_text(encoding="utf-8"))
SETTINGS = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))


def test_weights_are_exactly_tarekfit_v2():
    weights = SETTINGS["matching"]["tarek_fit_weights"]
    assert weights == {
        "technical": 0.30,
        "experience": 0.20,
        "education": 0.10,
        "domain": 0.10,
        "visa": 0.10,
        "salary": 0.10,
        "location": 0.05,
        "language": 0.05,
    }
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_schema_migration_is_idempotent(tmp_path):
    engine = get_engine(str(tmp_path / "jobs.db"))
    init_db(engine)
    init_db(engine)
    cols = {x["name"] for x in inspect(engine).get_columns("jobs")}
    for name in [
        "opportunity_type", "country", "deadline", "visa_status", "funding_status",
        "education_score", "domain_score", "visa_score", "salary_score", "language_score",
        "priority", "eligibility_status", "cover_letter_version", "interview_stage",
    ]:
        assert name in cols


def test_funded_robotics_phd_scores_as_strong_opportunity():
    job = JobRecord(
        opportunity_type="phd",
        title="PhD Researcher in Assistive Robotics and Shared Autonomy",
        company="Example University",
        location="Eindhoven, Netherlands",
        description=(
            "Fully funded doctoral researcher position in ROS2, C++, robot control, "
            "human-robot interaction, sensors and medical rehabilitation robotics. "
            "International candidates are welcome and visa sponsorship is provided."
        ),
        employment_type="doctoral",
        funding_status="fully funded salaried employment",
        tuition_covered=True,
        visa_sponsorship=True,
        international_candidates=True,
        salary_min=3200,
        salary_currency="EUR",
        salary_period="monthly",
        source_name="test",
        source_url="https://example.edu/phd/1",
    )
    result = score_job(job, PROFILE, SETTINGS)
    assert result.education == 100
    assert result.visa == 100
    assert result.salary >= 95
    assert result.domain >= 85
    assert result.overall >= 75
    assert result.eligibility_status == "eligible"
    assert result.priority in {"A", "B"}


def test_self_funded_phd_is_blocked_but_fit_remains_visible():
    job = JobRecord(
        opportunity_type="phd",
        title="PhD in Medical Robotics",
        company="Example University",
        location="Munich, Germany",
        description="Research in ROS2 robotic manipulation and medical devices.",
        funding_status="self-funded",
        source_name="test",
        source_url="https://example.edu/phd/2",
    )
    result = score_job(job, PROFILE, SETTINGS)
    assert result.technical > 0
    assert result.domain > 0
    assert result.salary == 0
    assert result.eligibility_status == "blocked"
    assert result.verdict == "blocked"


def test_priority_and_eligibility_are_independent():
    job = JobRecord(
        title="Junior Robotics Software Engineer",
        company="Example",
        location="Zurich, Switzerland",
        description="ROS2 C++ Python SLAM robot control. Must have the right to work in Switzerland without sponsorship.",
        min_years=1,
        source_name="test",
        source_url="https://example.com/blocked-high-fit",
    )
    result = score_job(job, PROFILE, SETTINGS)
    assert result.overall >= 65
    assert result.priority in {"A", "B"}
    assert result.eligibility_status == "blocked"
    assert result.verdict == "blocked"
