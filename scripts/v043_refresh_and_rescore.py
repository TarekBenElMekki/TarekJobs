from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from src.collectors.keejob import KeejobCollector
from src.database import JobORM, JobSourceORM, get_engine
from src.main import export_top_matches, show_top_jobs
from src.matcher import score_job
from src.models import JobRecord
from src.normalize import norm_text

settings_path = ROOT / "config" / "settings.yaml"
profile_path = ROOT / "config" / "profile.json"

settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
settings.setdefault("project", {})["version"] = "0.4.3"
settings_path.write_text(
    yaml.safe_dump(settings, sort_keys=False, allow_unicode=True, width=120),
    encoding="utf-8",
)
profile = json.loads(profile_path.read_text(encoding="utf-8"))

db_path = ROOT / settings.get("storage", {}).get("sqlite_path", "data/jobs.db")
engine = get_engine(str(db_path))
source_cfg = yaml.safe_load(
    (ROOT / "config" / "sources.yaml").read_text(encoding="utf-8")
)["sources"]["keejob"]
collector = KeejobCollector(source_cfg, settings)

now = datetime.now(timezone.utc)
ok = 0
failed = 0
cleared_future = 0

print("Refreshing existing Keejob records with Unicode-safe parser...")
try:
    with Session(engine) as session:
        rows = session.execute(
            select(JobORM, JobSourceORM)
            .join(JobSourceORM, JobSourceORM.job_id == JobORM.id)
            .where(JobSourceORM.source_name == "keejob")
        ).all()

        for job, source in rows:
            # Never preserve an impossible future publication date.
            if job.posted_at:
                posted = job.posted_at
                if posted.tzinfo is None:
                    posted = posted.replace(tzinfo=timezone.utc)
                if posted > now + timedelta(days=1):
                    job.posted_at = None
                    cleared_future += 1

            try:
                record = collector.fetch_detail(
                    source.source_url,
                    job.title,
                    job.description,
                )
                job.title = record.title or job.title
                job.company = record.company or job.company
                job.location = record.location or job.location
                job.description = record.description or job.description
                job.employment_type = record.employment_type or job.employment_type
                job.remote = record.remote
                job.remote_region = record.remote_region
                job.min_years = record.min_years
                job.max_years = record.max_years
                job.salary_min = record.salary_min
                job.salary_max = record.salary_max
                job.salary_currency = record.salary_currency
                job.salary_period = record.salary_period
                job.posted_at = record.posted_at
                job.norm_title = norm_text(job.title)
                job.norm_company = norm_text(job.company)
                job.norm_location = norm_text(job.location)
                source.raw_payload_json = json.dumps(
                    record.raw_payload,
                    ensure_ascii=False,
                    default=str,
                )
                ok += 1
                date_text = (
                    record.posted_at.date().isoformat()
                    if record.posted_at
                    else "DATE UNKNOWN"
                )
                print(
                    f"[KEEJOB-V043] OK {job.id:>3} | "
                    f"{job.company} | {date_text}"
                )
            except Exception as exc:
                failed += 1
                print(
                    f"[KEEJOB-V043] FAIL {job.id:>3} | "
                    f"{type(exc).__name__}: {exc}"
                )

            time.sleep(0.12)

        session.commit()
finally:
    try:
        collector.close()
    except Exception:
        pass

print(
    f"Keejob refresh: {ok} succeeded, {failed} failed; "
    f"cleared {cleared_future} impossible future date(s)"
)

print("Re-scoring all canonical jobs...")
with Session(engine) as session:
    jobs = session.scalars(
        select(JobORM).options(selectinload(JobORM.sources))
    ).all()

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
            source_url=(
                source.source_url
                if source
                else (job.canonical_url or f"stored:{job.id}")
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

    session.commit()

export_path = ROOT / settings.get("storage", {}).get(
    "export_top_matches_csv",
    "data/exports/top_matches.csv",
)
exported = export_top_matches(engine, export_path)
print(f"Re-scored {len(jobs)} canonical jobs.")
print(f"Exported {exported} live jobs.")
print()
show_top_jobs(engine)
