import json
from pathlib import Path

import yaml

from src.collectors.keejob import KeejobCollector
from src.matcher import _location_score, score_job
from src.models import JobRecord
from src.normalize import clean_text, extract_years

ROOT = Path(__file__).resolve().parents[1]
PROFILE = json.loads((ROOT / "config" / "profile.json").read_text(encoding="utf-8"))
SETTINGS = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))


def make_job(location: str) -> JobRecord:
    return JobRecord(
        title="Junior Software Engineer",
        company="Test",
        location=location,
        description="Python required. Requires 1 year of experience.",
        employment_type="full-time",
        remote=False,
        source_name="test",
        source_url="https://example.com/job",
    )


def test_year_range_with_hyphen_is_preserved():
    assert extract_years("3-5 years experience") == (3, 5)


def test_french_year_range_is_preserved():
    assert extract_years("Entre 5 et 10 ans") == (5, 10)


def test_plain_url_cleaning_does_not_need_html_parsing():
    assert clean_text("https://example.com/a/b") == "https://example.com/a/b"


def test_sousse_is_not_misread_as_tunis():
    score, reasons = _location_score(make_job("Kalaa El Kebira, Sousse, Tunisie"), PROFILE)
    assert score == 55
    assert any("historically preferred Greater Tunis" in reason for reason in reasons)


def test_sousse_job_is_retained_for_global_campaign():
    result = score_job(make_job("Kalaa El Kebira, Sousse, Tunisie"), PROFILE, SETTINGS)
    assert result.location == 55
    assert result.verdict != "blocked"


def test_ariana_remains_accepted():
    score, reasons = _location_score(make_job("Ariana Ville, Ariana, Tunisie"), PROFILE)
    assert score >= 85


def test_generic_tunisia_requires_city_verification_signal():
    result = score_job(make_job("Tunisie"), PROFILE, SETTINGS)
    assert result.location == 70
    assert any("city is not specified" in x for x in result.explanations)


def test_keejob_sector_labels_are_not_companies():
    assert KeejobCollector._looks_like_sector_label("informatique / tÃ©lÃ©coms")
    assert KeejobCollector._looks_like_sector_label("banque / finance / assurances")
    assert not KeejobCollector._looks_like_sector_label("PARTNER RECRUITMENT")