from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from src.database import JobORM, get_engine
from src.main import export_top_matches, show_top_jobs
from src.matcher import score_job
from src.models import JobRecord
from src.normalize import norm_text


SECTOR_SIGNALS = {
    "informatique", "telecoms", "telecom", "banque", "finance", "assurances",
    "industrie", "production", "commerce", "distribution", "marketing",
    "communication", "sante", "medical", "pharmaceutique", "transport",
    "logistique", "tourisme", "hotellerie", "restauration",
}


def looks_like_sector_label(value: str) -> bool:
    if "/" not in (value or ""):
        return False
    return bool(set(norm_text(value).split()).intersection(SECTOR_SIGNALS))


def main() -> int:
    settings_path = ROOT / "config" / "settings.yaml"
    profile_path = ROOT / "config" / "profile.json"

    settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    profile = json.loads(profile_path.read_text(encoding="utf-8"))

    settings.setdefault("project", {})["version"] = "0.4.1"
    settings.setdefault("matching", {}).setdefault("ranking_caps", {})[
        "local_outside_allowed"
    ] = 15

    settings_path.write_text(
        yaml.safe_dump(
            settings,
            sort_keys=False,
            allow_unicode=True,
            width=120,
        ),
        encoding="utf-8",
    )

    db_path = ROOT / settings.get("storage", {}).get("sqlite_path", "data/jobs.db")
    engine = get_engine(str(db_path))

    sanitised = 0
    rescored = 0

    with Session(engine) as session:
        jobs = session.scalars(
            select(JobORM).options(selectinload(JobORM.sources))
        ).all()

        for job in jobs:
            # Some Keejob pages hide the employer and expose a sector link in
            # the same visual position. Do not present that sector as a company.
            if looks_like_sector_label(job.company):
                job.company = "Confidential employer"
                job.norm_company = norm_text(job.company)
                sanitised += 1

            source = job.sources[0] if job.sources else None
            record = JobRecord(
                title=job.title,
                company=job.company,
                location=job.location,
                description=job.description,
                employment_type=job.employment_type,
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
                source_name=source.source_name if source else "stored",
                source_url=source.source_url if source else (
                    job.canonical_url or f"stored:{job.id}"
                ),
            )

            scores = score_job(record, profile, settings)

            job.seniority = record.seniority
            job.employment_type = record.employment_type
            job.min_years = record.min_years
            job.max_years = record.max_years

            job.technical_score = scores.technical
            job.eligibility_score = scores.eligibility
            job.location_score = scores.location
            job.experience_score = scores.experience
            job.overall_score = scores.overall
            job.verdict = scores.verdict
            job.score_explanation_json = json.dumps(
                scores.explanations, ensure_ascii=False
            )
            job.requirements_json = json.dumps(
                scores.requirements, ensure_ascii=False, default=str
            )
            job.required_languages_json = json.dumps(
                scores.requirements.get("languages", []),
                ensure_ascii=False,
                default=str,
            )
            job.extraction_confidence_json = json.dumps(
                scores.confidence, ensure_ascii=False, default=str
            )
            rescored += 1

        session.commit()

    export_path = ROOT / settings.get("storage", {}).get(
        "export_top_matches_csv",
        "data/exports/top_matches.csv",
    )
    exported = export_top_matches(engine, export_path)

    print(f"Sanitised sector-as-company values: {sanitised}")
    print(f"Re-scored canonical jobs: {rescored}")
    print(f"Exported live jobs: {exported}")
    print()
    show_top_jobs(engine)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())