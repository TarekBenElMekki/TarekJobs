"""Native commands plugged into the certified Coverage CLI."""
from dataclasses import asdict
from pathlib import Path

from .adapters.registry import ADAPTERS, create, info
from .contracts import Scope
from .diagnostics import diagnose
from .record_outcomes import migrate_native_scratch, require
from .service import CoverageService
from .storage import Database, one


def add_commands(commands, register):
    group = register.add_mutually_exclusive_group(required=True)
    group.add_argument("--fixture")
    group.add_argument("--adapter", choices=sorted(ADAPTERS))
    register.add_argument("--allow-production", action="store_true")
    commands.add_parser("adapter-list")
    commands.add_parser("init-native-scratch")
    about = commands.add_parser("adapter-info")
    about.add_argument("--adapter", required=True, choices=sorted(ADAPTERS))
    diagnostic = commands.add_parser("diagnose-source")
    diagnostic.add_argument("--adapter", required=True, choices=sorted(ADAPTERS))
    diagnostic.add_argument("--artifacts", required=True)
    diagnostic.add_argument("--inventory-only", action="store_true")
    start = commands.add_parser("start-source")
    start.add_argument("--adapter", required=True, choices=sorted(ADAPTERS))
    start.add_argument("--artifacts", required=True)
    start.add_argument("--scan-kind", choices=("full", "delta"), default="full")
    start.add_argument("--dry-run", action="store_true")
    resume = commands.add_parser("resume-source")
    resume.add_argument("run_id")
    resume.add_argument("--adapter", required=True, choices=sorted(ADAPTERS))
    resume.add_argument("--retry-failed", action="store_true")
    for command in (start, resume):
        command.add_argument("--allow-production", action="store_true")
        command.add_argument("--max-pages", type=int)
        command.add_argument("--max-details", type=int)
        command.add_argument("--inventory-only", action="store_true")


def dispatch(args):
    command = args.command
    native_register = command == "register-source" and args.adapter is not None
    if command not in {"adapter-list", "adapter-info", "diagnose-source", "init-native-scratch",
                       "start-source", "resume-source"} and not native_register:
        return False, None
    if command == "adapter-list":
        return True, [info(key) for key in sorted(ADAPTERS)]
    if command == "adapter-info":
        return True, info(args.adapter)
    if command == "diagnose-source":
        return True, diagnose(args.adapter, args.db, args.artifacts, sample_detail=not args.inventory_only)
    if command == "init-native-scratch":
        migrate_native_scratch(args.db)
        return True, {"migrations": ["004_coverage_core.sql", "005_coverage_record_outcomes.sql"],
                      "database": str(Path(args.db).resolve())}
    for limit in (getattr(args, "max_pages", None), getattr(args, "max_details", None)):
        if limit is not None and limit < 0:
            raise ValueError("Limits must be nonnegative")
    db = Database(args.db, allow_production=args.allow_production)
    scope = Scope(country="TN")
    cls = ADAPTERS[args.adapter]
    if getattr(args, "dry_run", False):
        # Construct no client and touch neither DB nor artifact directory.
        adapter = object.__new__(cls)
        return True, {"dry_run": True, **info(args.adapter),
                      "partitions": [asdict(p) for p in adapter.plan(scope)]}
    with db.read() as connection:
        require(connection)  # Fail before acquisition if additive schema is absent.
        run = one(connection, "SELECT * FROM coverage_runs WHERE run_id=?", (args.run_id,)) if command == "resume-source" else None
    root = run["artifact_root"] if run else getattr(args, "artifacts", db.path.parent / "coverage_artifacts")
    if native_register:
        # Metadata registration never creates an HTTP client.
        return True, CoverageService(db, root).register(object.__new__(cls))
    adapter = create(args.adapter, root)
    try:
        service = CoverageService(db, root)
        run_id = args.run_id if run else service.start(adapter, scope, scan_kind=args.scan_kind)
        result = service.run(adapter, run_id, max_pages=args.max_pages,
                             max_details=0 if args.inventory_only else args.max_details,
                             retry_failed=getattr(args, "retry_failed", False))
        return True, result
    finally:
        adapter.close()
