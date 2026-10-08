"""Clean-checkout migration dependencies; scratch SQLite only."""
import sqlite3
from pathlib import Path
from src.database import get_engine, init_db

ROOT = Path(__file__).resolve().parents[1]


def test_complete_migration_chain_on_empty_database(tmp_path):
    path = tmp_path / "release.db"
    engine = get_engine(str(path))
    init_db(engine)
    engine.dispose()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        for migration in sorted((ROOT / "migrations").glob("*.sql")):
            connection.executescript(migration.read_text(encoding="utf-8-sig"))
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        expected = {
            "applications": {"job_id", "current_state", "submitted_at"},
            "application_events": {"application_id", "metadata_json"},
            "application_sessions": {"session_token", "client_id", "ended_at"},
            "application_activity_events": {"session_id", "job_id", "action_type"},
            "ttea_canonical_records": {"batch_id", "job_id", "record_json"},
            "coverage_record_outcomes": {"inventory_id", "disposition"},
            "coverage_scoring_outbox": {"canonical_job_id", "status"},
            "ttea_job_decisions": {"canonical_job_id", "gate_decision", "score", "coverage_run_id", "ttea_run_id"},
        }
        for table, columns in expected.items():
            actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            assert columns <= actual
        assert {row[2] for row in connection.execute("PRAGMA foreign_key_list(ttea_job_decisions)")} == {"jobs", "coverage_runs", "ttea_runs"}
        schema = connection.execute("SELECT sql FROM sqlite_master WHERE name='coverage_scoring_outbox'").fetchone()[0]
        assert "'eliminated'" in schema
        assert connection.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
