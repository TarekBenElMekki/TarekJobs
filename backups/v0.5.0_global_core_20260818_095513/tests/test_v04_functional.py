import json
from pathlib import Path

import yaml

from src.matcher import score_job
from src.models import JobRecord
from src.normalize import detect_seniority, extract_years, parse_salary_text

ROOT = Path(__file__).resolve().parents[1]
PROFILE = json.loads((ROOT / 'config' / 'profile.json').read_text(encoding='utf-8'))
SETTINGS = yaml.safe_load((ROOT / 'config' / 'settings.yaml').read_text(encoding='utf-8'))


def make_job(title, description='', **kwargs):
    return JobRecord(
        title=title,
        description=description,
        company=kwargs.pop('company', 'Test'),
        location=kwargs.pop('location', 'Tunis'),
        source_name='test',
        source_url='https://example.com/' + str(abs(hash(title))),
        **kwargs,
    )


def test_year_is_not_salary_without_pay_context():
    assert parse_salary_text('Tunis 14 aoÃ»t 2026')[:2] == (None, None)


def test_year_is_excluded_from_real_salary_range():
    low, high, currency, period = parse_salary_text('CDI 1400 - 1800 TND Tunis 16 aoÃ»t 2026')
    assert (low, high, currency) == (1400.0, 1800.0, 'TND')


def test_keejob_monthly_salary_format():
    low, high, currency, period = parse_salary_text('Salaire proposÃ© 800 - 1200 TND / Mois')
    assert (low, high, currency, period) == (800.0, 1200.0, 'TND', 'monthly')


def test_ai_token_does_not_match_italien():
    job = make_job('SDA italien ou allemand et anglais', 'technicien support informatique')
    result = score_job(job, PROFILE, SETTINGS)
    assert not any('Technical terms: ai' in reason for reason in result.explanations)


def test_unsupported_mandatory_language_blocks_job():
    job = make_job('SDA italien ou allemand et anglais', 'technicien support informatique')
    result = score_job(job, PROFILE, SETTINGS)
    assert result.eligibility == 0
    assert result.verdict == 'blocked'
    assert any('Mandatory language not in profile' in reason for reason in result.explanations)


def test_optional_language_does_not_block():
    job = make_job('Junior Software Engineer', 'Python required. German is a plus.')
    result = score_job(job, PROFILE, SETTINGS)
    assert result.eligibility > 0


def test_french_supervisory_titles_are_detected():
    assert detect_seniority("chef d'equipe plasturgie") == 'lead'
    assert detect_seniority('Superviseur support IT') == 'lead'
    assert detect_seniority('Responsable infrastructure') == 'manager'


def test_french_experience_range():
    assert extract_years('ExpÃ©rience requise Entre 5 et 10 ans') == (5, 10)


def test_senior_french_role_is_not_actionable():
    job = make_job("Chef d'equipe plasturgie", '5 ans experience', company='Factory')
    result = score_job(job, PROFILE, SETTINGS)
    assert result.overall <= SETTINGS.get('matching', {}).get('ranking_caps', {}).get('senior_title', 30)
    assert result.verdict == 'blocked'