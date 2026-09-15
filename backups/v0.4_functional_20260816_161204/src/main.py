from __future__ import annotations

import argparse
import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .collectors.arbeitnow import ArbeitnowCollector
from .collectors.jobicy import JobicyCollector
from .collectors.keejob import KeejobCollector
from .collectors.remotive import RemotiveCollector
from .database import JobORM, get_engine, init_db, upsert_job
from .matcher import is_stale, score_job

ROOT = Path(__file__).resolve().parents[1]
console = Console()

COLLECTORS = {
    "jobicy": JobicyCollector,
    "remotive": RemotiveCollector,
    "arbeitnow": ArbeitnowCollector,
    "keejob": KeejobCollector,
}


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def setup_logging(settings: dict) -> Path:
    log_dir = ROOT / settings.get("logging", {}).get("directory", "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"dry_run_{stamp}.log"
    level = getattr(logging, settings.get("logging", {}).get("level", "INFO").upper(), logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )
    return log_path


def _poll_state_path() -> Path:
    return ROOT / "data" / "source_poll_state.json"


def load_poll_state() -> dict:
    path = _poll_state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_poll_state(state: dict) -> None:
    path = _poll_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def poll_allowed(source_name: str, source_cfg: dict, state: dict) -> bool:
    last = state.get(source_name)
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    min_minutes = int(source_cfg.get("min_interval_minutes", 0))
    elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
    return elapsed >= min_minutes


def save_raw_snapshot(run_id: str, source_name: str, records) -> None:
    raw_dir = ROOT / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    payload = [r.model_dump(mode="json") for r in records]
    (raw_dir / f"{run_id}_{source_name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def export_top_matches(engine, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Session(engine) as session:
        jobs = session.scalars(
            select(JobORM)
            .where(JobORM.stale.is_(False))
            .order_by(JobORM.overall_score.desc(), JobORM.posted_at.desc())
        ).all()

    fields = [
        "score", "title", "company", "location", "remote", "seniority",
        "employment_type", "posted_at", "source_url", "status",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for job in jobs:
            writer.writerow({
                "score": job.overall_score,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "remote": job.remote,
                "seniority": job.seniority,
                "employment_type": job.employment_type,
                "posted_at": job.posted_at.isoformat() if job.posted_at else "",
                "source_url": job.canonical_url,
                "status": job.application_status,
            })
    return len(jobs)


def show_top_jobs(engine, limit: int = 15) -> None:
    with Session(engine) as session:
        jobs = session.scalars(
            select(JobORM)
            .options(selectinload(JobORM.sources))
            .where(JobORM.stale.is_(False))
            .order_by(JobORM.overall_score.desc(), JobORM.posted_at.desc())
            .limit(limit)
        ).all()

    table = Table(title="Top live matches from the dry run")
    table.add_column("Score", justify="right")
    table.add_column("Title", overflow="fold")
    table.add_column("Company")
    table.add_column("Location")
    table.add_column("Remote")
    table.add_column("Source")

    for job in jobs:
        source = job.sources[0].source_name if job.sources else ""
        table.add_row(
            str(job.overall_score),
            job.title,
            job.company,
            job.location,
            "Yes" if job.remote else "No",
            source,
        )
    console.print(table)


def dry_run(max_jobs: int | None = None, ignore_poll_interval: bool = False) -> int:
    settings = load_yaml(ROOT / "config" / "settings.yaml")
    sources_cfg = load_yaml(ROOT / "config" / "sources.yaml").get("sources", {})
    profile = load_json(ROOT / "config" / "profile.json")
    log_path = setup_logging(settings)

    configured_max = int(settings.get("collection", {}).get("max_jobs_per_dry_run", 100))
    max_jobs = min(max_jobs or configured_max, configured_max)
    db_path = ROOT / settings.get("storage", {}).get("sqlite_path", "data/jobs.db")
    engine = get_engine(str(db_path))
    init_db(engine)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    poll_state = load_poll_state()
    records = []
    errors: dict[str, str] = {}
    enabled = [(name, cfg) for name, cfg in sources_cfg.items() if cfg.get("enabled", True)]
    per_source_default = max(1, max_jobs // max(1, len(enabled)))

    console.print(f"\n[bold]TarekJobs dry run[/bold] â€” max {max_jobs} raw listings")
    logging.info("Starting dry run with %s enabled sources", len(enabled))

    for source_name, cfg in enabled:
        if len(records) >= max_jobs:
            break

        source_limit = min(
            int(cfg.get("max_jobs", per_source_default)),
            per_source_default,
            max_jobs - len(records),
        )

        if not ignore_poll_interval and not poll_allowed(source_name, cfg, poll_state):
            msg = f"Skipped: minimum poll interval has not elapsed ({cfg.get('min_interval_minutes', 0)} min)."
            console.print(f"[yellow][{source_name.upper()}][/yellow] {msg}")
            logging.info("[%s] %s", source_name.upper(), msg)
            continue

        collector_cls = COLLECTORS.get(source_name)
        if not collector_cls:
            errors[source_name] = "No collector implementation"
            continue

        collector = collector_cls(cfg, settings)
        try:
            console.print(f"[cyan][{source_name.upper()}][/cyan] collecting up to {source_limit} jobs...")
            logging.info("[%s] collecting up to %s jobs", source_name.upper(), source_limit)
            fetched = collector.fetch(source_limit)
            save_raw_snapshot(run_id, source_name, fetched)
            records.extend(fetched)
            poll_state[source_name] = datetime.now(timezone.utc).isoformat()
            console.print(f"[green][{source_name.upper()}][/green] {len(fetched)} jobs fetched")
            logging.info("[%s] %s jobs fetched", source_name.upper(), len(fetched))
        except Exception as exc:
            errors[source_name] = f"{type(exc).__name__}: {exc}"
            console.print(f"[red][{source_name.upper()}][/red] failed: {exc}")
            logging.exception("[%s] collector failed", source_name.upper())
        finally:
            collector.close()

    save_poll_state(poll_state)

    counts = {"inserted": 0, "merged": 0, "seen_again": 0}
    stale_count = 0
    stale_after = int(settings.get("collection", {}).get("stale_after_days", 45))

    with Session(engine) as session:
        for record in records[:max_jobs]:
            scores = score_job(record, profile, settings)
            stale = is_stale(record, stale_after)
            if stale:
                stale_count += 1
            _, action = upsert_job(session, record, scores, stale)
            counts[action] = counts.get(action, 0) + 1
        session.commit()

    export_path = ROOT / settings.get("storage", {}).get(
        "export_top_matches_csv", "data/exports/top_matches.csv"
    )
    exported = export_top_matches(engine, export_path)

    show_top_jobs(engine)

    console.print("\n[bold]Dry-run summary[/bold]")
    console.print(f"Raw jobs fetched:   {len(records[:max_jobs])}")
    console.print(f"New canonical jobs: {counts.get('inserted', 0)}")
    console.print(f"Duplicates merged:  {counts.get('merged', 0)}")
    console.print(f"Already seen URLs:  {counts.get('seen_again', 0)}")
    console.print(f"Marked stale:       {stale_count}")
    console.print(f"Collector failures: {len(errors)}")
    console.print(f"Live jobs exported: {exported}")
    console.print(f"SQLite database:    {db_path}")
    console.print(f"CSV export:         {export_path}")
    console.print(f"Log file:           {log_path}")

    if errors:
        console.print("\n[yellow]Some collectors failed, but the pipeline continued:[/yellow]")
        for name, err in errors.items():
            console.print(f"  - {name}: {err}")

    # The pipeline itself succeeds if at least one source returned data.
    if not records:
        console.print("\n[red]No listings were fetched from any source.[/red]")
        return 2

    console.print("\n[green bold]DRY RUN PASSED[/green bold] â€” live data reached SQLite and the ranking export.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="TarekJobs personal job-market collector")
    sub = parser.add_subparsers(dest="command")

    dry = sub.add_parser("dry-run", help="Fetch a small live batch and run the full pipeline")
    dry.add_argument("--limit", type=int, default=None)
    dry.add_argument(
        "--ignore-poll-interval",
        action="store_true",
        help="Developer override; normal runs should respect source intervals.",
    )

    args = parser.parse_args()
    if args.command in (None, "dry-run"):
        return dry_run(
            max_jobs=getattr(args, "limit", None),
            ignore_poll_interval=getattr(args, "ignore_poll_interval", False),
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())