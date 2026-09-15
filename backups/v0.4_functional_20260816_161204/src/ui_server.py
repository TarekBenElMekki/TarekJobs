from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal

import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .database import JobORM, JobSourceORM, get_engine, init_db

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "ui"
SETTINGS_PATH = ROOT / "config" / "settings.yaml"
SOURCES_PATH = ROOT / "config" / "sources.yaml"
PROFILE_PATH = ROOT / "config" / "profile.json"
POLL_STATE_PATH = ROOT / "data" / "source_poll_state.json"
RUN_STATE_PATH = ROOT / "data" / "ui_run_state.json"
RUN_HISTORY_PATH = ROOT / "data" / "ui_run_history.json"

APPLICATION_STATUSES = [
    "discovered",
    "shortlisted",
    "rejected_by_you",
    "application_preparing",
    "ready_to_apply",
    "applied",
    "interview",
    "technical_test",
    "hr_interview",
    "offer",
    "rejected",
    "withdrawn",
    "expired",
]

RUN_LOCK = threading.Lock()
RUN_THREAD: threading.Thread | None = None


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


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


settings = _load_yaml(SETTINGS_PATH)
db_path = ROOT / settings.get("storage", {}).get("sqlite_path", "data/jobs.db")
engine = get_engine(str(db_path))
init_db(engine)

app = FastAPI(title="TarekJobs Dashboard API", version="0.2.0")
app.mount("/assets", StaticFiles(directory=UI_DIR), name="assets")


class JobUpdate(BaseModel):
    application_status: str | None = None
    notes: str | None = None
    cv_version: str | None = None


class RunRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=1000)
    ignore_poll_interval: bool = False


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _job_payload(job: JobORM, include_description: bool = False) -> dict:
    try:
        explanations = json.loads(job.score_explanation_json or "[]")
    except Exception:
        explanations = []
    payload = {
        "id": job.id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
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
        "stale": bool(job.stale),
        "scores": {
            "technical": job.technical_score,
            "eligibility": job.eligibility_score,
            "location": job.location_score,
            "experience": job.experience_score,
            "overall": job.overall_score,
        },
        "score_explanations": explanations,
        "application_status": job.application_status,
        "application_date": _iso(job.application_date),
        "cv_version": job.cv_version,
        "notes": job.notes,
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
    if include_description:
        payload["description"] = job.description or ""
    return payload


def _status_counts(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(JobORM.application_status, func.count(JobORM.id)).group_by(JobORM.application_status)
    ).all()
    return {status or "discovered": int(count) for status, count in rows}


def _source_poll_info() -> list[dict]:
    cfg = _load_yaml(SOURCES_PATH).get("sources", {})
    state = _load_json(POLL_STATE_PATH, {})
    now = datetime.now(timezone.utc)

    with Session(engine) as session:
        counts = dict(
            session.execute(
                select(JobSourceORM.source_name, func.count(JobSourceORM.id)).group_by(JobSourceORM.source_name)
            ).all()
        )
        live_counts = dict(
            session.execute(
                select(JobSourceORM.source_name, func.count(JobSourceORM.id))
                .join(JobORM, JobORM.id == JobSourceORM.job_id)
                .where(JobORM.stale.is_(False))
                .group_by(JobSourceORM.source_name)
            ).all()
        )

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
        result.append({
            "name": name,
            "enabled": bool(source_cfg.get("enabled", True)),
            "kind": source_cfg.get("kind", ""),
            "note": source_cfg.get("note", ""),
            "jobs_seen": int(counts.get(name, 0)),
            "live_jobs": int(live_counts.get(name, 0)),
            "last_polled_at": _iso(last_dt),
            "next_eligible_at": _iso(next_dt),
            "eligible_now": eligible,
            "seconds_remaining": seconds_remaining,
            "min_interval_minutes": min_minutes,
            "max_jobs": int(source_cfg.get("max_jobs", 0)),
        })
    return result


@app.get("/")
def index():
    return FileResponse(UI_DIR / "index.html")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "project": settings.get("project", {}).get("name", "TarekJobs"),
        "version": settings.get("project", {}).get("version", "0.2.0"),
        "database": str(db_path),
    }


@app.get("/api/overview")
def overview():
    threshold = int(settings.get("matching", {}).get("shortlist_threshold", 65))
    with Session(engine) as session:
        total = session.scalar(select(func.count(JobORM.id))) or 0
        live = session.scalar(select(func.count(JobORM.id)).where(JobORM.stale.is_(False))) or 0
        stale = session.scalar(select(func.count(JobORM.id)).where(JobORM.stale.is_(True))) or 0
        strong = session.scalar(
            select(func.count(JobORM.id)).where(JobORM.stale.is_(False), JobORM.overall_score >= threshold)
        ) or 0
        eligible_50 = session.scalar(
            select(func.count(JobORM.id)).where(
                JobORM.stale.is_(False),
                JobORM.overall_score >= 50,
                JobORM.eligibility_score >= 50,
            )
        ) or 0
        status_counts = _status_counts(session)
        top = session.scalars(
            select(JobORM)
            .options(selectinload(JobORM.sources))
            .where(JobORM.stale.is_(False))
            .order_by(JobORM.overall_score.desc(), JobORM.posted_at.desc())
            .limit(8)
        ).all()
        recent = session.scalars(
            select(JobORM)
            .options(selectinload(JobORM.sources))
            .order_by(JobORM.first_seen_at.desc())
            .limit(6)
        ).all()

    applied_family = sum(status_counts.get(s, 0) for s in [
        "applied", "interview", "technical_test", "hr_interview", "offer"
    ])
    active_pipeline = sum(status_counts.get(s, 0) for s in [
        "shortlisted", "application_preparing", "ready_to_apply", "applied",
        "interview", "technical_test", "hr_interview", "offer"
    ])
    return {
        "metrics": {
            "total_jobs": int(total),
            "live_jobs": int(live),
            "stale_jobs": int(stale),
            "strong_matches": int(strong),
            "eligible_50_plus": int(eligible_50),
            "active_pipeline": int(active_pipeline),
            "applied_or_beyond": int(applied_family),
        },
        "shortlist_threshold": threshold,
        "status_counts": status_counts,
        "top_jobs": [_job_payload(j) for j in top],
        "recent_jobs": [_job_payload(j) for j in recent],
        "sources": _source_poll_info(),
        "run": _load_json(RUN_STATE_PATH, {}),
    }


@app.get("/api/jobs")
def list_jobs(
    q: str = "",
    min_score: int = Query(default=0, ge=0, le=100),
    status: str = "all",
    source: str = "all",
    remote: Literal["all", "yes", "no"] = "all",
    freshness: Literal["live", "stale", "all"] = "live",
    sort: Literal["score", "newest", "first_seen", "updated"] = "score",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
):
    filters = [JobORM.overall_score >= min_score]
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
    if source != "all":
        filters.append(JobORM.sources.any(JobSourceORM.source_name == source))
    if remote == "yes":
        filters.append(JobORM.remote.is_(True))
    elif remote == "no":
        filters.append(JobORM.remote.is_(False))
    if freshness == "live":
        filters.append(JobORM.stale.is_(False))
    elif freshness == "stale":
        filters.append(JobORM.stale.is_(True))

    if sort == "newest":
        order = [JobORM.posted_at.desc(), JobORM.overall_score.desc()]
    elif sort == "first_seen":
        order = [JobORM.first_seen_at.desc()]
    elif sort == "updated":
        order = [JobORM.updated_at.desc()]
    else:
        order = [JobORM.overall_score.desc(), JobORM.posted_at.desc()]

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
            select(JobORM).options(selectinload(JobORM.sources)).where(JobORM.id == job_id)
        )
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return _job_payload(job, include_description=True)


@app.patch("/api/jobs/{job_id}")
def update_job(job_id: int, payload: JobUpdate):
    with Session(engine) as session:
        job = session.scalar(
            select(JobORM).options(selectinload(JobORM.sources)).where(JobORM.id == job_id)
        )
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

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

        session.commit()
        session.refresh(job)
        return _job_payload(job, include_description=True)


@app.get("/api/applications")
def applications():
    with Session(engine) as session:
        jobs = session.scalars(
            select(JobORM)
            .options(selectinload(JobORM.sources))
            .where(JobORM.application_status != "discovered")
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
    return {"items": _source_poll_info()}


@app.get("/api/profile")
def profile():
    profile_data = _load_json(PROFILE_PATH, {})
    return {
        "profile": profile_data,
        "settings": settings,
        "application_statuses": APPLICATION_STATUSES,
    }


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
        _save_json(RUN_HISTORY_PATH, history[:50])


def _parse_run_line(state: dict, line: str) -> None:
    state["last_line"] = line
    logs = state.setdefault("logs", [])
    logs.append(line)
    if len(logs) > 250:
        del logs[:-250]

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


def _run_collection_worker(run_id: str, limit: int, ignore_poll_interval: bool) -> None:
    state = _read_run_state()
    source_cfg = _load_yaml(SOURCES_PATH).get("sources", {})
    for name, cfg in source_cfg.items():
        if cfg.get("enabled", True):
            state.setdefault("sources", {}).setdefault(name, {"status": "queued", "fetched": 0})
    _write_run_state(state)

    cmd = [sys.executable, "-m", "src.main", "dry-run", "--limit", str(limit)]
    if ignore_poll_interval:
        cmd.append("--ignore-poll-interval")

    try:
        process = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        if process.stdout is not None:
            for raw_line in process.stdout:
                line = raw_line.rstrip("\r\n")
                if not line:
                    continue
                state = _read_run_state()
                _parse_run_line(state, line)
                _write_run_state(state)
        return_code = process.wait()
        state = _read_run_state()
        state["return_code"] = return_code
        state["status"] = "success" if return_code == 0 else "failed"
        state["phase"] = "Completed" if return_code == 0 else "Failed"
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_run_state(state)
        _append_history(state)
    except Exception as exc:
        state = _read_run_state()
        state["status"] = "failed"
        state["phase"] = "Failed"
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        state["error"] = f"{type(exc).__name__}: {exc}"
        state.setdefault("logs", []).append(state["error"])
        _write_run_state(state)
        _append_history(state)


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

    if not payload.ignore_poll_interval:
        source_info = _source_poll_info()
        enabled = [s for s in source_info if s["enabled"]]
        if enabled and not any(s["eligible_now"] for s in enabled):
            next_times = [s["next_eligible_at"] for s in enabled if s["next_eligible_at"]]
            raise HTTPException(
                status_code=429,
                detail={
                    "message": "All enabled sources are still inside their minimum polling interval.",
                    "next_eligible_at": min(next_times) if next_times else None,
                },
            )

    run_id = datetime.now(timezone.utc).strftime("ui_%Y%m%d_%H%M%S")
    source_cfg = _load_yaml(SOURCES_PATH).get("sources", {})
    state = {
        "id": run_id,
        "status": "running",
        "phase": "Starting",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "limit": payload.limit,
        "ignore_poll_interval": payload.ignore_poll_interval,
        "summary": {},
        "sources": {
            name: {"status": "queued", "fetched": 0}
            for name, cfg in source_cfg.items()
            if cfg.get("enabled", True)
        },
        "logs": [],
        "last_line": "",
    }
    _write_run_state(state)

    RUN_THREAD = threading.Thread(
        target=_run_collection_worker,
        args=(run_id, payload.limit, payload.ignore_poll_interval),
        daemon=True,
        name=f"tarekjobs-{run_id}",
    )
    RUN_THREAD.start()
    return state

# TAREKJOBS_OFFICIAL_FAVICON_ROUTE
from pathlib import Path as _TarekJobsBrandPath
from fastapi.responses import FileResponse as _TarekJobsBrandFileResponse

@app.get("/favicon.ico", include_in_schema=False)
def tarekjobs_official_favicon():
    _icon = _TarekJobsBrandPath(__file__).resolve().parents[1] / "ui" / "favicon.ico"
    return _TarekJobsBrandFileResponse(_icon)
