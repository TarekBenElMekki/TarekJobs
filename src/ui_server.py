from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import threading
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select, literal_column
from sqlalchemy.orm import Session, selectinload

from .database import JobORM, JobSourceORM, TteaAssessmentORM, TteaRunORM, get_engine, init_db
from .production_write_guard import production_write_guard, ProductionWriteSafetyError, BLOCKED_MESSAGE
from . import application_analytics as analytics
from . import ttea_live
from . import ttea_batch_import as canonical_batches
from .application_assistant import (
    APPLICATION_STATES,
    ALLOWED_TRANSITIONS,
    application_events as aa_application_events,
    document_library as aa_document_library,
    ensure_application as aa_ensure_application,
    get_application as aa_get_application,
    set_application_url as aa_set_application_url,
    transition_application as aa_transition_application,
)
from .matcher import score_job
from .models import JobRecord

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "ui"
SETTINGS_PATH = ROOT / "config" / "settings.yaml"
SOURCES_PATH = ROOT / "config" / "sources.yaml"
SOURCE_REGISTRY_PATH = ROOT / "config" / "source_registry.yaml"
PROFILE_PATH = ROOT / "config" / "profile.json"
POLL_STATE_PATH = ROOT / "data" / "source_poll_state.json"
RUN_STATE_PATH = ROOT / "data" / "ui_run_state.json"
RUN_HISTORY_PATH = ROOT / "data" / "ui_run_history.json"

REVIEW_STATUSES = ["unreviewed", "shortlisted", "dismissed", "not_eligible"]
APPLICATION_STATUSES = list(APPLICATION_STATES)
VERDICTS = ["actionable", "needs_verification", "blocked"]

RUN_LOCK = threading.Lock()
RUN_THREAD: threading.Thread | None = None
RUN_PROCESS: subprocess.Popen | None = None


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _json_field(value: str | None, default):
    try:
        return json.loads(value or "")
    except Exception:
        return default


settings = _load_yaml(SETTINGS_PATH)
db_path = ROOT / settings.get("storage", {}).get("sqlite_path", "data/jobs.db")
engine = get_engine(str(db_path))
# Configuration identifies production independently of an injected scratch engine.
PRODUCTION_DB_PATH = db_path.resolve()


@contextmanager
def _production_write_scope(reason: str, *, target_db=None):
    target = engine.url.database if target_db is None else target_db
    try:
        with production_write_guard(target, production_db_path=PRODUCTION_DB_PATH, reason=reason):
            yield
    except ProductionWriteSafetyError as exc:
        raise HTTPException(status_code=503, detail=BLOCKED_MESSAGE) from exc


def _guard_production_write(reason: str):
    def decorate(function):
        @wraps(function)
        def guarded(*args, **kwargs):
            with _production_write_scope(reason):
                return function(*args, **kwargs)
        return guarded
    return decorate


@asynccontextmanager
async def lifespan(app: FastAPI):
    with _production_write_scope("server.initialization"):
        init_db(engine)
    yield


app = FastAPI(title="TTEA Dashboard API", version="0.5.0", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=UI_DIR), name="assets")


class JobUpdate(BaseModel):
    review_status: str | None = None
    application_status: str | None = None
    notes: str | None = None
    cv_version: str | None = None
    cover_letter_version: str | None = None
    response: str | None = None
    interview_stage: str | None = None


class ApplicationEnsureRequest(BaseModel):
    source: str = "dashboard"


class ApplicationLinkRequest(BaseModel):
    url: str
    source: str = "chrome_sidepanel_v1"

class ApplicationTransitionRequest(BaseModel):
    state: str
    note: str | None = None
    source: str = "dashboard"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApplicationSessionStartRequest(BaseModel):
    client_id: str = Field(min_length=36, max_length=36)
    session_token: str = Field(min_length=36, max_length=36)
    timezone: str = Field(default="UTC", max_length=100)
    extension_version: str | None = Field(default=None, max_length=40)


class ApplicationSessionEndRequest(BaseModel):
    reason: Literal["last_window_closed"] = "last_window_closed"

class RunRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=1000)
    ignore_poll_interval: bool = False
    sources: list[str] = Field(default_factory=list)


class ProfileUpdate(BaseModel):
    profile: dict[str, Any]
    matching: dict[str, Any] = Field(default_factory=dict)
    collection: dict[str, Any] = Field(default_factory=dict)


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _ttea_iso(value) -> str | None:
    # SQLite returns naive datetimes even for timezone=True; the importer stores UTC.
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return _iso(value)


def _reason_groups(explanations: list[str]) -> dict[str, list[str]]:
    groups = {"blockers": [], "matches": [], "verification": [], "rules": []}
    for raw in explanations:
        if raw.startswith("BLOCKER:"):
            groups["blockers"].append(raw.split(":", 1)[1].strip())
        elif raw.startswith("MATCH:"):
            groups["matches"].append(raw.split(":", 1)[1].strip())
        elif raw.startswith("VERIFY:"):
            groups["verification"].append(raw.split(":", 1)[1].strip())
        elif raw.startswith("RULE:"):
            groups["rules"].append(raw.split(":", 1)[1].strip())
        else:
            groups["verification"].append(raw)
    return groups


def _job_payload(job: JobORM, include_description: bool = False) -> dict:
    explanations = _json_field(job.score_explanation_json, [])
    requirements = _json_field(job.requirements_json, {})
    required_languages = _json_field(job.required_languages_json, [])
    confidence = _json_field(job.extraction_confidence_json, {})
    reason_groups = _reason_groups(explanations)
    latest = job.ttea_assessments[0] if job.ttea_assessments else None

    payload = {
        "id": job.id,
        "opportunity_type": job.opportunity_type or "job",
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "country": job.country,
        "city": job.city,
        "work_mode": job.work_mode,
        "employment_type": job.employment_type,
        "remote": bool(job.remote),
        "remote_region": job.remote_region,
        "seniority": job.seniority,
        "min_years": job.min_years,
        "max_years": job.max_years,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency,
        "salary_period": job.salary_period,
        "posted_at": _iso(job.posted_at),
        "deadline": _iso(job.deadline),
        "last_verified_at": _iso(job.last_verified_at),
        "is_open": bool(job.is_open),
        "stale": bool(job.stale),
        "priority": job.priority or "discard",
        "eligibility_status": job.eligibility_status or "needs_verification",
        "verdict": job.verdict or "needs_verification",
        "review_status": job.review_status or "unreviewed",
        "score": {"value": latest.score if latest else job.overall_score,
                  "origin": "ttea" if latest else "legacy"},
        "ttea_assessment": {
            "score": latest.score,
            "strengths": _json_field(latest.strengths_json, []),
            "gaps": _json_field(latest.gaps_json, []),
            "explanation": latest.explanation,
            "provider": latest.provider,
            "model": latest.model,
            "run_id": latest.run.external_run_id,
            "scored_at": _ttea_iso(latest.scored_at),
            "imported_at": _ttea_iso(latest.imported_at),
        } if latest else None,
        "scores": {
            "technical": job.technical_score,
            "eligibility": job.eligibility_score,
            "location": job.location_score,
            "experience": job.experience_score,
            "education": job.education_score,
            "domain": job.domain_score,
            "visa": job.visa_score,
            "salary": job.salary_score,
            "language": job.language_score,
            "overall": job.overall_score,
        },
        "score_explanations": explanations,
        "reason_groups": reason_groups,
        "requirements": requirements,
        "required_languages": required_languages,
        "extraction_confidence": confidence,
        "application_status": job.application_status or "not_started",
        "application_date": _iso(job.application_date),
        "cv_version": job.cv_version,
        "cover_letter_version": job.cover_letter_version,
        "response": job.response,
        "interview_stage": job.interview_stage,
        "notes": job.notes,
        "education_requirement": job.education_requirement,
        "visa_status": job.visa_status,
        "visa_sponsorship": job.visa_sponsorship,
        "international_candidates": job.international_candidates,
        "relocation_support": job.relocation_support,
        "funding": {
            "status": job.funding_status,
            "tuition_covered": job.tuition_covered,
            "housing_support": job.housing_support,
            "insurance_support": job.insurance_support,
            "travel_support": job.travel_support,
            "benefits": _json_field(job.benefits_json, {}),
        },
        "canonical_url": job.canonical_url,
        "first_seen_at": _iso(job.first_seen_at),
        "last_seen_at": _iso(job.last_seen_at),
        "updated_at": _iso(job.updated_at),
        "sources": [
            {
                "name": source.source_name,
                "url": source.source_url,
                "external_id": source.external_id,
                "first_seen_at": _iso(source.first_seen_at),
                "last_seen_at": _iso(source.last_seen_at),
            }
            for source in (job.sources or [])
        ],
    }
    canonical = canonical_batches.assessment_for_job(engine, job.id)
    payload["canonical_assessment"] = canonical
    if canonical and latest is None:
        # Keep existing generic assessments and manual job fields untouched.
        # The canonical snapshot remains independently visible in either case.
        payload["score"] = {"value": canonical["original_model_score"], "origin": "ttea"}
        payload["ttea_assessment"] = {
            "score": canonical["original_model_score"], "strengths": canonical["strengths"],
            "gaps": canonical["gaps"], "explanation": canonical["reasoning"],
            "provider": canonical["scoring_provider"], "model": canonical["model"],
            "run_id": _canonical_batch_id_for_job(job.id), "scored_at": canonical["scored_at"], "imported_at": None,
        }
    if include_description:
        payload["description"] = job.description or ""
    return payload



def _canonical_batch_id_for_job(job_id: int):
    if not canonical_batches.available(engine):
        return None

    with engine.connect() as connection:
        return connection.exec_driver_sql(
            """
            SELECT batch_id
            FROM ttea_canonical_records
            WHERE job_id = ?
            ORDER BY batch_id DESC
            LIMIT 1
            """,
            (job_id,),
        ).scalar_one_or_none()


# TTEA_NEW_AI_ACTIVE_POOL_V1
def _new_ai_keep_clause():
    """Authoritative admission gate for the active opportunity pool."""
    return literal_column(
        "EXISTS ("
        "SELECT 1 "
        "FROM ttea_job_decisions AS ttea_active_gate "
        "WHERE ttea_active_gate.canonical_job_id = jobs.id "
        "AND ttea_active_gate.gate_decision = 'KEEP'"
        ")"
    )


def _new_ai_score():
    """Authoritative score for jobs admitted by the new AI gate."""
    return literal_column(
        "("
        "SELECT ttea_active_score.score "
        "FROM ttea_job_decisions AS ttea_active_score "
        "WHERE ttea_active_score.canonical_job_id = jobs.id "
        "AND ttea_active_score.gate_decision = 'KEEP' "
        "LIMIT 1"
        ")"
    )


def _primary_score():
    new_ai_score = _new_ai_score()
    latest = (select(TteaAssessmentORM.score)
              .where(TteaAssessmentORM.job_id == JobORM.id)
              .order_by(TteaAssessmentORM.scored_at.desc(), TteaAssessmentORM.imported_at.desc(),
                        TteaAssessmentORM.id.desc())
              .limit(1).correlate(JobORM).scalar_subquery())
    if canonical_batches.available(engine):
        canonical_score = literal_column(
            "(SELECT json_extract(record_json, '$.original_model_score') "
            "FROM ttea_canonical_records WHERE job_id=jobs.id ORDER BY batch_id DESC LIMIT 1)"
        )
        return func.coalesce(new_ai_score, latest, canonical_score, JobORM.overall_score)
    return func.coalesce(new_ai_score, latest, JobORM.overall_score)


@app.get("/api/ttea/runs")
def ttea_runs(limit: int = Query(default=25, ge=1, le=100)):
    with Session(engine) as session:
        runs = session.scalars(select(TteaRunORM).order_by(
            TteaRunORM.imported_at.desc(), TteaRunORM.id.desc()).limit(limit)).all()
        return {"items": [{
            "id": run.id, "run_id": run.external_run_id, "status": run.status,
            "source": run.source_name, "provider": run.provider, "model": run.model,
            "started_at": _ttea_iso(run.started_at), "finished_at": _ttea_iso(run.finished_at),
            "imported_at": _ttea_iso(run.imported_at),
            "counts": _json_field(run.counts_json, {}), "usage": _json_field(run.usage_json, {}),
        } for run in runs]}


def _status_counts(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(JobORM.application_status, func.count(JobORM.id)).group_by(JobORM.application_status)
    ).all()
    return {status or "not_started": int(count) for status, count in rows}


def _application_sqlite_connection(*, readonly: bool):
    database = engine.url.database

    if not database:
        raise HTTPException(
            status_code=503,
            detail="Application Assistant requires a SQLite database",
        )

    path = Path(database)

    if readonly:
        connection = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro",
            uri=True,
        )
    else:
        connection = sqlite3.connect(
            path
        )

    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

    return connection


def _require_application_job(job_id: int) -> None:
    with Session(engine) as session:
        exists = session.scalar(
            select(JobORM.id)
            .where(JobORM.id == job_id)
        )

    if exists is None:
        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )


def _application_document_links(
    connection,
    application_id: int | None,
) -> list[dict]:

    if application_id is None:
        return []

    rows = connection.execute(
        """
        SELECT
            ad.id,
            ad.role,
            ad.selection_mode,
            ad.active,
            ad.attached_at,
            dl.id,
            dl.document_key,
            dl.kind,
            dl.language,
            dl.niche,
            dl.format,
            dl.file_path,
            dl.sha256,
            dl.is_generated
        FROM application_documents AS ad
        JOIN document_library AS dl
          ON dl.id = ad.document_id
        WHERE ad.application_id = ?
        ORDER BY ad.active DESC, ad.id
        """,
        (application_id,),
    ).fetchall()

    return [
        {
            "link_id": row[0],
            "role": row[1],
            "selection_mode": row[2],
            "active": bool(row[3]),
            "attached_at": row[4],
            "document": {
                "id": row[5],
                "document_key": row[6],
                "kind": row[7],
                "language": row[8],
                "niche": row[9],
                "format": row[10],
                "file_path": row[11],
                "sha256": row[12],
                "is_generated": bool(row[13]),
            },
        }
        for row in rows
    ]


def _application_bundle(job_id: int) -> dict:
    _require_application_job(
        job_id
    )

    connection = _application_sqlite_connection(
        readonly=True
    )

    try:
        application = aa_get_application(
            connection,
            job_id,
        )

        events = aa_application_events(
            connection,
            job_id,
        )

        documents = _application_document_links(
            connection,
            (
                application["id"]
                if application
                else None
            ),
        )

    finally:
        connection.close()

    return {
        "application": application,
        "events": events,
        "documents": documents,
    }


def _active_status_counts(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(
            JobORM.application_status,
            func.count(JobORM.id),
        )
        .where(_new_ai_keep_clause())
        .group_by(JobORM.application_status)
    ).all()
    return {
        status or "not_started": int(count)
        for status, count in rows
    }


def _active_review_counts(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(
            JobORM.review_status,
            func.count(JobORM.id),
        )
        .where(_new_ai_keep_clause())
        .group_by(JobORM.review_status)
    ).all()
    return {
        status or "unreviewed": int(count)
        for status, count in rows
    }


def _review_counts(session: Session) -> dict[str, int]:
    rows = session.execute(select(JobORM.review_status, func.count(JobORM.id)).group_by(JobORM.review_status)).all()
    return {status or "unreviewed": int(count) for status, count in rows}


def _source_poll_info() -> list[dict]:
    cfg = _load_yaml(SOURCES_PATH).get("sources", {})
    state = _load_json(POLL_STATE_PATH, {})
    history = _load_json(RUN_HISTORY_PATH, [])
    now = datetime.now(timezone.utc)

    with Session(engine) as session:
        counts = dict(session.execute(
            select(JobSourceORM.source_name, func.count(JobSourceORM.id)).group_by(JobSourceORM.source_name)
        ).all())
        live_counts = dict(session.execute(
            select(
                JobSourceORM.source_name,
                func.count(func.distinct(JobORM.id)),
            )
            .join(JobORM, JobORM.id == JobSourceORM.job_id)
            .where(
                JobORM.stale.is_(False),
                _new_ai_keep_clause(),
            )
            .group_by(JobSourceORM.source_name)
        ).all())

    result = []
    for name, source_cfg in cfg.items():
        last_raw = state.get(name)
        last_dt = None
        if last_raw:
            try:
                last_dt = datetime.fromisoformat(last_raw)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                last_dt = None
        min_minutes = int(source_cfg.get("min_interval_minutes", 0))
        next_dt = last_dt + timedelta(minutes=min_minutes) if last_dt else None
        eligible = next_dt is None or next_dt <= now
        seconds_remaining = max(0, int((next_dt - now).total_seconds())) if next_dt and not eligible else 0

        last_success = None
        last_error = None
        for run in history:
            source_run = (run.get("sources") or {}).get(name, {})
            status = source_run.get("status")
            if status == "success" and last_success is None:
                last_success = run.get("finished_at") or run.get("started_at")
            if status == "failed" and last_error is None:
                last_error = source_run.get("error") or "Collector failed"

        result.append({
            "name": name,
            "enabled": bool(source_cfg.get("enabled", True)),
            "kind": source_cfg.get("kind", ""),
            "note": source_cfg.get("note", ""),
            "jobs_seen": int(counts.get(name, 0)),
            "live_jobs": int(live_counts.get(name, 0)),
            "last_polled_at": _iso(last_dt),
            "last_success_at": last_success or _iso(last_dt),
            "last_error": last_error,
            "next_eligible_at": _iso(next_dt),
            "eligible_now": eligible,
            "seconds_remaining": seconds_remaining,
            "min_interval_minutes": min_minutes,
            "max_jobs": int(source_cfg.get("max_jobs", 0)),
        })
    return result


def _source_registry_info() -> list[dict]:
    registry = _load_yaml(SOURCE_REGISTRY_PATH).get("sources", {})
    active = {item["name"]: item for item in _source_poll_info()}
    result = []
    for name, meta in registry.items():
        row = {
            "name": name,
            "market": meta.get("market", ""),
            "method": meta.get("method", ""),
            "state": meta.get("state", "planned"),
            "priority": meta.get("priority", "normal"),
            "note": meta.get("note", ""),
            "enabled": False,
            "jobs_seen": 0,
            "live_jobs": 0,
            "last_polled_at": None,
            "last_success_at": None,
            "last_error": None,
            "next_eligible_at": None,
            "eligible_now": False,
            "min_interval_minutes": None,
        }
        if name in active:
            row.update(active[name])
            row["market"] = meta.get("market", row.get("market", ""))
            row["method"] = meta.get("method", row.get("kind", ""))
            row["state"] = "active" if row.get("enabled") else "disabled"
            row["priority"] = meta.get("priority", "high")
            row["note"] = meta.get("note") or row.get("note", "")
        result.append(row)
    # Any configured collector missing from the registry must still appear.
    for name, row in active.items():
        if name not in registry:
            result.append({**row, "market": "", "method": row.get("kind", ""), "state": "active", "priority": "high"})
    order = {"active": 0, "in_development": 1, "planned": 2, "auth_required": 3, "browser_required": 4, "blocked": 5, "disabled": 6}
    return sorted(result, key=lambda x: (order.get(x.get("state"), 9), x.get("market", ""), x.get("name", "")))


def _stored_record(job: JobORM) -> JobRecord:
    source = job.sources[0] if job.sources else None
    return JobRecord(
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
        benefits=_json_field(job.benefits_json, {}),
        source_name=source.source_name if source else "stored",
        source_url=source.source_url if source else (job.canonical_url or f"stored:{job.id}"),
    )


def _rescore_all(profile_data: dict, settings_data: dict) -> int:
    changed = 0
    with Session(engine) as session:
        jobs = session.scalars(select(JobORM).options(selectinload(JobORM.sources))).all()
        for job in jobs:
            record = _stored_record(job)
            scores = score_job(record, profile_data, settings_data)
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
        session.commit()
    return changed


@app.get("/")
def index():
    return FileResponse(UI_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(UI_DIR / "ttea-icon.svg")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "project": settings.get("project", {}).get("name", "TTEA"),
        "version": settings.get("project", {}).get("version", "0.4.0"),
        "database": str(db_path),
    }


@app.get("/api/overview")
def overview():
    history = _load_json(RUN_HISTORY_PATH, [])
    last_run = history[0] if history else None
    new_last_run = int((last_run or {}).get("summary", {}).get("new_jobs", 0) or 0)

    with Session(engine) as session:
        active_keep = _new_ai_keep_clause()
        total = session.scalar(
            select(func.count(JobORM.id))
            .where(active_keep)
        ) or 0
        job_total = session.scalar(
            select(func.count(JobORM.id))
            .where(
                active_keep,
                JobORM.opportunity_type == "job",
            )
        ) or 0
        phd_total = session.scalar(
            select(func.count(JobORM.id))
            .where(
                active_keep,
                JobORM.opportunity_type == "phd",
            )
        ) or 0
        live = session.scalar(
            select(func.count(JobORM.id))
            .where(
                active_keep,
                JobORM.stale.is_(False),
            )
        ) or 0
        stale = session.scalar(
            select(func.count(JobORM.id))
            .where(
                active_keep,
                JobORM.stale.is_(True),
            )
        ) or 0
        strong = session.scalar(
            select(func.count(JobORM.id))
            .where(
                active_keep,
                JobORM.stale.is_(False),
                _primary_score() >= 65,
            )
        ) or 0
        eligible_50 = session.scalar(
            select(func.count(JobORM.id))
            .where(
                active_keep,
                JobORM.stale.is_(False),
                _primary_score() >= 50,
                JobORM.eligibility_score >= 50,
            )
        ) or 0
        status_counts = _active_status_counts(session)
        review_counts = _active_review_counts(session)

    return {
        "metrics": {
            "total_jobs": int(total),
            "total_opportunities": int(total),
            "job_opportunities": int(job_total),
            "phd_opportunities": int(phd_total),
            "live_jobs": int(live),
            "stale_jobs": int(stale),
            "new_last_run": new_last_run,
            # Kept for backward API compatibility; V0.4 does not render these on Overview.
            "strong_matches": int(strong),
            "eligible_50_plus": int(eligible_50),
        },
        "status_counts": status_counts,
        "review_counts": review_counts,
        "sources": _source_poll_info(),
        "last_run": last_run,
    }


@app.get("/api/jobs")
def list_jobs(
    q: str = "",
    opportunity_type: Literal["all", "job", "phd"] = "all",
    min_score: int = Query(default=0, ge=0, le=100),
    status: str = "all",
    review: str = "all",
    verdict: str = "all",
    source: str = "all",
    remote: Literal["all", "yes", "no"] = "all",
    location_bucket: Literal["all", "local", "remote_accessible", "remote_restricted"] = "all",
    freshness: Literal["live", "stale", "all"] = "live",
    sort: Literal["score", "verdict", "newest", "first_seen", "updated"] = "score",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
):
    primary_score = _primary_score()
    filters = [
        _new_ai_keep_clause(),
        primary_score >= min_score,
    ]
    if opportunity_type != "all":
        filters.append(JobORM.opportunity_type == opportunity_type)
    if q.strip():
        needle = f"%{q.strip().lower()}%"
        filters.append(or_(
            func.lower(JobORM.title).like(needle),
            func.lower(JobORM.company).like(needle),
            func.lower(JobORM.location).like(needle),
            func.lower(JobORM.description).like(needle),
        ))
    if status != "all":
        filters.append(JobORM.application_status == status)
    if review != "all":
        filters.append(JobORM.review_status == review)
    if verdict == "reviewable":
        filters.append(JobORM.verdict != "blocked")
    elif verdict != "all":
        filters.append(JobORM.verdict == verdict)
    if source != "all":
        filters.append(JobORM.sources.any(JobSourceORM.source_name == source))
    if remote == "yes":
        filters.append(JobORM.remote.is_(True))
    elif remote == "no":
        filters.append(JobORM.remote.is_(False))
    if location_bucket == "local":
        filters.extend([JobORM.remote.is_(False), JobORM.location_score >= 60])
    elif location_bucket == "remote_accessible":
        filters.extend([JobORM.remote.is_(True), JobORM.location_score >= 60])
    elif location_bucket == "remote_restricted":
        filters.extend([JobORM.remote.is_(True), JobORM.location_score < 60])
    if freshness == "live":
        filters.append(JobORM.stale.is_(False))
    elif freshness == "stale":
        filters.append(JobORM.stale.is_(True))

    if sort == "newest":
        order = [JobORM.posted_at.desc(), primary_score.desc()]
    elif sort == "first_seen":
        order = [JobORM.first_seen_at.desc()]
    elif sort == "updated":
        order = [JobORM.updated_at.desc()]
    else:
        # Verdict is a gate, score is the within-gate ranking.
        verdict_order = case(
            (JobORM.verdict == "actionable", 0),
            (JobORM.verdict == "needs_verification", 1),
            else_=2,
        )
        order = [verdict_order.asc(), primary_score.desc(), JobORM.posted_at.desc()]

    with Session(engine) as session:
        total = session.scalar(select(func.count(JobORM.id)).where(*filters)) or 0
        jobs = session.scalars(
            select(JobORM)
            .options(selectinload(JobORM.sources))
            .where(*filters)
            .order_by(*order)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    return {
        "items": [_job_payload(j) for j in jobs],
        "page": page,
        "page_size": page_size,
        "total": int(total),
        "pages": max(1, (int(total) + page_size - 1) // page_size),
    }


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    with Session(engine) as session:
        job = session.scalar(
            select(JobORM)
            .options(selectinload(JobORM.sources))
            .where(
                JobORM.id == job_id,
                _new_ai_keep_clause(),
            )
        )
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return _job_payload(job, include_description=True)


@app.patch("/api/jobs/{job_id}")
@_guard_production_write("job.update")
def update_job(
    job_id: int, payload: JobUpdate,
    session_token: str | None = Header(default=None, alias="X-TTEA-Session-Token"),
):
    with Session(engine) as session:
        if session_token:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        job = session.scalar(
            select(JobORM)
            .options(selectinload(JobORM.sources))
            .where(
                JobORM.id == job_id,
                _new_ai_keep_clause(),
            )
        )
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        previous_review = job.review_status
        if payload.review_status is not None:
            if payload.review_status not in REVIEW_STATUSES:
                raise HTTPException(status_code=400, detail="Invalid review status")
            job.review_status = payload.review_status
            job.updated_at = datetime.now(timezone.utc)

        if payload.application_status is not None:
            if payload.application_status not in APPLICATION_STATUSES:
                raise HTTPException(status_code=400, detail="Invalid application status")
            previous = job.application_status
            job.application_status = payload.application_status
            if payload.application_status == "applied" and job.application_date is None:
                job.application_date = datetime.now(timezone.utc)
            if previous != payload.application_status:
                job.updated_at = datetime.now(timezone.utc)

        if payload.notes is not None:
            job.notes = payload.notes.strip() or None
            job.updated_at = datetime.now(timezone.utc)
        if payload.cv_version is not None:
            job.cv_version = payload.cv_version.strip() or None
            job.updated_at = datetime.now(timezone.utc)
        if payload.cover_letter_version is not None:
            job.cover_letter_version = payload.cover_letter_version.strip() or None
            job.updated_at = datetime.now(timezone.utc)
        if payload.response is not None:
            job.response = payload.response.strip() or None
            job.updated_at = datetime.now(timezone.utc)
        if payload.interview_stage is not None:
            job.interview_stage = payload.interview_stage.strip() or None
            job.updated_at = datetime.now(timezone.utc)

        if session_token and payload.review_status != previous_review and payload.review_status in analytics.REVIEW_ACTIONS:
            session.flush()
            raw_connection = session.connection().connection.driver_connection
            analytics.record_activity(raw_connection, session_token,
                analytics.REVIEW_ACTIONS[payload.review_status], job_id)
        session.commit()
        session.refresh(job)
        return _job_payload(job, include_description=True)


def _analytics_operation(operation, *, write=False, reason="session.mutation"):
    if write:
        with _production_write_scope(reason):
            return _analytics_operation_impl(operation, write=True)
    return _analytics_operation_impl(operation)


def _analytics_operation_impl(operation, *, write=False):
    connection = _application_sqlite_connection(readonly=not write)
    try:
        # Reports use a single read snapshot; lifecycle writes serialize by client.
        connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        result = operation(connection)
        if write:
            connection.commit()
            analytics.log_event("Session lifecycle request committed")
        return result
    except ValueError as exc:
        if write:
            connection.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        if write:
            connection.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except sqlite3.Error as exc:
        if write:
            connection.rollback()
        analytics.log_event("Analytics storage unavailable")
        raise HTTPException(status_code=503, detail="Analytics storage unavailable. Migration 003 must be activated separately.") from exc
    finally:
        connection.close()


@app.post("/api/application-assistant/sessions/start")
def application_session_start(payload: ApplicationSessionStartRequest):
    return {"session": _analytics_operation(lambda connection: analytics.start_session(
        connection, payload.client_id, payload.session_token, payload.timezone, payload.extension_version,
    ), write=True, reason="session.start")}


@app.post("/api/application-assistant/sessions/{session_token}/heartbeat")
def application_session_heartbeat(session_token: str):
    return {"session": _analytics_operation(lambda connection: analytics.touch_session(connection, session_token), write=True, reason="session.heartbeat")}


@app.post("/api/application-assistant/sessions/{session_token}/end")
def application_session_end(session_token: str, payload: ApplicationSessionEndRequest):
    return {"session": _analytics_operation(lambda connection: analytics.touch_session(connection, session_token, end=True), write=True, reason="session.end")}


@app.get("/api/application-assistant/tracker")
def application_tracker(
    state: str = "all", search: str = "", company: str = "",
    limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
):
    if state != "all" and state not in APPLICATION_STATES:
        raise HTTPException(status_code=400, detail="Unknown application state")
    result = _analytics_operation(lambda connection: analytics.tracker(connection, state, search or company, limit, offset))
    with Session(engine) as session:
        for item in result["items"]:
            job = session.scalar(select(JobORM).options(selectinload(JobORM.sources)).where(JobORM.id == item["job_id"]))
            if job is not None:
                presentation = _job_payload(job)
                item["score"] = presentation["score"]
                item["verdict"] = presentation["verdict"]
    return result


@app.get("/api/application-assistant/analytics/sessions")
def application_session_analytics(
    limit: int = Query(default=30, ge=1, le=200), timezone: str = "UTC",
):
    return _analytics_operation(lambda connection: analytics.session_analytics(connection, limit, timezone))


@app.get("/api/application-assistant/analytics/days")
def application_daily_analytics(
    days: int = Query(default=14, ge=1, le=366), timezone: str = "UTC",
):
    return _analytics_operation(lambda connection: analytics.daily_analytics(connection, days, timezone))

@app.get("/api/application-assistant/states")
def application_assistant_states():
    return {
        "states": list(APPLICATION_STATES),
        "transitions": {
            state: sorted(
                ALLOWED_TRANSITIONS[state]
            )
            for state in APPLICATION_STATES
        },
    }


@app.get("/api/application-assistant/followups")
def application_assistant_followups():
    active_states = (
        "applied", "awaiting_response", "assessment", "interview",
        "technical_test", "hr_interview", "offer",
    )
    connection = _application_sqlite_connection(readonly=True)
    try:
        rows = connection.execute(
            """
            SELECT a.job_id, a.current_state, a.application_url,
                   a.submitted_at, a.last_activity_at
            FROM applications AS a
            JOIN jobs AS j ON j.id = a.job_id
            WHERE a.current_state IN (?, ?, ?, ?, ?, ?, ?)
            ORDER BY a.last_activity_at DESC, a.id DESC
            """,
            active_states,
        ).fetchall()
    finally:
        connection.close()

    # Reuse the existing job presentation, including its primary score origin.
    items = []
    with Session(engine) as session:
        for job_id, state, url, submitted_at, last_activity_at in rows:
            job = session.scalar(
                select(JobORM)
                .options(selectinload(JobORM.sources))
                .where(JobORM.id == job_id)
            )
            if job is None:
                continue
            item = _job_payload(job)
            item.update({
                "job_id": job_id,
                "current_state": state,
                "application_status": state,
                "application_url": url,
                "submitted_at": submitted_at,
                "last_activity_at": last_activity_at,
            })
            items.append(item)
    return {"items": items, "total": len(items), "active_states": list(active_states)}

@app.get("/api/application-assistant/documents")
def application_assistant_documents(
    kind: str = "all",
    language: str = "all",
    niche: str = "all",
    format: str = "all",
):
    normalized_kind = kind.strip().lower()
    normalized_language = language.strip().upper()
    normalized_niche = niche.strip().lower()
    normalized_format = format.strip().lower()

    if normalized_kind not in {
        "all",
        "cv",
        "motivation_letter",
    }:
        raise HTTPException(
            status_code=400,
            detail="Invalid document kind",
        )

    if normalized_language not in {
        "ALL",
        "EN",
        "FR",
    }:
        raise HTTPException(
            status_code=400,
            detail="Invalid document language",
        )

    if normalized_format not in {
        "all",
        "pdf",
        "docx",
    }:
        raise HTTPException(
            status_code=400,
            detail="Invalid document format",
        )

    connection = _application_sqlite_connection(
        readonly=True
    )

    try:
        rows = aa_document_library(
            connection
        )
    finally:
        connection.close()

    if normalized_kind != "all":
        rows = [
            row
            for row in rows
            if row["kind"] == normalized_kind
        ]

    if normalized_language != "ALL":
        rows = [
            row
            for row in rows
            if row["language"] == normalized_language
        ]

    if normalized_niche != "all":
        rows = [
            row
            for row in rows
            if row["niche"] == normalized_niche
        ]

    if normalized_format != "all":
        rows = [
            row
            for row in rows
            if row["format"] == normalized_format
        ]

    return {
        "items": rows,
        "total": len(rows),
    }


@app.get("/api/jobs/{job_id}/application")
def get_job_application(job_id: int):
    return _application_bundle(
        job_id
    )


@app.post("/api/jobs/{job_id}/application")
@_guard_production_write("application.ensure")
def ensure_job_application(
    job_id: int,
    payload: ApplicationEnsureRequest | None = None,
):
    _require_application_job(
        job_id
    )

    request = (
        payload
        or ApplicationEnsureRequest()
    )

    connection = _application_sqlite_connection(
        readonly=False
    )

    try:
        aa_ensure_application(
            connection,
            job_id,
            actor="user",
            source=request.source,
        )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

    return _application_bundle(
        job_id
    )


@app.patch("/api/jobs/{job_id}/application/link")
@_guard_production_write("application.link")
def save_job_application_link(
    job_id: int, payload: ApplicationLinkRequest,
    session_token: str | None = Header(default=None, alias="X-TTEA-Session-Token"),
):
    _require_application_job(job_id)
    connection = _application_sqlite_connection(readonly=False)
    try:
        # Serialize read/replace/event provenance, including concurrent saves.
        connection.execute("BEGIN IMMEDIATE")
        before = aa_get_application(connection, job_id)
        application = aa_set_application_url(
            connection, job_id, payload.url, actor="user", source=payload.source,
        )
        previous_url = before["application_url"] if before else None
        if previous_url != application["application_url"]:
            analytics.record_activity(connection, session_token,
                "application.link_replaced" if previous_url else "application.link_saved",
                job_id, application["id"])
        connection.commit()
    except ValueError as exc:
        connection.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {"application": application}

@app.post("/api/jobs/{job_id}/application/transition")
@_guard_production_write("application.transition")
def transition_job_application(
    job_id: int,
    payload: ApplicationTransitionRequest,
    session_token: str | None = Header(default=None, alias="X-TTEA-Session-Token"),
):
    _require_application_job(
        job_id
    )

    connection = _application_sqlite_connection(
        readonly=False
    )

    try:
        try:
            connection.execute("BEGIN IMMEDIATE")
            before = aa_get_application(connection, job_id)
            old_state = before["current_state"] if before else connection.execute(
                "SELECT application_status FROM jobs WHERE id=?", (job_id,)
            ).fetchone()[0]
            if old_state not in APPLICATION_STATES:
                old_state = "not_started"
            application = aa_transition_application(
                connection,
                job_id,
                payload.state,
                actor="user",
                source=payload.source,
                note=payload.note,
                metadata=payload.metadata,
            )

        except ValueError as exc:
            connection.rollback()

            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

        if old_state != application["current_state"]:
            if payload.metadata.get("mode") == "followup" or old_state in {
                "applied", "awaiting_response", "assessment", "interview",
                "technical_test", "hr_interview", "offer",
            }:
                action = "followup.transition"
                activity_metadata = {"from_state": old_state, "to_state": application["current_state"]}
            else:
                historical = payload.metadata.get("historical_application") is True
                action = {
                    "applied": "review.already_applied" if historical else "review.applied",
                    "expired": "review.expired", "not_interested": "review.not_interested",
                }.get(application["current_state"])
                activity_metadata = ({"historical_application": True, "application_date_unknown": True}
                                     if historical else {})
            if action:
                analytics.record_activity(connection, session_token, action,
                    job_id, application["id"], activity_metadata)
        connection.commit()

    except HTTPException:
        raise

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

    return _application_bundle(
        job_id
    )


@app.get("/api/applications")
def applications():
    with Session(engine) as session:
        jobs = session.scalars(
            select(JobORM)
            .options(selectinload(JobORM.sources))
            .where(or_(JobORM.review_status == "shortlisted", JobORM.application_status != "not_started"))
            .order_by(JobORM.updated_at.desc())
        ).all()
        counts = _status_counts(session)
    return {
        "items": [_job_payload(j) for j in jobs],
        "status_counts": counts,
        "statuses": APPLICATION_STATUSES,
    }


@app.get("/api/sources")
def sources():
    return {"items": _source_registry_info()}


@app.get("/api/profile")
def profile():
    return {
        "profile": _load_json(PROFILE_PATH, {}),
        "settings": _load_yaml(SETTINGS_PATH),
        "review_statuses": REVIEW_STATUSES,
        "application_statuses": APPLICATION_STATUSES,
        "verdicts": VERDICTS,
    }


@app.patch("/api/profile")
@_guard_production_write("profile.update")
def update_profile(payload: ProfileUpdate):
    global settings
    profile_data = payload.profile
    if not isinstance(profile_data.get("languages", {}), dict):
        raise HTTPException(status_code=400, detail="profile.languages must be an object")

    new_settings = _load_yaml(SETTINGS_PATH)
    matching = new_settings.setdefault("matching", {})
    collection = new_settings.setdefault("collection", {})
    for key, value in payload.matching.items():
        matching[key] = value
    for key, value in payload.collection.items():
        collection[key] = value

    weights = matching.get("tarek_fit_weights", {}) or matching.get("overall_weights", {})
    if weights:
        keys = ["technical", "experience", "education", "domain", "visa", "salary", "location", "language"]
        total = sum(float(weights.get(k, 0)) for k in keys)
        if abs(total - 1.0) > 0.02:
            raise HTTPException(status_code=400, detail="TarekFit weights must add up to 1.0")

    _save_json(PROFILE_PATH, profile_data)
    _save_yaml(SETTINGS_PATH, new_settings)
    settings = new_settings
    changed = _rescore_all(profile_data, new_settings)
    return {"ok": True, "rescored_jobs": changed, "profile": profile_data, "settings": new_settings}


def _read_run_state() -> dict:
    with RUN_LOCK:
        return _load_json(RUN_STATE_PATH, {})


def _write_run_state(state: dict) -> None:
    with RUN_LOCK:
        _save_json(RUN_STATE_PATH, state)


def _append_history(state: dict) -> None:
    with RUN_LOCK:
        history = _load_json(RUN_HISTORY_PATH, [])
        history.insert(0, {
            "id": state.get("id"),
            "status": state.get("status"),
            "started_at": state.get("started_at"),
            "finished_at": state.get("finished_at"),
            "limit": state.get("limit"),
            "summary": state.get("summary", {}),
            "sources": state.get("sources", {}),
        })
        _save_json(RUN_HISTORY_PATH, history[:100])


def _parse_run_line(state: dict, line: str) -> None:
    state["last_line"] = line
    logs = state.setdefault("logs", [])
    logs.append(line)
    if len(logs) > 300:
        del logs[:-300]

    start_match = re.search(r"\[([A-Z0-9_-]+)\]\s+collecting up to\s+(\d+)", line, re.I)
    if start_match:
        name = start_match.group(1).lower()
        source = state.setdefault("sources", {}).setdefault(name, {})
        source.update({"status": "running", "requested": int(start_match.group(2))})
        state["phase"] = f"Collecting {name}"

    fetched_match = re.search(r"\[([A-Z0-9_-]+)\]\s+(\d+)\s+jobs fetched", line, re.I)
    if fetched_match:
        name = fetched_match.group(1).lower()
        source = state.setdefault("sources", {}).setdefault(name, {})
        source.update({"status": "success", "fetched": int(fetched_match.group(2))})
        state["phase"] = f"Completed {name}"

    failed_match = re.search(r"\[([A-Z0-9_-]+)\]\s+failed:\s*(.*)", line, re.I)
    if failed_match:
        name = failed_match.group(1).lower()
        source = state.setdefault("sources", {}).setdefault(name, {})
        source.update({"status": "failed", "error": failed_match.group(2).strip()})

    skipped_match = re.search(r"\[([A-Z0-9_-]+)\]\s+Skipped:", line, re.I)
    if skipped_match:
        name = skipped_match.group(1).lower()
        source = state.setdefault("sources", {}).setdefault(name, {})
        source.update({"status": "skipped"})

    summary_patterns = {
        "raw_jobs": r"Raw jobs fetched:\s+(\d+)",
        "new_jobs": r"New canonical jobs:\s+(\d+)",
        "duplicates": r"Duplicates merged:\s+(\d+)",
        "seen_again": r"Already seen URLs:\s+(\d+)",
        "stale": r"Marked stale:\s+(\d+)",
        "collector_failures": r"Collector failures:\s+(\d+)",
        "live_exported": r"Live jobs exported:\s+(\d+)",
    }
    summary = state.setdefault("summary", {})
    for key, pattern in summary_patterns.items():
        m = re.search(pattern, line, re.I)
        if m:
            summary[key] = int(m.group(1))
    if "Dry-run summary" in line:
        state["phase"] = "Saving and summarizing"


def _run_collection_worker(run_id: str, limit: int, ignore_poll_interval: bool, selected_sources: list[str]) -> None:
    global RUN_PROCESS
    state = _read_run_state()
    _write_run_state(state)
    cmd = [sys.executable, "-m", "src.main", "dry-run", "--limit", str(limit)]
    if ignore_poll_interval:
        cmd.append("--ignore-poll-interval")
    if selected_sources:
        cmd.extend(["--sources", ",".join(selected_sources)])

    try:
        # The child reads the project configuration rather than an injected engine.
        collector_target = ROOT / _load_yaml(SETTINGS_PATH).get("storage", {}).get("sqlite_path", "data/jobs.db")
        with _production_write_scope("collection.run", target_db=collector_target):
            try:
                RUN_PROCESS = subprocess.Popen(
                    cmd,
                    cwd=str(ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
                if RUN_PROCESS.stdout is not None:
                    for raw_line in RUN_PROCESS.stdout:
                        line = raw_line.rstrip("\r\n")
                        if not line:
                            continue
                        state = _read_run_state()
                        _parse_run_line(state, line)
                        _write_run_state(state)
                return_code = RUN_PROCESS.wait()
                state = _read_run_state()
                state["return_code"] = return_code
                if state.get("stop_requested"):
                    state["status"] = "stopped"
                    state["phase"] = "Stopped by user"
                else:
                    state["status"] = "success" if return_code == 0 else "failed"
                    state["phase"] = "Completed" if return_code == 0 else "Failed"
                state["finished_at"] = datetime.now(timezone.utc).isoformat()
                _write_run_state(state)
                _append_history(state)
            finally:
                # A failed output/state update must not release the guard while
                # its child can still write the production database.
                if RUN_PROCESS is not None and RUN_PROCESS.poll() is None:
                    RUN_PROCESS.terminate()
                    try:
                        RUN_PROCESS.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        RUN_PROCESS.kill()
                        RUN_PROCESS.wait()
    except Exception as exc:
        state = _read_run_state()
        state["status"] = "failed"
        state["phase"] = "Failed"
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        state["error"] = f"{type(exc).__name__}: {exc}"
        state.setdefault("logs", []).append(state["error"])
        _write_run_state(state)
        _append_history(state)
    finally:
        RUN_PROCESS = None


@app.get("/api/run/status")
def run_status():
    return _read_run_state()


@app.get("/api/run/history")
def run_history():
    return {"items": _load_json(RUN_HISTORY_PATH, [])}


@app.post("/api/run")
def start_run(payload: RunRequest):
    global RUN_THREAD
    current = _read_run_state()
    if current.get("status") == "running":
        raise HTTPException(status_code=409, detail="A collection run is already in progress")

    active = _source_poll_info()
    selected_set = {x.lower() for x in payload.sources}
    enabled = [s for s in active if s["enabled"] and (not selected_set or s["name"].lower() in selected_set)]
    if not enabled:
        raise HTTPException(status_code=400, detail="No enabled collectors match the requested source scope")
    if not payload.ignore_poll_interval and not any(s["eligible_now"] for s in enabled):
        next_times = [s["next_eligible_at"] for s in enabled if s["next_eligible_at"]]
        raise HTTPException(status_code=429, detail={
            "message": "All selected sources are still inside their minimum polling interval.",
            "next_eligible_at": min(next_times) if next_times else None,
        })

    selected_names = [s["name"] for s in enabled]
    run_id = datetime.now(timezone.utc).strftime("ui_%Y%m%d_%H%M%S")
    state = {
        "id": run_id,
        "status": "running",
        "phase": "Starting",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "limit": payload.limit,
        "ignore_poll_interval": payload.ignore_poll_interval,
        "selected_sources": selected_names,
        "summary": {},
        "sources": {name: {"status": "queued", "fetched": 0} for name in selected_names},
        "logs": [],
        "last_line": "",
        "stop_requested": False,
    }
    _write_run_state(state)
    RUN_THREAD = threading.Thread(
        target=_run_collection_worker,
        args=(run_id, payload.limit, payload.ignore_poll_interval, selected_names),
        daemon=True,
        name=f"ttea-{run_id}",
    )
    RUN_THREAD.start()
    return state


@app.post("/api/run/stop")
def stop_run():
    global RUN_PROCESS
    state = _read_run_state()
    if state.get("status") != "running":
        raise HTTPException(status_code=409, detail="No collection run is active")
    state["stop_requested"] = True
    state["phase"] = "Stopping"
    state.setdefault("logs", []).append("Stop requested from dashboard")
    _write_run_state(state)
    if RUN_PROCESS is not None and RUN_PROCESS.poll() is None:
        RUN_PROCESS.terminate()
    return state


@app.get("/api/ttea/live")
def ttea_live_state():
    return JSONResponse(ttea_live.state(), headers={"Cache-Control": "no-store"})


@app.get("/api/ttea/live/events")
def ttea_live_events(run_id: str | None = None, limit: int = Query(default=100, ge=1, le=100)):
    return JSONResponse(ttea_live.events(run_id, limit), headers={"Cache-Control": "no-store"})


@app.get("/api/ttea/live/observer")
def ttea_live_observer(run_id: str | None = None, page: str = "detail"):
    content = ttea_live.observer(run_id, page)
    return HTMLResponse(content or "<p>WAITING FOR SOURCE OBSERVATION</p>", status_code=200 if content else 404,
                        headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox; base-uri 'none'; form-action 'none'; frame-ancestors 'self'",
                                 "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
                                 "Referrer-Policy": "no-referrer"})


@app.get("/api/ttea/batches/{batch_id}/jobs")
def canonical_batch_jobs(batch_id: str, action_bucket: str | None = None,
                         semantic_status: str | None = None,
                         scoring_provider: str | None = None,
                         assessment_version: str | None = None):
    items = canonical_batches.query_batch(engine, batch_id, action_bucket=action_bucket,
        semantic_status=semantic_status, scoring_provider=scoring_provider,
        assessment_version=assessment_version)
    return {"items": items, "total": len(items), "batch_id": batch_id}
