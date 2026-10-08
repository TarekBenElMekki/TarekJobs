"""TTEA offline snapshot-to-TN-1 regressions; disposable files/databases only."""
import hashlib
import json
import socket

import pytest

from src.coverage.ttea_tanitjobs_capture import TTEATanitjobsCaptureAdapter, ttea_import_snapshot
from src.coverage.storage import Database, ROOT, migrate_scratch, rows


@pytest.fixture(autouse=True)
def ttea_no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("TTEA staged capture import cannot use a network")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def ttea_artifact(root, kind, value):
    raw = (json.dumps(value, sort_keys=True) + "\n").encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = root / kind / (digest + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path, {"path":str(path.relative_to(root)), "sha256":digest}


def ttea_snapshot(tmp_path, *, detail=True, company="Example"):
    root = tmp_path / ("ttea_stage_" + company)
    record = {"external_id":"123","source_url":"https://www.tanitjobs.com/job/123/engineer/",
              "title":"Engineer","company":company,"location":"Tunis","description":"Build useful systems",
              "posted_date":"2026-10-05","employment":"CDI","work_mode":"","category":"Engineering"}
    evidence = {"source_id":"tanitjobs_tn","mode":"detail" if detail else "listing",
                "captured_at":"2026-10-05T12:00:00Z","records":[record]}
    _, ref = ttea_artifact(root, "ttea_evidence", evidence)
    entry = {"external_id":"123","source_url":record["source_url"],
             "listing_json":json.dumps(record),"listing_evidence":json.dumps(ref),
             "detail_json":json.dumps(record) if detail else None,
             "detail_evidence":json.dumps(ref) if detail else None,
             "detail_status":"ready" if detail else "pending"}
    path, _ = ttea_artifact(root, "ttea_snapshots", {
        "format":"ttea-tanitjobs-stage-v1","source_id":"tanitjobs_tn",
        "coverage":"manual_capture_set_only","site_exhausted":False,"records":[entry]})
    return path


def ttea_query(db, sql):
    with Database(db).read() as connection:
        return rows(connection, sql)


def test_ttea_repeated_snapshot_and_new_snapshot_reuse_canonical_outbox(tmp_path):
    db = tmp_path / "ttea_scratch.db"
    migrate_scratch(db)
    snapshot = ttea_snapshot(tmp_path)
    first = ttea_import_snapshot(db, snapshot, tmp_path / "ttea_artifacts")
    assert first["canonical_new"] == 1
    repeated = ttea_import_snapshot(db, snapshot, tmp_path / "ttea_artifacts")
    assert repeated["canonical_new"] == 0 and repeated["canonical_reused"] == 1
    changed = ttea_import_snapshot(db, ttea_snapshot(tmp_path, company="Example Updated"), tmp_path / "ttea_artifacts")
    assert changed["canonical_new"] == 0 and changed["canonical_reused"] == 1
    assert len(ttea_query(db, "SELECT * FROM jobs")) == 1
    assert len(ttea_query(db, "SELECT * FROM coverage_scoring_outbox")) == 1
    assert ttea_query(db, "SELECT source_id FROM coverage_provenance") == [{"source_id":"tanitjobs_tn"}]


def test_ttea_inventory_without_manual_detail_remains_pending_import(tmp_path):
    db = tmp_path / "ttea_scratch.db"
    migrate_scratch(db)
    result = ttea_import_snapshot(db, ttea_snapshot(tmp_path, detail=False), tmp_path / "ttea_artifacts")
    assert result["status"] == "partial" and result["canonical_new"] == 0
    assert ttea_query(db, "SELECT * FROM coverage_scoring_outbox") == []


def test_ttea_capture_scope_never_claims_site_exhaustion(tmp_path):
    adapter = TTEATanitjobsCaptureAdapter(ttea_snapshot(tmp_path))
    from src.coverage.contracts import Checkpoint, Scope
    with pytest.raises(ValueError):
        adapter.plan(Scope(country="TN"))
    partition = adapter.plan(adapter.ttea_scope)[0]
    page = adapter.inventory(adapter.ttea_scope, partition,
        Checkpoint("ttea-test", partition, None, 0, adapter.metadata.adapter_version))
    assert page.terminal.evidence["site_exhaustion_claimed"] is False


def test_ttea_production_refused_before_snapshot_access(tmp_path):
    with pytest.raises(PermissionError):
        ttea_import_snapshot(ROOT / "data/jobs.db", tmp_path / "absent.json", tmp_path / "artifacts")


def test_ttea_corrupt_evidence_stops_import(tmp_path):
    adapter = TTEATanitjobsCaptureAdapter(ttea_snapshot(tmp_path))
    ref = json.loads(adapter.ttea_rows[0]["detail_evidence"])
    (adapter.ttea_path.parent.parent / ref["path"]).write_text("tampered")
    from src.coverage.contracts import InventoryRecord
    with pytest.raises(ValueError, match="hash mismatch"):
        adapter.detail(InventoryRecord("123","https://www.tanitjobs.com/job/123/engineer/"))
