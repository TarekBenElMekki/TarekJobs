"""Write-safety regression using scratch databases only."""
import hashlib
import json
import sqlite3
import pytest
from src import production_write_guard as guard


def test_failed_backup_blocks_write(tmp_path, monkeypatch):
    target = tmp_path / "production.db"
    with sqlite3.connect(target) as connection:
        connection.execute("CREATE TABLE evidence (value TEXT)")
    before = target.read_bytes()
    def fail(*args):
        raise OSError("fixture backup failure")
    monkeypatch.setattr(guard, "_verified_backup", fail)
    with pytest.raises(guard.ProductionWriteSafetyError):
        with guard.production_write_guard(target, production_db_path=target, reason="test.block"):
            pytest.fail("Write scope admitted after backup failure")
    assert target.read_bytes() == before


def test_verified_backup_precedes_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    target = tmp_path / "production.db"
    with sqlite3.connect(target) as connection:
        connection.execute("CREATE TABLE evidence (value TEXT)")
        connection.execute("INSERT INTO evidence VALUES ('before')")
    with guard.production_write_guard(target, production_db_path=target, reason="test.backup"):
        with sqlite3.connect(target) as connection:
            connection.execute("UPDATE evidence SET value='after'")
    directory = tmp_path / "TTEASafety/automatic_prewrite"
    backup = directory / "jobs_prewrite_latest.db"
    receipt = json.loads((directory / "jobs_prewrite_latest.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == receipt["backup_sha256"]
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT value FROM evidence").fetchone() == ("before",)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_scratch_write_never_creates_backup(tmp_path, monkeypatch):
    def fail(*args):
        pytest.fail("Scratch database requested production backup")
    monkeypatch.setattr(guard, "_verified_backup", fail)
    with guard.production_write_guard(tmp_path / "scratch.db", production_db_path=tmp_path / "production.db", reason="test.scratch"):
        pass
