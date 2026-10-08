"""Verified rolling pre-write backups for the configured production SQLite file.

Importing this module does not access databases or create files. Callers must
hold the context across connection opening, transaction commit/rollback and close.
The RLock coordinates this server process, not independent CLI/server processes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml

GUARD_VERSION = '1.3.1'
BLOCKED_MESSAGE = 'Production write blocked: verified pre-write backup unavailable.'
_WRITE_LOCK = threading.RLock()
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ProductionWriteSafetyError(RuntimeError):
    """No mutation is admitted when a fresh verified backup cannot be published."""


def configured_production_path(project_root: str | Path | None = None) -> Path:
    root = Path(project_root) if project_root is not None else _PROJECT_ROOT
    with (root / 'config' / 'settings.yaml').open(encoding='utf-8') as stream:
        settings = yaml.safe_load(stream) or {}
    configured = settings.get('storage', {}).get('sqlite_path', 'data/jobs.db')
    return (root / configured).resolve()


def _log(event: str, reason: str) -> None:
    # Fixed caller reason labels only. Never log request bodies, tokens or URLs.
    try:
        logger = logging.getLogger('ttea.production_write_guard')
        if not logger.handlers:
            directory = _PROJECT_ROOT / 'logs'
            directory.mkdir(exist_ok=True)
            handler = RotatingFileHandler(directory / 'production_write_guard.log',
                                          maxBytes=1_000_000, backupCount=2, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
        logger.info('%s reason=%s', event, reason)
    except OSError:
        # The published JSON receipt is also a persistent record of verification.
        pass


def _temporary(directory: Path, suffix: str) -> Path:
    descriptor, name = tempfile.mkstemp(prefix='.jobs_prewrite_', suffix=suffix, dir=directory)
    os.close(descriptor)
    return Path(name)


def _verified_backup(production: Path, reason: str) -> dict:
    profile = os.environ.get('USERPROFILE')
    directory = (Path(profile) if profile else Path.home()) / 'TTEASafety' / 'automatic_prewrite'
    directory.mkdir(parents=True, exist_ok=True)
    latest = directory / 'jobs_prewrite_latest.db'
    receipt_path = directory / 'jobs_prewrite_latest.json'
    if latest.resolve() == production or receipt_path.resolve() == production:
        raise ProductionWriteSafetyError(BLOCKED_MESSAGE)
    temporary_db = temporary_json = None
    try:
        temporary_db = _temporary(directory, '.db')
        deadline = time.monotonic() + 30

        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise TimeoutError('SQLite backup deadline exceeded')

        # Read-only URI prevents accidental creation or writes to the source.
        with closing(sqlite3.connect(production.as_uri() + '?mode=ro', uri=True, timeout=5)) as source:
            with closing(sqlite3.connect(temporary_db)) as destination:
                source.backup(destination, pages=256, progress=progress, sleep=0.1)
                # Publish one self-contained DB, never a DB dependent on sidecars.
                journal_mode = destination.execute('PRAGMA journal_mode=DELETE').fetchone()
                if not journal_mode or journal_mode[0].lower() != 'delete':
                    raise ProductionWriteSafetyError(BLOCKED_MESSAGE)
                destination.commit()

        # Independent verification connection after both backup handles are closed.
        with closing(sqlite3.connect(temporary_db.as_uri() + '?mode=ro', uri=True)) as verified:
            integrity = verified.execute('PRAGMA integrity_check').fetchall()
            foreign_keys = verified.execute('PRAGMA foreign_key_check').fetchall()
        if integrity != [('ok',)] or foreign_keys:
            raise ProductionWriteSafetyError(BLOCKED_MESSAGE)

        digest = hashlib.sha256()
        with temporary_db.open('r+b') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
            os.fsync(stream.fileno())
        receipt = {
            'guard_version': GUARD_VERSION,
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'reason': reason,
            'production_db_path': str(production),
            'backup_path': str(latest),
            'backup_sha256': digest.hexdigest(),
            # Main-file sizes are informational; WAL content is included by backup().
            'production_db_size_bytes': production.stat().st_size,
            'backup_size_bytes': temporary_db.stat().st_size,
        }
        temporary_json = _temporary(directory, '.json')
        with temporary_json.open('w', encoding='utf-8', newline='\n') as stream:
            json.dump(receipt, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())

        # Do not unlink the old verified backup. Each replacement is atomic and
        # on the same filesystem. Both must succeed before admitting a mutation.
        os.replace(temporary_db, latest)
        temporary_db = None
        os.replace(temporary_json, receipt_path)
        temporary_json = None
        return receipt
    finally:
        for temporary in (temporary_db, temporary_json):
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    _log('temporary_cleanup_failed', reason)


@contextmanager
def production_write_guard(
    target_db: str | Path | None, *, reason: str,
    production_db_path: str | Path | None = None,
):
    # SQLite memory targets and unrelated scratch/test files never create backups.
    if target_db is None or str(target_db) in {'', ':memory:'}:
        yield
        return
    try:
        production = (Path(production_db_path).resolve() if production_db_path is not None
                      else configured_production_path())
        target = Path(target_db).resolve()
    except Exception as exc:
        raise ProductionWriteSafetyError(BLOCKED_MESSAGE) from exc
    if target != production:
        yield
        return

    with _WRITE_LOCK:
        try:
            _verified_backup(production, reason)
        except Exception as exc:
            _log('backup_failed_write_blocked', reason)
            raise ProductionWriteSafetyError(BLOCKED_MESSAGE) from exc
        _log('backup_verified_write_admitted', reason)
        try:
            yield
        except BaseException:
            _log('guarded_write_scope_failed', reason)
            raise
        else:
            _log('guarded_write_scope_completed', reason)
        # RLock release happens only after caller commit/rollback/connection close.
