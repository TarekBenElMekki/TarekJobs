from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

from src.collectors.keejob import KeejobCollector
from src.matcher import score_job
from src.models import JobRecord
from src.normalize import clean_text, extract_years

ROOT = Path(__file__).resolve().parents[1]
PROFILE = json.loads((ROOT / "config" / "profile.json").read_text(encoding="utf-8"))
SETTINGS = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))


def test_clean_text_plain_tokens_are_safe():
    assert clean_text("Italian") == "Italian"
    assert clean_text("fran\u00e7ais") == "fran\u00e7ais"
    assert clean_text("https://example.com/a/b") == "https://example.com/a/b"


def test_year_ranges_still_work():
    assert extract_years("3-5 years experience") == (3, 5)
    assert extract_years("Entre 5 et 10 ans") == (5, 10)


def test_keejob_company_from_enterprise_section():
    html = """
    <html><body>
      <h1>Ing\u00e9nieur infrastructure</h1>
      <h2>Entreprise</h2>
      <h3>TUNISIE LEASING ET FACTORING</h3>
      <div>Secteur: banque / finance / assurances</div>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    assert (
        KeejobCollector._company_from_page(soup, "Ing\u00e9nieur infrastructure")
        == "TUNISIE LEASING ET FACTORING"
    )


def test_keejob_publication_uses_main_header_date():
    html = """
    <html><body>
      <h1>Technicien Maintenance Smartphone</h1>
      <div>FLASH PHONE Ariana Ville, Ariana Publi\u00e9e le 11 ao\u00fbt 2026</div>
      <h3>Date de publication</h3>
      <div>11 ao\u00fbt 2026</div>
      <div>Expiration 16 novembre 2026</div>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    lines = [x.strip() for x in soup.stripped_strings]
    raw, parsed = KeejobCollector._publication_from_page(soup, lines)
    assert parsed is not None
    assert (parsed.year, parsed.month, parsed.day) == (2026, 8, 11)


def test_future_publication_is_rejected():
    future = datetime.now(timezone.utc) + timedelta(days=30)
    text = future.strftime("%d %B %Y")
    soup = BeautifulSoup(
        f"<html><body><div>Published on {text}</div><h3>Date de publication</h3><div>{text}</div></body></html>",
        "html.parser",
    )
    lines = [x.strip() for x in soup.stripped_strings]
    _, parsed = KeejobCollector._publication_from_page(soup, lines)
    assert parsed is None


def test_normal_full_time_job_can_be_actionable_without_salary():
    job = JobRecord(
        title="Ing\u00e9nieur infrastructure",
        company="TUNISIE LEASING ET FACTORING",
        location="El Menzah, Tunis, Tunisie",
        description=(
            "Administration Linux reseaux sauvegarde documentation technique. "
            "Master Bac+5. Experience professionnelle d'au moins deux ans."
        ),
        employment_type="full-time",
        remote=False,
        min_years=2,
        max_years=5,
        source_name="test",
        source_url="https://example.com/job",
    )
    result = score_job(job, PROFILE, SETTINGS)
    assert result.verdict == "actionable"


def test_part_time_salary_is_not_a_matching_filter():
    job = JobRecord(
        title="Junior Developer Part Time",
        company="Test",
        location="Tunis, Tunisie",
        description="Python developer entry level.",
        employment_type="part-time",
        remote=False,
        source_name="test",
        source_url="https://example.com/job",
    )
    result = score_job(job, PROFILE, SETTINGS)
    assert result.verdict == "actionable"
    assert not any("salary" in x.lower() for x in result.explanations)
