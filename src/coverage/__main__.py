"""Run with the project virtual environment: python -m src.coverage --help."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from .artifacts import canonical_json
from .contracts import Scope
from .fixture import FixtureAdapter
from .native_cli import add_commands, dispatch
from .service import CoverageService
from .storage import Database, migrate_scratch, one, rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="Coverage Core and Tunisia native acquisition")
    parser.add_argument("--db", required=True, help="Explicit SQLite path; no production default")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-scratch")
    commands.add_parser("list-sources")
    commands.add_parser("status")
    register = commands.add_parser("register-source")
    register.add_argument("--dry-run", action="store_true")
    start = commands.add_parser("start")
    start.add_argument("--fixture", required=True)
    start.add_argument("--artifacts", required=True)
    start.add_argument("--country", default="*")
    start.add_argument("--work-mode", default="*")
    start.add_argument("--region", default="")
    start.add_argument("--scan-kind", choices=("full","delta"), default="full")
    start.add_argument("--dry-run", action="store_true")
    resume = commands.add_parser("resume")
    resume.add_argument("run_id")
    resume.add_argument("--fixture", required=True)
    resume.add_argument("--retry-failed", action="store_true")
    for command in (start,resume):
        command.add_argument("--max-pages", type=int)
        command.add_argument("--max-details", type=int)
    for name in ("run","receipt","needs-scoring"):
        commands.add_parser(name).add_argument("run_id")
    add_commands(commands, register)
    args = parser.parse_args(argv)
    handled, result = dispatch(args)
    if handled:
        print(canonical_json(result))
        return 0
    db = Database(args.db)  # CLI never enables production mutation.
    if args.command == "init-scratch":
        migrate_scratch(args.db)
        result = {"migration":"004_coverage_core.sql","database":str(db.path)}
    elif args.command in {"start","register-source"}:
        adapter = FixtureAdapter(args.fixture)
        scope = Scope(getattr(args,"country","*"),getattr(args,"work_mode","*"),getattr(args,"region",""))
        if args.dry_run:
            result = {"dry_run":True,"metadata":asdict(adapter.metadata),
                      "scope":asdict(scope),"partitions":[asdict(p) for p in adapter.plan(scope)]}
        else:
            service = CoverageService(db,getattr(args,"artifacts",db.path.parent/"coverage_artifacts"))
            if args.command == "register-source":
                result = {"source_id":service.register(adapter)}
            else:
                run_id = service.start(adapter,scope,scan_kind=args.scan_kind)
                result = service.run(adapter,run_id,max_pages=args.max_pages,max_details=args.max_details)
    elif args.command in {"list-sources","status"}:
        with db.read() as connection:
            result = {"sources":rows(connection,"SELECT * FROM coverage_sources ORDER BY source_id")}
            if args.command == "status":
                result["coverage"] = rows(connection,"SELECT * FROM coverage_scopes ORDER BY coverage_id")
                result["runs"] = rows(connection,"SELECT * FROM coverage_runs ORDER BY started_at")
    else:
        with db.read() as connection:
            run = one(connection,"SELECT * FROM coverage_runs WHERE run_id=?", (args.run_id,))
        service = CoverageService(db,run["artifact_root"])
        if args.command == "resume":
            result = service.run(FixtureAdapter(args.fixture),args.run_id,
                                 max_pages=args.max_pages,max_details=args.max_details,
                                 retry_failed=args.retry_failed)
        elif args.command == "receipt":
            result = service.receipt(args.run_id)
        elif args.command == "needs-scoring":
            with db.read() as connection:
                result = rows(connection,"SELECT * FROM coverage_scoring_outbox WHERE run_id=? ORDER BY canonical_job_id", (args.run_id,))
        else:
            result = service.inspect(args.run_id)
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError,PermissionError,OSError) as exc:
        print(json.dumps({"error":type(exc).__name__,"message":str(exc)}),file=sys.stderr)
        raise SystemExit(2)
