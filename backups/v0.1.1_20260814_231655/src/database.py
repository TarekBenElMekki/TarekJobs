from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from rapidfuzz import fuzz
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, select
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

    application_status: Mapped[str] = mapped_column(String(80), default="discovered")
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


def init_db(engine) -> None:
    Base.metadata.create_all(engine)


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

    candidates = session.scalars(
        select(JobORM).where(JobORM.norm_company == normalized_company)
    ).all()
    for candidate in candidates:
        title_score = fuzz.token_set_ratio(candidate.norm_title, normalized_title)
        company_score = fuzz.token_set_ratio(candidate.norm_company, normalized_company)
        if title_score >= 92 and company_score >= 95 and _same_remote_family(candidate, record):
            return candidate
    return None


def upsert_job(session: Session, record: JobRecord, scores: ScoreResult, stale: bool) -> tuple[JobORM, str]:
    existing_source = session.scalar(
        select(JobSourceORM).where(JobSourceORM.source_url == record.source_url)
    )
    now = datetime.now(timezone.utc)

    if existing_source:
        job = existing_source.job
        job.last_seen_at = now
        job.updated_at = now
        existing_source.last_seen_at = now
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
            technical_score=scores.technical,
            eligibility_score=scores.eligibility,
            location_score=scores.location,
            experience_score=scores.experience,
            overall_score=scores.overall,
            score_explanation_json=json.dumps(scores.explanations, ensure_ascii=False),
            canonical_url=record.source_url,
            norm_title=norm_text(record.title),
            norm_company=norm_text(record.company),
            norm_location=norm_text(record.location),
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        session.flush()
    else:
        # Keep the richest text/metadata found across sources.
        if len(record.description or "") > len(job.description or ""):
            job.description = record.description
        if not job.location and record.location:
            job.location = record.location
            job.norm_location = norm_text(record.location)
        if not job.employment_type and record.employment_type:
            job.employment_type = record.employment_type
        if not job.seniority and record.seniority:
            job.seniority = record.seniority
        if job.posted_at is None and record.posted_at is not None:
            job.posted_at = record.posted_at
        if job.salary_min is None and record.salary_min is not None:
            job.salary_min = record.salary_min
            job.salary_max = record.salary_max
            job.salary_currency = record.salary_currency
            job.salary_period = record.salary_period
        job.remote = job.remote or record.remote
        job.stale = job.stale and stale
        # Preserve the best-scoring representation until a full rescore pass exists.
        if scores.overall > job.overall_score:
            job.technical_score = scores.technical
            job.eligibility_score = scores.eligibility
            job.location_score = scores.location
            job.experience_score = scores.experience
            job.overall_score = scores.overall
            job.score_explanation_json = json.dumps(scores.explanations, ensure_ascii=False)
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