from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, selectinload

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database import JobORM, get_engine, init_db
from src.main import export_top_matches
from src.matcher import score_job
from src.models import JobRecord


def jf(value: str | None, default):
    try:
        return json.loads(value or "")
    except Exception:
        return default


settings_path = ROOT / "config" / "settings.yaml"
profile_path = ROOT / "config" / "profile.json"
settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
profile = json.loads(profile_path.read_text(encoding="utf-8"))

settings.setdefault("project", {})["version"] = "0.5.0"
settings_path.write_text(
    yaml.safe_dump(settings, sort_keys=False, allow_unicode=True, width=120),
    encoding="utf-8",
)

db_path = ROOT / settings.get("storage", {}).get("sqlite_path", "data/jobs.db")
engine = get_engine(str(db_path))
init_db(engine)

required_columns = {
    "opportunity_type", "country", "deadline", "is_open", "education_score",
    "domain_score", "visa_score", "salary_score", "language_score", "priority",
    "eligibility_status", "funding_status", "tuition_covered", "cover_letter_version",
}
actual = {c["name"] for c in inspect(engine).get_columns("jobs")}
missing = sorted(required_columns - actual)
if missing:
    raise RuntimeError("V0.5 schema migration missing columns: " + ", ".join(missing))

changed = 0
priority_counts: dict[str, int] = {}
eligibility_counts: dict[str, int] = {}
with Session(engine) as session:
    jobs = session.scalars(select(JobORM).options(selectinload(JobORM.sources))).all()
    for job in jobs:
        source = job.sources[0] if job.sources else None
        record = JobRecord(
            opportunity_type=job.opportunity_type or "job",
            title=job.title,
            company=job.company,
            location=job.location,
            country=job.country,
            city=job.city,
            description=job.description,
            employment_type=job.employment_type,
            work_mode=job.work_mode,
            remote=job.remote,
            remote_region=job.remote_region,
            seniority=job.seniority,
            min_years=job.min_years,
            max_years=job.max_years,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            salary_currency=job.salary_currency,
            salary_period=job.salary_period,
            posted_at=job.posted_at,
            deadline=job.deadline,
            last_verified_at=job.last_verified_at,
            is_open=job.is_open,
            education_requirement=job.education_requirement,
            visa_status=job.visa_status or "unknown",
            visa_sponsorship=job.visa_sponsorship,
            international_candidates=job.international_candidates,
            relocation_support=job.relocation_support,
            funding_status=job.funding_status,
            tuition_covered=job.tuition_covered,
            housing_support=job.housing_support,
            insurance_support=job.insurance_support,
            travel_support=job.travel_support,
            benefits=jf(job.benefits_json, {}),
            source_name=source.source_name if source else "stored",
            source_url=source.source_url if source else (job.canonical_url or f"stored:{job.id}"),
        )
        scores = score_job(record, profile, settings)

        job.opportunity_type = record.opportunity_type
        if not job.country:
            job.country = str(scores.requirements.get("country") or "")
        if not job.work_mode:
            job.work_mode = "remote" if job.remote else "onsite_or_hybrid"
        if job.last_verified_at is None:
            job.last_verified_at = job.last_seen_at or datetime.now(timezone.utc)

        job.seniority = record.seniority
        job.employment_type = record.employment_type
        job.min_years = record.min_years
        job.max_years = record.max_years
        job.technical_score = scores.technical
        job.eligibility_score = scores.eligibility
        job.location_score = scores.location
        job.experience_score = scores.experience
        job.education_score = scores.education
        job.domain_score = scores.domain
        job.visa_score = scores.visa
        job.salary_score = scores.salary
        job.language_score = scores.language
        job.overall_score = scores.overall
        job.priority = scores.priority
        job.eligibility_status = scores.eligibility_status
        job.verdict = scores.verdict
        job.score_explanation_json = json.dumps(scores.explanations, ensure_ascii=False)
        job.requirements_json = json.dumps(scores.requirements, ensure_ascii=False, default=str)
        job.required_languages_json = json.dumps(scores.requirements.get("languages", []), ensure_ascii=False, default=str)
        job.extraction_confidence_json = json.dumps(scores.confidence, ensure_ascii=False, default=str)
        job.updated_at = datetime.now(timezone.utc)
        changed += 1
        priority_counts[scores.priority] = priority_counts.get(scores.priority, 0) + 1
        eligibility_counts[scores.eligibility_status] = eligibility_counts.get(scores.eligibility_status, 0) + 1
    session.commit()

export_path = ROOT / settings.get("storage", {}).get("export_top_matches_csv", "data/exports/top_matches.csv")
exported = export_top_matches(engine, export_path)

print("========================================================")
print(" TTEA V0.5.0 - Global Core migration complete")
print("========================================================")
print(f"Database:            {db_path}")
print(f"Canonical rows:      {changed}")
print(f"Live export rows:    {exported}")
print(f"Priority counts:     {priority_counts}")
print(f"Eligibility counts:  {eligibility_counts}")
print(f"CSV:                 {export_path}")
