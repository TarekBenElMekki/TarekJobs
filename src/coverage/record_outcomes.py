"""Optional additive outcomes for sources that exclude or expand acquired records."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from .artifacts import canonical_json, utc_now
from .normalization import canonical_url, normalize
from .storage import Database, event, is_production, migrate_scratch, one, rows

MIGRATION = Path(__file__).resolve().parents[2] / "migrations/005_coverage_record_outcomes.sql"


def available(connection):
    return bool(rows(connection, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='coverage_record_outcomes'"))


def require(connection):
    if not available(connection):
        raise ValueError("Native acquisition requires additive migration 005 after 004")


def migrate_native_scratch(path):
    database = Database(path)
    if is_production(database.path):
        raise PermissionError("TN-1 migration is restricted to scratch databases")
    migrate_scratch(path)
    with database.write("coverage_native_scratch_migration") as connection:
        statement = ""
        for line in MIGRATION.read_text(encoding="utf-8").splitlines(True):
            statement += line
            if sqlite3.complete_statement(statement):
                connection.exec_driver_sql(statement)
                statement = ""
        if statement.strip():
            raise ValueError("Incomplete migration SQL")
        event(connection, None, "migration_005")


def unresolved_filter(connection):
    # Preserve old semantics when migration 005 is absent.
    if not available(connection):
        return ""
    return """ AND NOT EXISTS (SELECT 1 FROM coverage_record_outcomes o
               WHERE o.inventory_id=coverage_inventory.inventory_id)"""


def extra_seen(connection, run_id):
    if not available(connection):
        return 0
    derived = one(connection, """SELECT COALESCE(SUM(l.discovered_in_detail),0) AS n
        FROM coverage_record_links l JOIN coverage_inventory i ON i.inventory_id=l.parent_inventory_id
        WHERE i.run_id=?""", (run_id,))["n"]
    overlap = one(connection, """SELECT COALESCE(SUM(records_seen),0) AS n
        FROM coverage_overlap_observations WHERE run_id=?""", (run_id,))["n"]
    return derived + overlap


def shared_url(connection, url):
    if not url or not available(connection):
        return False
    return bool(rows(connection, "SELECT 1 FROM coverage_nonunique_urls WHERE url=?", (canonical_url(url),)))


def note_shared_identity(connection, candidate):
    if candidate.source_url_is_identity:
        return
    require(connection)
    for url in {candidate.job.source_url, candidate.canonical_url}:
        if url:
            connection.exec_driver_sql("""INSERT INTO coverage_nonunique_urls(url,reason,created_at)
                VALUES (?,'multiple_records_share_source_document',?) ON CONFLICT(url) DO NOTHING""",
                (canonical_url(url), utc_now()))


def persist_outcome(connection, store, adapter, run_id, parent, result, detail_path, detail_hash):
    require(connection)
    parent_id = parent["inventory_id"]
    if result.disposition not in {"excluded", "expanded"} or not result.evidence:
        raise ValueError("Explicit disposition requires evidence")
    if result.disposition == "excluded" and result.emissions:
        raise ValueError("Excluded records cannot emit children")
    if result.disposition == "expanded" and not result.emissions:
        raise ValueError("Expanded records require children")
    keys = [emission.record.key for emission in result.emissions]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate emission keys")
    for emission in result.emissions:
        record = emission.record
        if record.key == parent["source_record_key"] or not record.key or not record.source_url:
            raise ValueError("Invalid expansion identity")
        prior = rows(connection, """SELECT * FROM coverage_inventory WHERE run_id=? AND source_record_key=?""",
                     (run_id, record.key))
        created = not prior
        if prior:
            child = prior[0]
            if child["source_url"] != record.source_url or child["external_id"] != record.external_id:
                raise ValueError("Expansion conflicts with persisted source identity")
            child_id = child["inventory_id"]
        else:
            raw_path, raw_hash = store.write(adapter.metadata.source_id, run_id, "inventory", asdict(record))
            candidate = None if emission.requires_detail else normalize(record, record.values, adapter.metadata)
            inserted = connection.exec_driver_sql("""INSERT INTO coverage_inventory
                (run_id,source_id,source_record_key,external_id,source_url,discovered_at,
                 raw_artifact_path,raw_artifact_sha256,detail_status,detail_artifact_path,
                 detail_artifact_sha256,normalized_json,canonical_url)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, adapter.metadata.source_id, record.key, record.external_id, record.source_url,
                 utc_now(), raw_path, raw_hash, "pending" if emission.requires_detail else "ready",
                 None if emission.requires_detail else detail_path,
                 None if emission.requires_detail else detail_hash,
                 canonical_json(candidate.payload()) if candidate else None,
                 candidate.canonical_url if candidate else None))
            child_id = inserted.lastrowid
        # Reject campaign link cycles (A->B->A), including links to an existing row.
        cycle = rows(connection, """WITH RECURSIVE descendants(id) AS (
            SELECT child_inventory_id FROM coverage_record_links WHERE parent_inventory_id=?
            UNION SELECT l.child_inventory_id FROM coverage_record_links l JOIN descendants d ON l.parent_inventory_id=d.id)
            SELECT 1 FROM descendants WHERE id=?""", (child_id, parent_id))
        if cycle or child_id == parent_id:
            raise ValueError("Source record expansion cycle")
        connection.exec_driver_sql("""INSERT INTO coverage_record_links
            (parent_inventory_id,child_inventory_id,discovered_in_detail) VALUES (?,?,?)
            ON CONFLICT(parent_inventory_id,child_inventory_id) DO NOTHING""", (parent_id, child_id, int(created)))
    connection.exec_driver_sql("""INSERT INTO coverage_record_outcomes
        (inventory_id,disposition,evidence_json,created_at) VALUES (?,?,?,?)""",
        (parent_id, result.disposition, canonical_json({k:v for k,v in result.evidence.items() if k != "http"}), utc_now()))
    event(connection, run_id, "detail_" + result.disposition)


def receipt_extension(connection, run_id):
    if not available(connection):
        return {}
    outcomes = rows(connection, """SELECT o.inventory_id,o.disposition,o.evidence_json FROM coverage_record_outcomes o
        JOIN coverage_inventory i USING(inventory_id) WHERE i.run_id=? ORDER BY o.inventory_id""", (run_id,))
    links = rows(connection, """SELECT l.* FROM coverage_record_links l JOIN coverage_inventory i
        ON i.inventory_id=l.parent_inventory_id WHERE i.run_id=? ORDER BY parent_inventory_id,child_inventory_id""", (run_id,))
    overlaps = rows(connection, """SELECT * FROM coverage_overlap_observations
        WHERE run_id=? ORDER BY observation_id""", (run_id,))
    failures = rows(connection, """SELECT e.error_id,e.stage,e.code,e.resolved_at,f.artifact_path,f.artifact_sha256
        FROM coverage_errors e JOIN coverage_failure_evidence f USING(error_id)
        WHERE e.run_id=? ORDER BY e.error_id""", (run_id,))
    return {"record_outcomes": outcomes, "record_links": links, "overlap_observations": overlaps,
            "failure_evidence": failures}
