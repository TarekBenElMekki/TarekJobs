from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from rapidfuzz import fuzz
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from .models import JobRecord, ScoreResult
from .normalize import norm_text


class Base(DeclarativeBase):
    pass


class JobORM(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500))
    company: Mapped[str] = mapped_column(String(300), default="Unknown")
    location: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    employment_type: Mapped[str] = mapped_column(String(80), default="")
    remote: Mapped[bool] = mapped_column(Boolean, default=False)
    remote_region: Mapped[str] = mapped_column(String(200), default="")
    seniority: Mapped[str] = mapped_column(String(80), default="")
    min_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    salary_period: Mapped[str | None] = mapped_column(String(30), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)

    technical_score: Mapped[int] = mapped_column(Integer, default=0)
    eligibility_score: Mapped[int] = mapped_column(Integer, default=0)
    location_score: Mapped[int] = mapped_column(Integer, default=0)
    experience_score: Mapped[int] = mapped_column(Integer, default=0)
    overall_score: Mapped[int] = mapped_column(Integer, default=0)
    score_explanation_json: Mapped[str] = mapped_column(Text, default="[]")

    # V0.4 separates market-review decisions from application workflow.
    review_status: Mapped[str] = mapped_column(String(40), default="unreviewed")
    verdict: Mapped[str] = mapped_column(String(40), default="needs_verification")
    required_languages_json: Mapped[str] = mapped_column(Text, default="[]")
    requirements_json: Mapped[str] = mapped_column(Text, default="{}")
    extraction_confidence_json: Mapped[str] = mapped_column(Text, default="{}")

    application_status: Mapped[str] = mapped_column(String(80), default="not_started")
    application_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cv_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    canonical_url: Mapped[str] = mapped_column(Text, default="")
    norm_title: Mapped[str] = mapped_column(String(500), index=True)
    norm_company: Mapped[str] = mapped_column(String(300), index=True)
    norm_location: Mapped[str] = mapped_column(String(300), index=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    sources: Mapped[list["JobSourceORM"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class JobSourceORM(Base):
    __tablename__ = "job_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    source_name: Mapped[str] = mapped_column(String(100), index=True)
    source_url: Mapped[str] = mapped_column(Text, unique=True)
    external_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    raw_payload_json: Mapped[str] = mapped_column(Text, default="{}")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    job: Mapped[JobORM] = relationship(back_populates="sources")


def get_engine(db_path: str):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path.as_posix()}", future=True)


def _ensure_v04_columns(engine) -> None:
    inspector = inspect(engine)
    if "jobs" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("jobs")}
    additions = {
        "review_status": "VARCHAR(40) NOT NULL DEFAULT 'unreviewed'",
        "verdict": "VARCHAR(40) NOT NULL DEFAULT 'needs_verification'",
        "required_languages_json": "TEXT NOT NULL DEFAULT '[]'",
        "requirements_json": "TEXT NOT NULL DEFAULT '{}'",
        "extraction_confidence_json": "TEXT NOT NULL DEFAULT '{}'",
    }
    with engine.begin() as conn:
        for name, ddl in additions.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE jobs ADD COLUMN {name} {ddl}"))

        # Migrate old overloaded statuses once. The operation is idempotent.
        conn.execute(text("UPDATE jobs SET review_status='shortlisted', application_status='not_started' WHERE application_status='shortlisted'"))
        conn.execute(text("UPDATE jobs SET review_status='dismissed', application_status='not_started' WHERE application_status IN ('rejected_by_you','rejected_by_user')"))
        conn.execute(text("UPDATE jobs SET application_status='not_started' WHERE application_status='discovered'"))
        conn.execute(text("UPDATE jobs SET review_status='unreviewed' WHERE review_status IS NULL OR review_status=''"))
        conn.execute(text("UPDATE jobs SET verdict='needs_verification' WHERE verdict IS NULL OR verdict=''"))

        # Remove the concrete false-positive observed in V0.3: a year parsed as salary.
        conn.execute(text("""
            UPDATE jobs
            SET salary_min=NULL, salary_max=NULL, salary_currency=NULL, salary_period=NULL
            WHERE salary_min BETWEEN 1900 AND 2100
              AND salary_max BETWEEN 1900 AND 2100
              AND (salary_currency IS NULL OR salary_currency='')
        """))


def init_db(engine) -> None:
    Base.metadata.create_all(engine)
    _ensure_v04_columns(engine)


def _same_remote_family(existing: JobORM, record: JobRecord) -> bool:
    if existing.remote and record.remote:
        return True
    a = norm_text(existing.location)
    b = norm_text(record.location)
    return bool(a and b and (a == b or a in b or b in a))


def _find_duplicate(session: Session, record: JobRecord) -> JobORM | None:
    normalized_company = norm_text(record.company)
    normalized_title = norm_text(record.title)
    normalized_location = norm_text(record.location)

    exact = session.scalar(
        select(JobORM).where(
            JobORM.norm_company == normalized_company,
            JobORM.norm_title == normalized_title,
            JobORM.norm_location == normalized_location,
        )
    )
    if exact:
        return exact

    if not normalized_company or normalized_company == "unknown":
        return None

    candidates = session.scalars(select(JobORM).where(JobORM.norm_company == normalized_company)).all()
    for candidate in candidates:
        title_score = fuzz.token_set_ratio(candidate.norm_title, normalized_title)
        company_score = fuzz.token_set_ratio(candidate.norm_company, normalized_company)
        if title_score >= 92 and company_score >= 95 and _same_remote_family(candidate, record):
            return candidate
    return None


def _valid_company(value: str | None) -> bool:
    n = norm_text(value)
    if not n or n in {"unknown", "entreprise anonyme"}:
        return False
    # Common Keejob sector labels that V0.1 accidentally stored as companies.
    bad = [
        "informatique telecoms", "recrutement ressources humaines", "banque finance assurances",
        "architecture immobilier btp", "sante paramedical optique", "call center televente",
        "agriculture agro alimentaire environnement",
    ]
    return n not in bad


def _apply_scores(job: JobORM, scores: ScoreResult) -> None:
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


def _apply_record_metadata(job: JobORM, record: JobRecord) -> None:
    if record.title and (not job.title or len(record.title) >= len(job.title)):
        job.title = record.title
        job.norm_title = norm_text(record.title)
    if _valid_company(record.company) and (not _valid_company(job.company) or norm_text(record.company) != norm_text(job.company)):
        job.company = record.company
        job.norm_company = norm_text(record.company)
    if record.location:
        job.location = record.location
        job.norm_location = norm_text(record.location)
    if len(record.description or "") > len(job.description or ""):
        job.description = record.description
    if record.employment_type:
        job.employment_type = record.employment_type
    if record.seniority:
        job.seniority = record.seniority
    job.min_years = record.min_years
    job.max_years = record.max_years
    if record.posted_at is not None:
        job.posted_at = record.posted_at
    if record.salary_min is not None:
        job.salary_min = record.salary_min
        job.salary_max = record.salary_max
        job.salary_currency = record.salary_currency
        job.salary_period = record.salary_period
    elif job.salary_min is not None and 1900 <= job.salary_min <= 2100 and not job.salary_currency:
        job.salary_min = None
        job.salary_max = None
        job.salary_period = None
    job.remote = bool(record.remote)
    if record.remote_region:
        job.remote_region = record.remote_region


def upsert_job(session: Session, record: JobRecord, scores: ScoreResult, stale: bool) -> tuple[JobORM, str]:
    existing_source = session.scalar(select(JobSourceORM).where(JobSourceORM.source_url == record.source_url))
    now = datetime.now(timezone.utc)

    if existing_source:
        job = existing_source.job
        _apply_record_metadata(job, record)
        _apply_scores(job, scores)
        job.stale = stale
        job.last_seen_at = now
        job.updated_at = now
        existing_source.last_seen_at = now
        existing_source.raw_payload_json = json.dumps(record.raw_payload, ensure_ascii=False, default=str)
        return job, "seen_again"

    job = _find_duplicate(session, record)
    action = "merged" if job else "inserted"

    if not job:
        job = JobORM(
            title=record.title,
            company=record.company or "Unknown",
            location=record.location,
            description=record.description,
            employment_type=record.employment_type,
            remote=record.remote,
            remote_region=record.remote_region,
            seniority=record.seniority,
            min_years=record.min_years,
            max_years=record.max_years,
            salary_min=record.salary_min,
            salary_max=record.salary_max,
            salary_currency=record.salary_currency,
            salary_period=record.salary_period,
            posted_at=record.posted_at,
            stale=stale,
            review_status="unreviewed",
            application_status="not_started",
            canonical_url=record.source_url,
            norm_title=norm_text(record.title),
            norm_company=norm_text(record.company),
            norm_location=norm_text(record.location),
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        _apply_scores(job, scores)
        session.add(job)
        session.flush()
    else:
        _apply_record_metadata(job, record)
        _apply_scores(job, scores)
        job.stale = job.stale and stale
        job.last_seen_at = now
        job.updated_at = now

    source = JobSourceORM(
        job_id=job.id,
        source_name=record.source_name,
        source_url=record.source_url,
        external_id=record.external_id,
        raw_payload_json=json.dumps(record.raw_payload, ensure_ascii=False, default=str),
        first_seen_at=now,
        last_seen_at=now,
    )
    session.add(source)
    return job, action