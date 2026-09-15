from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

from src.collectors.keejob import KeejobCollector
from src.matcher import score_job
from src.models import JobRecord

ROOT = Path(__file__).resolve().parents[1]
PROFILE = json.loads((ROOT / "config" / "profile.json").read_text(encoding="utf-8"))
SETTINGS = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))


def test_keejob_company_from_canonical_company_link():
    soup = BeautifulSoup("""
    <html><head><title>IngÃ©nieur infrastructure</title></head><body>
    <h1>IngÃ©nieur infrastructure</h1>
    <a href="/offres-emploi/companies/123/tlf/">TUNISIE LEASING ET FACTORING</a>
    <a href="/secteurs/x/">banque / finance / assurances</a>
    </body></html>
    """, "html.parser")
    assert KeejobCollector._company_from_page(soup, "IngÃ©nieur infrastructure") == "TUNISIE LEASING ET FACTORING"


def test_keejob_publication_date_prefers_explicit_publication():
    soup = BeautifulSoup("""
    <html><body>
    <div>Date de publication</div><div>11 aoÃ»t 2026</div>
    <div>Expiration</div><div>16 novembre 2026</div>
    </body></html>
    """, "html.parser")
    lines = [x.strip() for x in soup.stripped_strings]
    raw, parsed = KeejobCollector._publication_from_page(soup, lines)
    assert raw == "11 aoÃ»t 2026"
    assert parsed is not None
    assert (parsed.year, parsed.month, parsed.day) == (2026, 8, 11)


def test_future_publication_date_is_rejected():
    future = datetime.now(timezone.utc) + timedelta(days=30)
    raw = future.strftime("%d/%m/%Y")
    soup = BeautifulSoup(f"<html><body><div>Date de publication</div><div>{raw}</div></body></html>", "html.parser")
    lines = [x.strip() for x in soup.stripped_strings]
    _, parsed = KeejobCollector._publication_from_page(soup, lines)
    assert parsed is None


def test_optional_missing_metadata_does_not_force_verification():
    job = JobRecord(
        title="IngÃ©nieur infrastructure",
        company="TUNISIE LEASING ET FACTORING",
        location="El Menzah, Tunis, Tunisie",
        description="Administration Linux rÃ©seaux sauvegarde documentation technique. Master Bac+5. Au moins deux ans d'expÃ©rience.",
        employment_type="full-time",
        remote=False,
        posted_at=None,
        source_name="test",
        source_url="https://example.com/job",
    )
    result = score_job(job, PROFILE, SETTINGS)
    assert not any(x.startswith("VERIFY:") for x in result.explanations)
    assert result.verdict == "actionable"


def test_salary_sensitive_unknown_salary_still_verifies():
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
    assert any("Salary-sensitive role" in x for x in result.explanations)
    assert result.verdict == "needs_verification"