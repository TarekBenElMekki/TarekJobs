"""TN-0 implementation checks, all databases and artifacts are disposable."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from src.coverage.artifacts import ArtifactStore, canonical_json
from src.coverage.contracts import DetailResult, InventoryPage, Scope
from src.coverage.fixture import FixtureAdapter
from src.coverage.service import CoverageService
from src.coverage.storage import Database, migrate_scratch, rows
from src.coverage import importer, storage


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    import socket
    def forbidden(*args,**kwargs):
        raise AssertionError("TN-0 tests must never access a network")
    monkeypatch.setattr(socket.socket,"connect",forbidden)
    monkeypatch.setattr(socket,"create_connection",forbidden)


@pytest.fixture
def core(tmp_path):
    db = tmp_path/"scratch.db"
    migrate_scratch(db)
    return CoverageService(Database(db),tmp_path/"artifacts")


def fixture(tmp_path, source="board_a", entries=None, **header):
    if entries is None:
        entries = [entry("1"),entry("2",title="Mechanical Designer"),entry("1")]
    path = tmp_path/(source+".jsonl")
    data = [{"format":"coverage-fixture-v1","source":{"source_id":source,"source_name":source},
             "page_size":2,**header},*entries]
    path.write_text("\n".join(canonical_json(row) for row in data)+"\n",encoding="utf-8")
    return FixtureAdapter(path)


def entry(key, title="Software Engineer", company="Example", canonical=None, **extra):
    return {"key":key,"external_id":key,"source_url":"https://board.example/jobs/"+key,
            "values":{"title":title,"company":company,"location":"Tunis",
                      "description":"Build deterministic coverage systems and test resilient imports.",
                      "posted_at":"2026-10-01","canonical_url":canonical or "https://employer.example/jobs/"+key},
            **extra}


def query(core, sql, args=()):
    with core.database.read() as connection:
        return rows(connection,sql,args)


def test_end_to_end_resume_dedup_receipt_and_outbox(core,tmp_path):
    adapter = fixture(tmp_path)
    run_id = core.start(adapter)
    first = core.run(adapter,run_id,max_pages=1)
    assert first["status"] == "partial"
    assert first["pages_processed"] == 1
    assert len(query(core,"SELECT * FROM coverage_inventory")) == 2
    resumed = CoverageService(Database(core.database.path),core.store.root)
    original = adapter.inventory
    seen_cursors = []
    def track(scope,partition,checkpoint):
        seen_cursors.append(checkpoint.cursor)
        return original(scope,partition,checkpoint)
    adapter.inventory = track
    result = resumed.run(adapter,run_id)
    assert seen_cursors == [{"offset":2}]
    assert result["status"] == "exhausted"
    assert (result["raw_records_seen"],result["raw_records_valid"],result["duplicates"]) == (3,2,1)
    assert (result["canonical_new"],result["canonical_reused"]) == (2,0)
    assert len(result["needs_scoring"]) == 2
    assert result["detail_pages_attempted"] == 2
    assert resumed.receipt(run_id)["accounted_for"]
    assert core.run(adapter,run_id) == result
    assert query(core,"PRAGMA foreign_key_check") == []
    next_run = core.start(adapter)
    again = core.run(adapter,next_run)
    assert again["canonical_new"] == 0 and again["canonical_reused"] == 2
    assert len(query(core,"SELECT * FROM jobs")) == 2
    assert len(query(core,"SELECT * FROM coverage_provenance")) == 2
    assert len(query(core,"SELECT * FROM coverage_scoring_outbox")) == 2


def test_cross_source_reuse_preserves_scores_and_review(core,tmp_path):
    adapter = fixture(tmp_path,entries=[entry("1")])
    core.run(adapter,core.start(adapter))
    with core.database.write() as connection:
        connection.exec_driver_sql("UPDATE jobs SET overall_score=91,review_status='shortlisted',notes='keep'")
    other = entry("other",canonical="https://employer.example/jobs/1?utm_source=second")
    other["source_url"] = "https://second.example/jobs/other"
    second = fixture(tmp_path,"board_b",[other])
    result = core.run(second,core.start(second))
    assert result["canonical_reused"] == 1 and result["canonical_new"] == 0
    assert query(core,"SELECT overall_score,review_status,notes FROM jobs") == [
        {"overall_score":91,"review_status":"shortlisted","notes":"keep"}]
    assert len(query(core,"SELECT * FROM coverage_provenance")) == 2
    assert len(query(core,"SELECT * FROM job_sources")) == 2
    assert len(query(core,"SELECT * FROM coverage_scoring_outbox")) == 1


def test_descriptive_similarity_never_auto_merges(core,tmp_path):
    adapter = fixture(tmp_path,entries=[entry("1"),entry("2")])
    result = core.run(adapter,core.start(adapter))
    assert result["status"] == "partial" and not result["exhausted"]
    assert result["canonical_new"] == 1
    assert len(query(core,"SELECT * FROM coverage_dedupe_candidates")) == 1
    assert len(query(core,"SELECT * FROM jobs")) == 1
    assert not core.receipt(result["run_id"])["accounted_for"]


def test_detail_failure_recovery_and_no_rediscovery(core,tmp_path):
    adapter = fixture(tmp_path,entries=[entry("1",detail_error=True)])
    run_id = core.start(adapter)
    result = core.run(adapter,run_id)
    assert result["status"] == "partial" and result["detail_pages_failed"] == 1
    def no_inventory(*args):
        pytest.fail("Persisted inventory must not be rediscovered")
    adapter.inventory = no_inventory
    adapter.detail = lambda record: DetailResult(record.values)
    result = core.run(adapter,run_id,retry_failed=True)
    assert result["status"] == "exhausted" and result["detail_pages_failed"] == 0
    assert result["detail_pages_attempted"] == 2 and result["detail_pages_completed"] == 1
    assert len(query(core,"SELECT * FROM coverage_errors WHERE resolved_at IS NOT NULL")) == 1


def test_adapter_version_incompatibility_is_explicit(core,tmp_path):
    adapter = fixture(tmp_path)
    run_id = core.start(adapter)
    core.run(adapter,run_id,max_pages=1)
    adapter._metadata = replace(adapter.metadata,adapter_version="incompatible")
    with pytest.raises(ValueError,match="version mismatch"):
        core.run(adapter,run_id)
    assert core.inspect(run_id)["pages_processed"] == 1


def test_batch_rollback_and_single_guard(core,tmp_path,monkeypatch):
    adapter = fixture(tmp_path,entries=[entry("1"),entry("2",company="Different")])
    run_id = core.start(adapter)
    original = importer._new_job
    calls = []
    def fail_second(connection,candidate):
        calls.append(candidate)
        if len(calls) == 2:
            raise RuntimeError("injected")
        return original(connection,candidate)
    monkeypatch.setattr(importer,"_new_job",fail_second)
    result = core.run(adapter,run_id)
    assert result["status"] == "partial"
    assert query(core,"SELECT * FROM jobs") == []
    assert query(core,"SELECT * FROM coverage_provenance") == []
    assert query(core,"SELECT * FROM coverage_scoring_outbox") == []
    assert query(core,"SELECT * FROM coverage_receipts WHERE kind='import'") == []
    monkeypatch.setattr(importer,"_new_job",original)
    guarded = []
    real_guard = storage.guard.production_write_guard
    @contextmanager
    def spy(*args,**kwargs):
        guarded.append(kwargs["reason"])
        with real_guard(*args,**kwargs):
            yield
    monkeypatch.setattr(storage.guard,"production_write_guard",spy)
    recovered = core.run(adapter,run_id)
    assert recovered["status"] == "exhausted"
    assert guarded.count("coverage_bulk_import") == 1
    assert recovered["canonical_new"] == 2


def test_production_requires_opt_in_and_migration_always_refuses(core,monkeypatch):
    monkeypatch.setattr(storage,"is_production",lambda path:True)
    def no_guard(*args,**kwargs):
        pytest.fail("Guard must not be entered before opt-in")
    monkeypatch.setattr(storage.guard,"production_write_guard",no_guard)
    with pytest.raises(PermissionError):
        with core.database.write():
            pass
    with pytest.raises(PermissionError):
        migrate_scratch(core.database.path)


@pytest.mark.parametrize("state",["blocked","manual_only","unavailable"])
def test_explicit_terminal_states(core,tmp_path,state):
    adapter = fixture(tmp_path,entries=[],terminal={"state":state,"reason":"fixture_boundary","evidence":{"declared":True}})
    result = core.run(adapter,core.start(adapter))
    assert result["status"] == state and not result["exhausted"]
    assert core.receipt(result["run_id"])["accounted_for"]


def test_inventory_error_is_not_exhaustion(core,tmp_path):
    adapter = fixture(tmp_path)
    original = adapter.inventory
    adapter.inventory = lambda *args: InventoryPage((),None)
    run_id = core.start(adapter)
    result = core.run(adapter,run_id)
    assert result["status"] == "partial" and not result["exhausted"]
    adapter.inventory = original
    assert core.run(adapter,run_id)["status"] == "exhausted"


def test_artifacts_are_atomic_hashed_and_corruption_detected(tmp_path):
    store = ArtifactStore(tmp_path/"artifacts")
    first = store.write("source","run_a","inventory",{"z":1,"a":2})
    assert first == store.write("source","run_a","inventory",{"a":2,"z":1})
    second = store.write("source","run_b","inventory",{"z":1,"a":2})
    assert first[0] != second[0]
    Path(first[0]).write_text("corrupt")
    with pytest.raises(ValueError,match="SHA256"):
        store.read(*first)
    with pytest.raises(ValueError):
        store.write("../escape","run","inventory",{})


def test_additive_idempotent_migration(core):
    before = query(core,"SELECT * FROM jobs")
    migrate_scratch(core.database.path)
    assert query(core,"SELECT * FROM jobs") == before
    assert query(core,"PRAGMA foreign_key_check") == []


def test_cli_dry_run_has_no_side_effects(tmp_path,capsys):
    from src.coverage.__main__ import main
    adapter = fixture(tmp_path)
    path = tmp_path/"absent.db"
    main(["--db",str(path),"start","--fixture",str(adapter.path),
          "--artifacts",str(tmp_path/"absent_artifacts"),"--dry-run"])
    assert json.loads(capsys.readouterr().out)["dry_run"]
    assert not path.exists() and not (tmp_path/"absent_artifacts").exists()


def test_ats_tenant_identity_and_conflicting_identifiers(core,tmp_path):
    first = entry("1")
    first["values"].update(ats_namespace="employer:example",ats_requisition_id="REQ-9")
    second = entry("2",company="Different Employer")
    adapter = fixture(tmp_path,entries=[first,second])
    core.run(adapter,core.start(adapter))
    cross = entry("third",canonical="https://third.example/req")
    cross["source_url"] = "https://third.example/listing"
    cross["values"].update(ats_namespace="employer:example",ats_requisition_id="REQ-9")
    other = fixture(tmp_path,"board_b",[cross])
    result = core.run(other,core.start(other))
    assert result["canonical_reused"] == 1
    conflicting = entry("fourth",canonical="https://employer.example/jobs/2")
    conflicting["source_url"] = "https://fourth.example/job"
    conflicting["values"].update(ats_namespace="employer:example",ats_requisition_id="REQ-9")
    other = fixture(tmp_path,"board_c",[conflicting])
    result = core.run(other,core.start(other))
    assert result["status"] == "partial" and result["canonical_reused"] == 0
    assert len(query(core,"SELECT * FROM coverage_dedupe_candidates")) == 2
    assert len(query(core,"SELECT * FROM jobs")) == 2


def test_unscoped_requisition_ids_never_merge(core,tmp_path):
    a = entry("1")
    b = entry("2",company="Another Company")
    for row in (a,b):
        row["values"]["ats_requisition_id"] = "123"
    adapter = fixture(tmp_path,entries=[a,b])
    result = core.run(adapter,core.start(adapter))
    assert result["canonical_new"] == 2


def test_partition_checkpoints_and_global_scope(core,tmp_path):
    adapter = fixture(tmp_path,entries=[
        entry("1",partition="north"),entry("2",company="Other",partition="south")],
        partitions=["north","south"])
    run_id = core.start(adapter,Scope("*","*"))
    result = core.run(adapter,run_id,max_pages=1)
    assert result["status"] == "partial"
    assert result["checkpoints"][0]["completed"] == 1
    result = core.run(adapter,run_id)
    assert result["status"] == "exhausted" and result["pages_processed"] == 2
    assert result["pages_expected"] == 2
    assert len(query(core,"SELECT * FROM coverage_scopes WHERE scope_work_mode='*'")) == 2


def test_long_cursor_cycle_is_rejected(core,tmp_path):
    adapter = fixture(tmp_path)
    def cycle(scope,partition,checkpoint):
        cursor = (checkpoint.cursor or {}).get("token")
        next_token = "b" if cursor == "a" else "a"
        return InventoryPage((),{"token":next_token})
    adapter.inventory = cycle
    result = core.run(adapter,core.start(adapter))
    assert result["status"] == "partial" and result["pages_processed"] == 2
    assert not result["exhausted"]


def test_crash_between_detail_attempt_and_persistence_resumes(core,tmp_path):
    adapter = fixture(tmp_path,entries=[entry("1")])
    run_id = core.start(adapter)
    original = adapter.detail
    def crash(record):
        raise KeyboardInterrupt()
    adapter.detail = crash
    with pytest.raises(KeyboardInterrupt):
        core.run(adapter,run_id)
    assert core.inspect(run_id)["status"] == "running"
    adapter.detail = original
    result = core.run(adapter,run_id)
    assert result["status"] == "exhausted"
    assert result["detail_pages_attempted"] == 2
    assert len(query(core,"SELECT * FROM jobs")) == 1


def test_terminal_receipt_manifest_is_self_contained(core,tmp_path):
    adapter = fixture(tmp_path)
    run_id = core.start(adapter)
    core.run(adapter,run_id)
    receipt = core.receipt(run_id)
    manifests = receipt["record_manifests"]
    assert sum(m["records"] for m in manifests) == 2
    for manifest in manifests:
        data = core.store.read(manifest["path"],manifest["sha256"])
        for row in data["records"]:
            assert core.store.read(row["raw_artifact_path"],row["raw_artifact_sha256"])
            assert core.store.read(row["detail_artifact_path"],row["detail_artifact_sha256"])
            assert row["canonical_job_id"]


def test_concurrent_run_worker_is_rejected_and_lock_released(core,tmp_path):
    from src.coverage.artifacts import run_lock
    adapter = fixture(tmp_path)
    run_id = core.start(adapter)
    with run_lock(core.database.path,run_id):
        with pytest.raises(RuntimeError,match="active worker"):
            core.run(adapter,run_id)
    assert core.run(adapter,run_id)["status"] == "exhausted"


def test_impossible_run_state_constraint(core,tmp_path):
    from sqlalchemy.exc import IntegrityError
    adapter = fixture(tmp_path)
    run_id = core.start(adapter)
    with pytest.raises(IntegrityError):
        with core.database.write() as connection:
            connection.exec_driver_sql("UPDATE coverage_runs SET status='exhausted' WHERE run_id=?", (run_id,))
    assert core.inspect(run_id)["status"] == "planned"


def test_production_bulk_service_calls_verified_backup_once(core,tmp_path,monkeypatch):
    adapter = fixture(tmp_path,entries=[entry("1"),entry("2",company="Other")])
    run_id = core.start(adapter)
    original_import = importer.import_pending
    import src.coverage.service as service_module
    # Acquire/normalize in the scratch DB, leaving canonical work pending.
    monkeypatch.setattr(service_module,"import_pending",lambda *args: None)
    core.run(adapter,run_id)
    monkeypatch.setattr(service_module,"import_pending",original_import)
    with core.database.write() as connection:
        connection.exec_driver_sql("UPDATE coverage_runs SET status='running',completed_at=NULL WHERE run_id=?", (run_id,))
    backups = []
    monkeypatch.setattr(storage,"is_production",lambda p:True)
    monkeypatch.setattr(storage.guard,"_verified_backup",lambda path,reason: backups.append((path,reason)))
    monkeypatch.setattr(storage.guard,"_log",lambda *args:None)
    result = importer.import_pending(Database(core.database.path,allow_production=True),run_id)
    assert result["canonical_new"] == 2
    assert backups == [(core.database.path,"coverage_bulk_import")]


def test_backup_failure_prevents_entire_import(core,tmp_path,monkeypatch):
    adapter = fixture(tmp_path,entries=[entry("1")])
    run_id = core.start(adapter)
    monkeypatch.setattr(storage,"is_production",lambda p:True)
    def failed(*args):
        raise OSError("injected")
    monkeypatch.setattr(storage.guard,"_verified_backup",failed)
    monkeypatch.setattr(storage.guard,"_log",lambda *args:None)
    with pytest.raises(storage.guard.ProductionWriteSafetyError):
        importer.import_pending(Database(core.database.path,allow_production=True),run_id)
    assert query(core,"SELECT * FROM jobs") == []


def test_completed_details_not_repeated_on_resume(core,tmp_path):
    adapter = fixture(tmp_path)
    run_id = core.start(adapter)
    result = core.run(adapter,run_id,max_details=1)
    assert result["status"] == "partial"
    attempts = []
    original = adapter.detail
    def track(record):
        attempts.append(record.key)
        return original(record)
    adapter.detail = track
    result = core.run(adapter,run_id)
    assert result["status"] == "exhausted" and attempts == ["2"]
    assert result["canonical_new"] == 2
