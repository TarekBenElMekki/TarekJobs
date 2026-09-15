from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
from src.normalize import (
    canonical_job_type,
    detect_seniority,
    norm_text,
    parse_date,
)


DATE_KEYS_BY_SOURCE = {
    "remotive": [
        "publicationdate",
        "publishedat",
        "dateposted",
        "postedat",
        "createdat",
    ],
    "jobicy": [
        "pubdate",
        "dateposted",
        "publishedat",
        "publicationdate",
        "createdat",
    ],
    "arbeitnow": [
        "createdat",
        "publishedat",
        "publicationdate",
        "dateposted",
        "postedat",
    ],
}

GENERIC_DATE_KEYS = [
    "publicationdate",
    "pubdate",
    "publishedat",
    "dateposted",
    "postedat",
    "createdat",
]


def key_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def iter_key_values(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key_id(key), child
            yield from iter_key_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_key_values(child)


def raw_posted_at(source_name: str, raw_json: str):
    try:
        payload = json.loads(raw_json or "{}")
    except Exception:
        return None, None

    collected = list(iter_key_values(payload))
    priorities = DATE_KEYS_BY_SOURCE.get(source_name, []) + GENERIC_DATE_KEYS

    seen = set()
    for wanted in priorities:
        if wanted in seen:
            continue
        seen.add(wanted)

        for key, value in collected:
            if key != wanted or value in (None, ""):
                continue

            parsed = parse_date(value)
            if parsed is None:
                continue

            return parsed, value

    return None, None


def aware(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


settings_path = ROOT / "config" / "settings.yaml"
profile_path = ROOT / "config" / "profile.json"

settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
settings.setdefault("project", {})["version"] = "0.4.4"
settings_path.write_text(
    yaml.safe_dump(
        settings,
        sort_keys=False,
        allow_unicode=True,
        width=120,
    ),
    encoding="utf-8",
)

profile = json.loads(profile_path.read_text(encoding="utf-8"))
db_path = ROOT / settings.get("storage", {}).get("sqlite_path", "data/jobs.db")
engine = get_engine(str(db_path))

now = datetime.now(timezone.utc)
future_limit = now + timedelta(days=1)

dates_repaired = 0
future_cleared = 0
seniority_repaired = 0
types_repaired = 0
date_examples = []

with Session(engine) as session:
    jobs = session.scalars(
        select(JobORM).options(selectinload(JobORM.sources))
    ).all()

    for job in jobs:
        # Keejob was already refreshed directly from detail pages in V0.4.3.
        # Repair API-source dates from their preserved original raw payload.
        source_candidates = [
            source for source in job.sources
            if source.source_name != "keejob"
        ]

        repaired_date = None
        repaired_raw = None
        repaired_source = None

        for source in source_candidates:
            candidate, raw_value = raw_posted_at(
                source.source_name,
                source.raw_payload_json,
            )
            candidate = aware(candidate)
            if candidate is None or candidate > future_limit:
                continue

            repaired_date = candidate
            repaired_raw = raw_value
            repaired_source = source.source_name
            break

        current = aware(job.posted_at)

        if repaired_date is not None:
            if current is None or abs((current - repaired_date).total_seconds()) > 1:
                old_text = current.isoformat() if current else "None"
                job.posted_at = repaired_date
                dates_repaired += 1
                if len(date_examples) < 12:
                    date_examples.append(
                        (
                            job.id,
                            repaired_source,
                            old_text,
                            repaired_date.isoformat(),
                            str(repaired_raw)[:80],
                        )
                    )
        elif current is not None and current > future_limit:
            job.posted_at = None
            future_cleared += 1

        # Recompute high-impact derived fields instead of retaining stale
        # inference values from old collectors.
        title_seniority = detect_seniority(job.title or "")
        if (job.seniority or "") != title_seniority:
            job.seniority = title_seniority
            seniority_repaired += 1

        canonical_type = canonical_job_type(job.employment_type, job.remote)
        if canonical_type and canonical_type != (job.employment_type or ""):
            job.employment_type = canonical_type
            types_repaired += 1

    session.commit()

print(f"Source dates repaired: {dates_repaired}")
print(f"Impossible future dates cleared: {future_cleared}")
print(f"Derived seniority values repaired: {seniority_repaired}")
print(f"Employment-type spellings canonicalized: {types_repaired}")

for job_id, source, old, new, raw in date_examples:
    print(
        f"[DATE-REPAIR] job={job_id} source={source} "
        f"{old} -> {new} | raw={raw}"
    )

print()
print("Re-scoring all jobs with corrected derived fields...")

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
            scores.explanations,
            ensure_ascii=False,
        )
        job.requirements_json = json.dumps(
            scores.requirements,
            ensure_ascii=False,
            default=str,
        )
        job.required_languages_json = json.dumps(
            scores.requirements.get("languages", []),
            ensure_ascii=False,
            default=str,
        )
        job.extraction_confidence_json = json.dumps(
            scores.confidence,
            ensure_ascii=False,
            default=str,
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
