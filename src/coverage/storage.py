"""Explicit paths, read-only inspection, additive migrations and guarded writes."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine

from .. import production_write_guard as guard
from .artifacts import utc_now

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations" / "004_coverage_core.sql"


def is_production(path: Path) -> bool:
    configured = guard.configured_production_path()
    # Keep the authoritative production file protected even if config changes.
    for production in {configured, ROOT / "data" / "jobs.db"}:
        if path.resolve() == production.resolve():
            return True
        if path.exists() and production.exists() and os.path.samefile(path, production):
            return True
    return False


def rows(connection, sql, parameters=()):
    return [dict(row) for row in connection.exec_driver_sql(sql, parameters).mappings()]


def one(connection, sql, parameters=()):
    result = rows(connection, sql, parameters)
    if not result:
        raise ValueError("Coverage record not found")
    return result[0]


def event(connection, run_id, event_type: str):
    # Fixed event codes only; never exception text, URLs, tokens or payloads.
    connection.exec_driver_sql(
        "INSERT INTO coverage_events(run_id,event_type,occurred_at) VALUES (?,?,?)",
        (run_id, event_type, utc_now()))


class Database:
    def __init__(self, path: str | Path, *, allow_production: bool = False):
        if not path or str(path) == ":memory:":
            raise ValueError("Coverage requires an explicit persistent database path")
        self.path = Path(path).resolve()
        self.allow_production = allow_production

    @contextmanager
    def read(self):
        engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(
            self.path.as_uri() + "?mode=ro", uri=True, timeout=30))
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                yield connection
        finally:
            engine.dispose()

    @contextmanager
    def write(self, reason="coverage_write"):
        production = is_production(self.path)
        if production and not self.allow_production:
            raise PermissionError("Production mutation requires explicit opt-in")
        if not self.path.exists():
            raise ValueError("Initialize an explicit scratch database first")
        # Passing an alias as production_db_path ensures hardlinks receive a backup.
        with guard.production_write_guard(
            self.path, reason=reason,
            production_db_path=self.path if production else None,
        ):
            engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(
                self.path.as_uri() + "?mode=rw", uri=True, timeout=30))
            try:
                with engine.connect() as connection:
                    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                    connection.commit()
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                    try:
                        yield connection
                        connection.commit()
                    except BaseException:
                        connection.rollback()
                        raise
            finally:
                engine.dispose()


def migrate_scratch(path: str | Path):
    database = Database(path)
    if is_production(database.path):
        raise PermissionError("TN-0 migration is restricted to scratch databases")
    # Never invoke legacy init_db: it also repairs historical rows.
    from ..database import Base
    database.path.parent.mkdir(parents=True, exist_ok=True)
    if not database.path.exists():
        sqlite3.connect(database.path).close()
    with database.write("coverage_scratch_migration") as connection:
        Base.metadata.create_all(connection)
        statement = ""
        for line in MIGRATION.read_text(encoding="utf-8").splitlines(True):
            statement += line
            if sqlite3.complete_statement(statement):
                connection.exec_driver_sql(statement)
                statement = ""
        if statement.strip():
            raise ValueError("Incomplete Coverage Core migration")
        event(connection, None, "migration_004")
