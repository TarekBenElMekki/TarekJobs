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
    UniqueConstraint,
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

    # V0.5 Global Opportunity Core. Historical rows remain type=job.
    opportunity_type: Mapped[str] = mapped_column(String(20), default="job")
    country: Mapped[str] = mapped_column(String(120), default="")
    city: Mapped[str] = mapped_column(String(160), default="")
    work_mode: Mapped[str] = mapped_column(String(40), default="")
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)

    education_requirement: Mapped[str] = mapped_column(Text, default="")
    visa_status: Mapped[str] = mapped_column(String(60), default="unknown")
    visa_sponsorship: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    international_candidates: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    relocation_support: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    funding_status: Mapped[str] = mapped_column(String(80), default="")
    tuition_covered: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    housing_support: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    insurance_support: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    travel_support: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    benefits_json: Mapped[str] = mapped_column(Text, default="{}")

    technical_score: Mapped[int] = mapped_column(Integer, default=0)
    eligibility_score: Mapped[int] = mapped_column(Integer, default=0)
    location_score: Mapped[int] = mapped_column(Integer, default=0)
    experience_score: Mapped[int] = mapped_column(Integer, default=0)
    education_score: Mapped[int] = mapped_column(Integer, default=0)
    domain_score: Mapped[int] = mapped_column(Integer, default=0)
    visa_score: Mapped[int] = mapped_column(Integer, default=0)
    salary_score: Mapped[int] = mapped_column(Integer, default=0)
    language_score: Mapped[int] = mapped_column(Integer, default=0)
    overall_score: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[str] = mapped_column(String(20), default="discard")
    eligibility_status: Mapped[str] = mapped_column(String(40), default="needs_verification")
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
    cover_letter_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    interview_stage: Mapped[str | None] = mapped_column(String(120), nullable=True)
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
    ttea_assessments: Mapped[list["TteaAssessmentORM"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="selectin",
        order_by="(TteaAssessmentORM.scored_at.desc(), TteaAssessmentORM.imported_at.desc(), TteaAssessmentORM.id.desc())",
    )


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


class TteaRunORM(Base):
    __tablename__ = "ttea_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_run_id: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(40))
    source_name: Mapped[str] = mapped_column(String(100), default="")
    provider: Mapped[str] = mapped_column(String(100), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    counts_json: Mapped[str] = mapped_column(Text, default="{}")
    usage_json: Mapped[str] = mapped_column(Text, default="{}")


class TteaAssessmentORM(Base):
    __tablename__ = "ttea_assessments"
    __table_args__ = (UniqueConstraint("job_id", "run_id", name="uq_ttea_job_run"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("ttea_runs.id"), index=True)
    score: Mapped[float] = mapped_column(Float)
    strengths_json: Mapped[str] = mapped_column(Text, default="[]")
    gaps_json: Mapped[str] = mapped_column(Text, default="[]")
    explanation: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(100), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    job: Mapped[JobORM] = relationship(back_populates="ttea_assessments")
    run: Mapped[TteaRunORM] = relationship(lazy="joined")


def get_engine(db_path: str):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path.as_posix()}", future=True)


def _ensure_v04_columns(engine) -> None:
    """Idempotent schema evolution through V0.5.0.

    The historical function name is retained because older scripts import or
    conceptually depend on it, but it now owns all additive migrations.
    """
    inspector = inspect(engine)
    if "jobs" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("jobs")}
    additions = {
        # V0.4 columns
        "review_status": "VARCHAR(40) NOT NULL DEFAULT 'unreviewed'",
        "verdict": "VARCHAR(40) NOT NULL DEFAULT 'needs_verification'",
        "required_languages_json": "TEXT NOT NULL DEFAULT '[]'",
        "requirements_json": "TEXT NOT NULL DEFAULT '{}'",
        "extraction_confidence_json": "TEXT NOT NULL DEFAULT '{}'",
        # V0.5 opportunity identity / geography / freshness
        "opportunity_type": "VARCHAR(20) NOT NULL DEFAULT 'job'",
        "country": "VARCHAR(120) NOT NULL DEFAULT ''",
        "city": "VARCHAR(160) NOT NULL DEFAULT ''",
        "work_mode": "VARCHAR(40) NOT NULL DEFAULT ''",
        "deadline": "DATETIME",
        "last_verified_at": "DATETIME",
        "is_open": "BOOLEAN NOT NULL DEFAULT 1",
        # V0.5 eligibility / funding metadata
        "education_requirement": "TEXT NOT NULL DEFAULT ''",
        "visa_status": "VARCHAR(60) NOT NULL DEFAULT 'unknown'",
        "visa_sponsorship": "BOOLEAN",
        "international_candidates": "BOOLEAN",
        "relocation_support": "BOOLEAN",
        "funding_status": "VARCHAR(80) NOT NULL DEFAULT ''",
        "tuition_covered": "BOOLEAN",
        "housing_support": "BOOLEAN",
        "insurance_support": "BOOLEAN",
        "travel_support": "BOOLEAN",
        "benefits_json": "TEXT NOT NULL DEFAULT '{}'",
        # V0.5 TarekFit V2 components
        "education_score": "INTEGER NOT NULL DEFAULT 0",
        "domain_score": "INTEGER NOT NULL DEFAULT 0",
        "visa_score": "INTEGER NOT NULL DEFAULT 0",
        "salary_score": "INTEGER NOT NULL DEFAULT 0",
        "language_score": "INTEGER NOT NULL DEFAULT 0",
        "priority": "VARCHAR(20) NOT NULL DEFAULT 'discard'",
        "eligibility_status": "VARCHAR(40) NOT NULL DEFAULT 'needs_verification'",
        # richer application tracking
        "cover_letter_version": "VARCHAR(200)",
        "response": "TEXT",
        "interview_stage": "VARCHAR(120)",
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
        conn.execute(text("UPDATE jobs SET opportunity_type='job' WHERE opportunity_type IS NULL OR opportunity_type=''"))
        conn.execute(text("UPDATE jobs SET priority='discard' WHERE priority IS NULL OR priority=''"))
        conn.execute(text("UPDATE jobs SET eligibility_status='needs_verification' WHERE eligibility_status IS NULL OR eligibility_status=''"))
        conn.execute(text("UPDATE jobs SET visa_status='unknown' WHERE visa_status IS NULL OR visa_status=''"))
        conn.execute(text("UPDATE jobs SET is_open=1 WHERE is_open IS NULL"))

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
            JobORM.opportunity_type == (record.opportunity_type or "job").lower(),
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
        select(JobORM).where(
            JobORM.norm_company == normalized_company,
            JobORM.opportunity_type == (record.opportunity_type or "job").lower(),
        )
    ).all()
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
    if not job.country:
        job.country = str(scores.requirements.get("country") or "")
    if not job.work_mode:
        job.work_mode = "remote" if job.remote else "onsite_or_hybrid"


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
    job.opportunity_type = (record.opportunity_type or job.opportunity_type or "job").lower()
    if record.country:
        job.country = record.country
    if record.city:
        job.city = record.city
    if record.work_mode:
        job.work_mode = record.work_mode
    if record.deadline is not None:
        job.deadline = record.deadline
    if record.last_verified_at is not None:
        job.last_verified_at = record.last_verified_at
    job.is_open = bool(record.is_open)
    if record.education_requirement:
        job.education_requirement = record.education_requirement
    if record.visa_status and record.visa_status != "unknown":
        job.visa_status = record.visa_status
    if record.visa_sponsorship is not None:
        job.visa_sponsorship = record.visa_sponsorship
    if record.international_candidates is not None:
        job.international_candidates = record.international_candidates
    if record.relocation_support is not None:
        job.relocation_support = record.relocation_support
    if record.funding_status:
        job.funding_status = record.funding_status
    if record.tuition_covered is not None:
        job.tuition_covered = record.tuition_covered
    if record.housing_support is not None:
        job.housing_support = record.housing_support
    if record.insurance_support is not None:
        job.insurance_support = record.insurance_support
    if record.travel_support is not None:
        job.travel_support = record.travel_support
    if record.benefits:
        job.benefits_json = json.dumps(record.benefits, ensure_ascii=False, default=str)
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


def _apply_import_metadata(job: JobORM, record: JobRecord) -> None:
    """Fill missing evidence without resetting richer metadata or human decisions."""
    if not job.title:
        job.title = record.title
        job.norm_title = norm_text(record.title)
    if not job.work_mode and record.work_mode:
        job.remote = record.remote
    for name in (
        "location", "employment_type", "remote_region", "seniority", "min_years", "max_years",
        "posted_at", "country", "city", "work_mode", "education_requirement",
    ):
        value = getattr(record, name)
        if getattr(job, name) in (None, "") and value not in (None, ""):
            setattr(job, name, value)
    if not _valid_company(job.company) and _valid_company(record.company):
        job.company = record.company
    if len(record.description or "") > len(job.description or ""):
        job.description = record.description
    job.norm_company = norm_text(job.company)
    job.norm_location = norm_text(job.location)


def upsert_job(session: Session, record: JobRecord, scores: ScoreResult, stale: bool) -> tuple[JobORM, str]:
    return _upsert_job(session, record, scores, stale)


def upsert_job_metadata(session: Session, record: JobRecord) -> tuple[JobORM, str]:
    """Reuse canonical/source dedup without assigning any legacy scores."""
    return _upsert_job(session, record, None, False)


def upsert_job_metadata_strong(
    session: Session,
    record: JobRecord,
) -> tuple[JobORM, str]:
    """Upsert using strong source identity only.

    Intended for browser-grounded Hunting opportunities.

    Identity order:
      1. exact source URL;
      2. same source/provider + external posting ID;
      3. exact canonical URL already held by a job;
      4. otherwise insert a distinct job.

    The legacy title/company/location fuzzy matcher is deliberately
    excluded from this path.
    """
    return _upsert_job(
        session,
        record,
        None,
        False,
        strong_identity_only=True,
    )


def _upsert_job(
    session: Session,
    record: JobRecord,
    scores: ScoreResult | None,
    stale: bool,
    *,
    strong_identity_only: bool = False,
) -> tuple[JobORM, str]:
    existing_source = session.scalar(
        select(JobSourceORM).where(
            JobSourceORM.source_url == record.source_url
        )
    )

    if (
        existing_source is None
        and strong_identity_only
        and record.source_name
        and record.external_id
    ):
        existing_source = session.scalar(
            select(JobSourceORM).where(
                JobSourceORM.source_name == record.source_name,
                JobSourceORM.external_id == record.external_id,
            )
        )

    now = datetime.now(timezone.utc)

    if existing_source:
        job = existing_source.job
        (_apply_record_metadata if scores is not None else _apply_import_metadata)(job, record)
        if scores is not None:
            _apply_scores(job, scores)
        job.stale = stale
        job.last_seen_at = now
        job.last_verified_at = record.last_verified_at or now
        job.updated_at = now
        existing_source.last_seen_at = now

        if (
            strong_identity_only
            and record.external_id
            and not existing_source.external_id
        ):
            existing_source.external_id = record.external_id

        existing_source.raw_payload_json = json.dumps(
            record.raw_payload,
            ensure_ascii=False,
            default=str,
        )

        return job, "seen_again"

    if strong_identity_only:
        job = session.scalar(
            select(JobORM).where(
                JobORM.canonical_url == record.source_url
            )
        )

        action = (
            "matched_canonical_url"
            if job
            else "inserted"
        )
    else:
        job = _find_duplicate(
            session,
            record,
        )

        action = (
            "merged"
            if job
            else "inserted"
        )

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
            opportunity_type=(record.opportunity_type or "job").lower(),
            country=record.country,
            city=record.city,
            work_mode=record.work_mode,
            deadline=record.deadline,
            last_verified_at=record.last_verified_at or now,
            is_open=record.is_open,
            education_requirement=record.education_requirement,
            visa_status=record.visa_status or "unknown",
            visa_sponsorship=record.visa_sponsorship,
            international_candidates=record.international_candidates,
            relocation_support=record.relocation_support,
            funding_status=record.funding_status,
            tuition_covered=record.tuition_covered,
            housing_support=record.housing_support,
            insurance_support=record.insurance_support,
            travel_support=record.travel_support,
            benefits_json=json.dumps(record.benefits, ensure_ascii=False, default=str),
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
        if scores is not None:
            _apply_scores(job, scores)
        session.add(job)
        session.flush()
    else:
        (_apply_record_metadata if scores is not None else _apply_import_metadata)(job, record)
        if scores is not None:
            _apply_scores(job, scores)
        job.stale = job.stale and stale
        job.last_seen_at = now
        job.last_verified_at = record.last_verified_at or now
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
