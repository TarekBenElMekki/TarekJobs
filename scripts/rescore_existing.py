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


def main() -> int:
    profile = json.loads((ROOT / "config" / "profile.json").read_text(encoding="utf-8"))
    settings = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    db_path = ROOT / settings.get("storage", {}).get("sqlite_path", "data/jobs.db")
    engine = get_engine(str(db_path))

    changed = 0
    with Session(engine) as session:
        jobs = session.scalars(select(JobORM).options(selectinload(JobORM.sources))).all()
        for job in jobs:
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
                source_url=source.source_url if source else (job.canonical_url or f"stored:{job.id}"),
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
            job.score_explanation_json = json.dumps(scores.explanations, ensure_ascii=False)
            changed += 1
        session.commit()

    export_path = ROOT / settings.get("storage", {}).get("export_top_matches_csv", "data/exports/top_matches.csv")
    exported = export_top_matches(engine, export_path)
    print(f"Rescored {changed} stored canonical jobs.")
    print(f"Exported {exported} live jobs to {export_path}.")
    show_top_jobs(engine)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
