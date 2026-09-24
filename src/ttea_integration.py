"""Offline, atomic import of completed T/TEA Job Hunter artifacts.

Usage: python -m src.ttea_integration import-run --run-dir PATH [--db PATH]
Only known evidence fields are retained; manifests and source documents are
never copied wholesale. The first assessment for a canonical job/run wins.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .database import (
    JobSourceORM, TteaAssessmentORM, TteaRunORM, get_engine, init_db,
    upsert_job_metadata,
)
from .models import JobRecord

COUNT_KEYS = frozenset("""cache_hits cards_discovered cards_rejected_before_detail
cloud_cache_hits cloud_candidates cloud_requests cloud_scored conditional_requests
detail_pages_avoided detail_requests deterministic_relevant discovered_offer_urls
extracted fetched_offer_urls listing_requests malformed_or_failed not_modified_304
robots_requests source_requests_total unique_normalized""".split())
USAGE_KEYS = frozenset("prompt_tokens completion_tokens total_tokens input_tokens output_tokens".split())


class ImportRunError(ValueError):
    """Invalid local artifact; no import data has been committed."""


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"p", "br", "li", "div"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "li", "div"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _text(value, limit=20000):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ImportRunError("Expected a text field")
    parser = _PlainText()
    parser.feed(value)
    return "\n".join(" ".join(line.split()) for line in "".join(parser.parts).splitlines() if line.strip())[:limit]


def _date(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ImportRunError("Invalid artifact date") from exc


def _object(value, label):
    if not isinstance(value, dict):
        raise ImportRunError(f"{label} must be a JSON object")
    return value


def _read(path):
    try:
        return _object(json.loads(path.read_text(encoding="utf-8")), path.name)
    except (OSError, UnicodeError) as exc:
        raise ImportRunError(f"Cannot read {path.name} as UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ImportRunError(f"Malformed {path.name}: line {exc.lineno}, column {exc.colno}") from exc


def _numbers(value, allowed):
    value = _object(value or {}, "counts/usage")
    return {key: number for key, number in value.items()
            if key in allowed and type(number) in (int, float)
            and math.isfinite(number) and 0 <= number <= 10**15}


def _strings(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ImportRunError("Strengths, gaps and qualifications must be arrays")
    return [_text(item, 2000) for item in value[:50]]


def _url(value):
    if not isinstance(value, str) or len(value) > 4000:
        raise ImportRunError("A valid source URL is required for every job")
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
        raise ImportRunError("Source URL must be HTTP(S) without credentials")
    return value


def _entry(entry, manifest):
    entry = _object(entry, "Ranked job")
    job = _object(entry.get("job"), "Ranked job.job")
    evidence = _object(entry.get("source_evidence") or {}, "source_evidence")
    title = _text(job.get("title") or evidence.get("title"), 500)
    if not title:
        raise ImportRunError("Every job needs a title")
    score = entry.get("score")
    if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 100:
        raise ImportRunError("Every ranked job needs a finite score from 0 to 100")
    sources = job.get("sources") or []
    if not isinstance(sources, list):
        raise ImportRunError("job.sources must be an array")
    provenance = {}
    primary = entry.get("source_url") or evidence.get("source_url")
    if primary:
        provenance[_url(primary)] = _text(evidence.get("source_reference"), 300) or None
    for source in sources:
        source = _object(source, "job.sources item")
        url = _url(source.get("source_url"))
        provenance[url] = _text(source.get("source_reference"), 300) or provenance.get(url)
    if not provenance:
        raise ImportRunError("Every job needs source provenance")
    years = job.get("min_experience_years")
    if years is not None and (type(years) not in (int, float) or not math.isfinite(years) or years < 0):
        raise ImportRunError("Invalid min_experience_years")
    # JobRecord has integer years: retain fractional evidence verbatim below.
    integer_years = int(years) if years is not None and float(years).is_integer() else None
    description = _text(evidence.get("description"), 100000)
    experience = _text(evidence.get("experience"), 500)
    languages = _text(evidence.get("languages"), 500)
    for label, value in (("Experience", experience), ("Languages", languages)):
        if value:
            description += f"\n{label}: {value}"
    if years is not None and integer_years is None:
        description += f"\nMinimum experience years: {years}"
    arrangement = _text(job.get("working_arrangement"), 40)
    records = [JobRecord(
        title=title, company=_text(job.get("company") or evidence.get("company"), 300) or "Unknown",
        location=_text(job.get("location") or evidence.get("location"), 300),
        description=description.strip(), employment_type=_text(evidence.get("contract"), 80),
        seniority=_text(job.get("experience_level"), 80), min_years=integer_years,
        education_requirement=_text(evidence.get("education")) or "; ".join(_strings(job.get("qualifications"))),
        work_mode=arrangement, remote=arrangement.lower() == "remote",
        posted_at=_date(evidence.get("publication_date")),
        source_name=_text(manifest.get("source"), 100) or "ttea",
        source_url=url, external_id=external_id,
        raw_payload={"integration": "ttea", "ttea_run_id": manifest["run_id"],
                     "source_job_id": _text(job.get("job_id"), 200)},
    ) for url, external_id in provenance.items()]
    assessment = dict(
        score=score, strengths_json=json.dumps(_strings(entry.get("strengths")), ensure_ascii=False),
        gaps_json=json.dumps(_strings(entry.get("gaps")), ensure_ascii=False),
        explanation=_text(entry.get("explanation")),
        provider=_text(entry.get("provider") or manifest.get("provider"), 100),
        model=_text(entry.get("model") or manifest.get("model"), 200),
        scored_at=_date(entry.get("scored_at")),
    )
    return records, assessment, languages


def import_run(run_dir: str | Path, engine) -> dict:
    """Import in one transaction into an initialized DB (caller owns schema setup)."""
    run_dir = Path(run_dir)
    manifest = _read(run_dir / "run.json")
    ranked = _read(run_dir / "ranked_jobs.json")
    external_id = manifest.get("run_id")
    if not isinstance(external_id, str) or not external_id.strip() or len(external_id) > 200:
        raise ImportRunError("run.json needs a nonempty run_id of at most 200 characters")
    if manifest.get("status") != "completed":
        raise ImportRunError("Only completed T/TEA runs can be imported")
    entries = ranked.get("jobs")
    if not isinstance(entries, list):
        raise ImportRunError("ranked_jobs.json needs a jobs array")
    metadata = _object(ranked.get("metadata") or {}, "ranked metadata")
    if metadata.get("run_id", external_id) != external_id:
        raise ImportRunError("Artifact run IDs do not match")
    parsed = [_entry(entry, manifest) for entry in entries]
    now = datetime.now(timezone.utc)
    run_values = dict(
        external_run_id=external_id, status="completed",
        source_name=_text(manifest.get("source"), 100),
        provider=_text(manifest.get("provider"), 100), model=_text(manifest.get("model"), 200),
        started_at=_date(manifest.get("started_at")), finished_at=_date(manifest.get("completed_at")),
        imported_at=now, counts_json=json.dumps(_numbers(manifest.get("counts"), COUNT_KEYS)),
        usage_json=json.dumps(_numbers(manifest.get("usage"), USAGE_KEYS)),
    )
    summary = dict(run_id=external_id, runs_added=0, runs_reused=0, jobs_read=len(entries),
                   new_jobs=0, existing_jobs=0, sources_added=0, sources_reused=0,
                   assessments_added=0, assessments_reused=0)
    with Session(engine) as session, session.begin():
        run = session.scalar(select(TteaRunORM).where(TteaRunORM.external_run_id == external_id))
        if run is None:
            run = TteaRunORM(**run_values)
            session.add(run)
            session.flush()
            summary["runs_added"] = 1
        else:
            summary["runs_reused"] = 1
        for records, assessment, languages in parsed:
            canonical = None
            for index, record in enumerate(records):
                source = session.scalar(select(JobSourceORM).where(JobSourceORM.source_url == record.source_url))
                job, action = upsert_job_metadata(session, record)
                session.flush()
                if canonical is not None and canonical.id != job.id:
                    raise ImportRunError("Source URLs in one ranked job resolve to different canonical jobs")
                canonical = job
                if index == 0:
                    summary["new_jobs" if action == "inserted" else "existing_jobs"] += 1
                summary["sources_reused" if source else "sources_added"] += 1
            if languages and canonical.required_languages_json in (None, "", "[]"):
                # Preserve the source wording; do not infer separate mandatory languages.
                canonical.required_languages_json = json.dumps([languages], ensure_ascii=False)
            existing = session.scalar(select(TteaAssessmentORM).where(
                TteaAssessmentORM.job_id == canonical.id, TteaAssessmentORM.run_id == run.id))
            if existing is None:
                # Inspected artifacts have no per-job timestamp. Prefer run completion
                # (then start/import time), so importing an old run cannot hide newer scores.
                assessment["scored_at"] = assessment["scored_at"] or run.finished_at or run.started_at or run.imported_at
                session.add(TteaAssessmentORM(job_id=canonical.id, run_id=run.id, imported_at=now, **assessment))
                session.flush()
                summary["assessments_added"] += 1
            else:
                summary["assessments_reused"] += 1
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("import-run")
    command.add_argument("--run-dir", required=True, type=Path)
    command.add_argument("--db", type=Path, help="Override the configured SQLite database")
    args = parser.parse_args(argv)
    engine = None
    try:
        # Check JSON before opening any database; import_run performs full validation.
        _read(args.run_dir / "run.json")
        _read(args.run_dir / "ranked_jobs.json")
        root = Path(__file__).resolve().parents[1]
        settings = yaml.safe_load((root / "config" / "settings.yaml").read_text(encoding="utf-8")) or {}
        path = args.db or root / settings.get("storage", {}).get("sqlite_path", "data/jobs.db")
        engine = get_engine(str(path))
        init_db(engine)
        print(json.dumps(import_run(args.run_dir, engine), ensure_ascii=False))
        return 0
    except (ImportRunError, OSError, ValueError, SQLAlchemyError) as exc:
        # Database exception strings can contain SQL parameters; never echo them.
        message = str(exc) if isinstance(exc, ImportRunError) else "Import failed; no run data committed"
        print(f"T/TEA import error: {message}", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
