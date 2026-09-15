from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from src.collectors.keejob import KeejobCollector
from src.database import JobORM, JobSourceORM, get_engine, init_db
from src.main import export_top_matches, show_top_jobs
from src.matcher import score_job
from src.models import JobRecord
from src.normalize import norm_text


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def bootstrap_legacy_history(engine) -> bool:
    history_path = ROOT / "data" / "ui_run_history.json"
    history = load_json(history_path, [])
    if history:
        return False

    with Session(engine) as session:
        total = int(session.scalar(select(func.count(JobORM.id))) or 0)
        source_rows = int(session.scalar(select(func.count(JobSourceORM.id))) or 0)
        stale = int(session.scalar(select(func.count(JobORM.id)).where(JobORM.stale.is_(True))) or 0)
        live = total - stale
        earliest = session.scalar(select(func.min(JobORM.first_seen_at)))
        source_names = [row[0] for row in session.execute(select(JobSourceORM.source_name).distinct()).all()]

    if total == 0:
        return False

    started = earliest.isoformat() if hasattr(earliest, "isoformat") else datetime.now(timezone.utc).isoformat()
    sources = {name: {"status": "success"} for name in source_names}
    history = [{
        "id": "legacy_initial_dry_run",
        "status": "success",
        "started_at": started,
        "finished_at": started,
        "limit": source_rows,
        "summary": {
            "raw_jobs": source_rows,
            "new_jobs": total,
            "duplicates": max(0, source_rows - total),
            "seen_again": 0,
            "stale": stale,
            "collector_failures": 0,
            "live_exported": live,
        },
        "sources": sources,
    }]
    save_json(history_path, history)
    return True


def update_from_record(job: JobORM, source: JobSourceORM, record: JobRecord) -> None:
    job.title = record.title or job.title
    if record.company and norm_text(record.company) not in {"unknown", ""}:
        job.company = record.company
    if record.location:
        job.location = record.location
    if record.description:
        job.description = record.description
    if record.employment_type:
        job.employment_type = record.employment_type
    job.remote = record.remote
    job.remote_region = record.remote_region
    job.min_years = record.min_years
    job.max_years = record.max_years
    job.salary_min = record.salary_min
    job.salary_max = record.salary_max
    job.salary_currency = record.salary_currency
    job.salary_period = record.salary_period
    if record.posted_at:
        job.posted_at = record.posted_at
    job.norm_title = norm_text(job.title)
    job.norm_company = norm_text(job.company)
    job.norm_location = norm_text(job.location)
    job.updated_at = datetime.now(timezone.utc)
    source.raw_payload_json = json.dumps(record.raw_payload, ensure_ascii=False, default=str)


def enrich_keejob(engine, settings: dict) -> tuple[int, int]:
    source_cfg = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))["sources"]["keejob"]
    collector = KeejobCollector(source_cfg, settings)
    ok = 0
    failed = 0
    try:
        with Session(engine) as session:
            rows = session.execute(
                select(JobORM, JobSourceORM)
                .join(JobSourceORM, JobSourceORM.job_id == JobORM.id)
                .where(JobSourceORM.source_name == "keejob")
            ).all()
            for job, source in rows:
                try:
                    record = collector.fetch_detail(source.source_url, job.title, job.description)
                    update_from_record(job, source, record)
                    ok += 1
                    print(f"[KEEJOB-DETAIL] OK   {job.id:>3}  {job.company} | {job.title}")
                except Exception as exc:
                    failed += 1
                    print(f"[KEEJOB-DETAIL] FAIL {job.id:>3}  {type(exc).__name__}: {exc}")
                time.sleep(0.12)
            session.commit()
    finally:
        collector.close()
    return ok, failed


def rescore(engine, profile: dict, settings: dict) -> int:
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
            job.verdict = scores.verdict
            job.score_explanation_json = json.dumps(scores.explanations, ensure_ascii=False)
            job.requirements_json = json.dumps(scores.requirements, ensure_ascii=False, default=str)
            job.required_languages_json = json.dumps(scores.requirements.get("languages", []), ensure_ascii=False, default=str)
            job.extraction_confidence_json = json.dumps(scores.confidence, ensure_ascii=False, default=str)
            changed += 1
        session.commit()
    return changed


def main() -> int:
    settings = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")) or {}
    profile = json.loads((ROOT / "config" / "profile.json").read_text(encoding="utf-8"))
    db_path = ROOT / settings.get("storage", {}).get("sqlite_path", "data/jobs.db")
    engine = get_engine(str(db_path))
    init_db(engine)

    imported = bootstrap_legacy_history(engine)
    print(f"Legacy run ledger imported: {'YES' if imported else 'already present / not needed'}")

    print("\nRe-enriching existing Keejob records from their detail pages...")
    ok, failed = enrich_keejob(engine, settings)
    print(f"Keejob detail enrichment: {ok} succeeded, {failed} failed")

    changed = rescore(engine, profile, settings)
    print(f"Rescored {changed} canonical jobs with V0.4 rules.")

    export_path = ROOT / settings.get("storage", {}).get("export_top_matches_csv", "data/exports/top_matches.csv")
    exported = export_top_matches(engine, export_path)
    print(f"Exported {exported} live jobs to {export_path}.")
    show_top_jobs(engine)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())