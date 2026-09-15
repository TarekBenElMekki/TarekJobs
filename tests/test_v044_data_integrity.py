from __future__ import annotations

import json
from pathlib import Path

import yaml

from src.matcher import score_job
from src.models import JobRecord
from src.normalize import (
    canonical_job_type,
    parse_date,
    parse_salary_text,
)

ROOT = Path(__file__).resolve().parents[1]
PROFILE = json.loads(
    (ROOT / "config" / "profile.json").read_text(encoding="utf-8")
)
SETTINGS = yaml.safe_load(
    (ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
)


def test_iso_date_does_not_swap_month_and_day():
    dt = parse_date("2026-08-11T20:18:02")
    assert (dt.year, dt.month, dt.day) == (2026, 8, 11)


def test_iso_date_only_does_not_swap_month_and_day():
    dt = parse_date("2026-08-11")
    assert (dt.year, dt.month, dt.day) == (2026, 8, 11)


def test_local_slash_date_remains_day_first():
    dt = parse_date("11/08/2026")
    assert (dt.year, dt.month, dt.day) == (2026, 8, 11)


def test_unix_milliseconds_are_supported():
    seconds = 1786479482
    dt_s = parse_date(seconds)
    dt_ms = parse_date(seconds * 1000)
    assert dt_s == dt_ms


def test_canonical_full_time_aliases():
    assert canonical_job_type("full_time") == "full-time"
    assert canonical_job_type("full-time") == "full-time"
    assert canonical_job_type("FULL TIME") == "full-time"


def test_remote_office_assistant_is_not_an_internship():
    job = JobRecord(
        title="Remote Office Assistant",
        company="Coalition Technologies",
        location="Worldwide",
        description=(
            "Full-time remote office assistant. Entry-level bookkeeping, "
            "billing, reporting and data entry. International team members."
        ),
        employment_type="full_time",
        remote=True,
        remote_region="Worldwide",
        seniority="intern",
        source_name="test",
        source_url="https://example.com/office-assistant",
    )
    result = score_job(job, PROFILE, SETTINGS)
    assert job.seniority != "intern"
    assert job.employment_type == "full-time"
    assert not any("Job type 'full_time'" in x for x in result.explanations)
    assert result.verdict == "blocked"


def test_k_salary_format():
    low, high, currency, period = parse_salary_text(
        "Salary $31.2k - $52k per year"
    )
    assert low == 31200
    assert high == 52000
    assert currency == "USD"
    assert period == "yearly"


def test_comma_decimal_k_salary_format():
    low, high, currency, _ = parse_salary_text(
        "Salary $31,2k - $52k"
    )
    assert low == 31200
    assert high == 52000
    assert currency == "USD"
