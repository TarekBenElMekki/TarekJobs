"""One guard and one transaction for a whole pending canonical import."""
from __future__ import annotations

import json

from sqlalchemy import insert

from ..database import JobORM, JobSourceORM
from ..normalize import norm_text
from .artifacts import ArtifactStore, canonical_json, utc_now
from .dedupe import decide
from .normalization import NormalizedCandidate
from .storage import Database, event, one, rows
from .record_outcomes import extra_seen, note_shared_identity, shared_url


def refresh_counters(connection, run_id):
    pages = one(connection, """SELECT COALESCE(SUM(raw_records_seen),0) AS seen,
        COUNT(*) AS pages FROM coverage_inventory_pages WHERE run_id=?""", (run_id,))
    pages["seen"] += extra_seen(connection, run_id)
    counts = one(connection, """SELECT COUNT(*) AS inventory,
        COALESCE(SUM(normalized_json IS NOT NULL),0) AS valid,
        COALESCE(SUM(dedupe_status='create'),0) AS new,
        COALESCE(SUM(dedupe_status='reuse'),0) AS reused,
        COALESCE(SUM(detail_attempts),0) AS attempted,
        COALESCE(SUM(detail_status='ready' AND detail_attempts>0),0) AS completed,
        COALESCE(SUM(detail_status='failed'),0) AS failed,
        COALESCE(SUM(json_extract(normalized_json,'$.job.is_open')=1),0) AS active,
        COALESCE(SUM(json_extract(normalized_json,'$.job.is_open')=0),0) AS expired
        FROM coverage_inventory WHERE run_id=?""", (run_id,))
    expected = one(connection, """SELECT CASE WHEN COUNT(*)=COUNT(pages_expected)
        THEN SUM(pages_expected) END AS expected FROM coverage_checkpoints WHERE run_id=?""", (run_id,))
    connection.exec_driver_sql("""UPDATE coverage_runs SET pages_expected=?,pages_processed=?,
        raw_records_seen=?,raw_records_valid=?,canonical_new=?,canonical_reused=?,duplicates=?,
        detail_pages_attempted=?,detail_pages_completed=?,detail_pages_failed=?,
        active_jobs=?,expired_jobs=? WHERE run_id=?""",
        (expected["expected"],pages["pages"],pages["seen"],counts["valid"],counts["new"],counts["reused"],
         pages["seen"]-counts["inventory"]+counts["reused"],counts["attempted"],counts["completed"],
         counts["failed"],counts["active"],counts["expired"],run_id))


def _new_job(connection, candidate):
    job = candidate.job
    values = job.model_dump(exclude={"source_name","source_url","external_id","raw_payload","benefits"})
    values.update(canonical_url=candidate.canonical_url, norm_title=norm_text(job.title),
                  norm_company=norm_text(job.company), norm_location=norm_text(job.location),
                  benefits_json=canonical_json(job.benefits))
    # Core insert applies the same ORM column defaults without invoking legacy
    # upsert/scoring or modifying existing jobs.
    return connection.execute(insert(JobORM.__table__).values(**values)).inserted_primary_key[0]


def import_pending(database: Database, run_id: str) -> dict:
    """Crash atomic: all canonical jobs, provenance, outbox and counters commit once."""
    with database.write("coverage_bulk_import") as connection:
        run = one(connection,"SELECT * FROM coverage_runs WHERE run_id=?", (run_id,))
        if run["status"] != "running":
            raise ValueError("Canonical import requires a running run")
        store = ArtifactStore(run["artifact_root"])
        imported = 0
        reviewed = 0
        # Fetch bounded windows rather than retaining the whole acquisition.
        last_id = 0
        while True:
            pending = rows(connection, """SELECT * FROM coverage_inventory
                WHERE run_id=? AND inventory_id>? AND detail_status='ready'
                AND dedupe_status='pending' AND normalized_json IS NOT NULL ORDER BY inventory_id LIMIT 100""", (run_id,last_id))
            if not pending:
                break
            for item in pending:
                last_id = item["inventory_id"]
                candidate = NormalizedCandidate.from_payload(json.loads(item["normalized_json"]))
                note_shared_identity(connection, candidate)
                decision = decide(connection, run["source_id"], item["source_record_key"], candidate)
                if decision.action == "review":
                    for evidence in decision.evidence["candidates"]:
                        connection.exec_driver_sql("""INSERT INTO coverage_dedupe_candidates
                            (inventory_id,canonical_job_id,confidence,method,evidence_json,created_at)
                            VALUES (?,?,?,?,?,?) ON CONFLICT(inventory_id,canonical_job_id) DO NOTHING""",
                            (last_id,evidence["job_id"],evidence.get("confidence",decision.confidence),
                             decision.method,canonical_json(evidence),utc_now()))
                    connection.exec_driver_sql(
                        "UPDATE coverage_inventory SET dedupe_status='review' WHERE inventory_id=?", (last_id,))
                    reviewed += 1
                    event(connection,run_id,"dedupe_review")
                    continue
                job_id = decision.candidate_canonical_job_id
                now = utc_now()
                if decision.action == "create":
                    job_id = _new_job(connection,candidate)
                    connection.exec_driver_sql("""INSERT INTO coverage_scoring_outbox
                        (canonical_job_id,run_id,created_at) VALUES (?,?,?)""", (job_id,run_id,now))
                prior = rows(connection,"SELECT canonical_job_id FROM coverage_provenance WHERE source_id=? AND source_record_key=?",
                             (run["source_id"],item["source_record_key"]))
                if prior and prior[0]["canonical_job_id"] != job_id:
                    raise ValueError("Provenance identity cannot be reassigned")
                connection.exec_driver_sql("""INSERT INTO coverage_provenance
                    (canonical_job_id,source_id,source_record_key,external_id,source_url,canonical_url,
                     ats_namespace,ats_requisition_id,posted_date,first_seen,last_seen,
                     duplicate_confidence,dedupe_method,evidence_json,
                     raw_artifact_path,raw_artifact_sha256,detail_artifact_path,detail_artifact_sha256)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source_id,source_record_key) DO UPDATE SET
                     last_seen=excluded.last_seen, duplicate_confidence=excluded.duplicate_confidence,
                     dedupe_method=excluded.dedupe_method,evidence_json=excluded.evidence_json,
                     raw_artifact_path=excluded.raw_artifact_path,raw_artifact_sha256=excluded.raw_artifact_sha256,
                     detail_artifact_path=excluded.detail_artifact_path,detail_artifact_sha256=excluded.detail_artifact_sha256""",
                    (job_id,run["source_id"],item["source_record_key"],item["external_id"],
                     item["source_url"],candidate.canonical_url,candidate.ats_namespace,
                     candidate.ats_requisition_id,
                     candidate.job.posted_at.isoformat() if candidate.job.posted_at else None,
                     now,now,decision.confidence,decision.method,canonical_json({**decision.evidence,
                     "application_url":candidate.application_url,"source_metadata":candidate.source_metadata}),
                     item["raw_artifact_path"],item["raw_artifact_sha256"],
                     item["detail_artifact_path"],item["detail_artifact_sha256"]))
                # Existing UI/source consumers continue seeing source appearances.
                legacy = rows(connection,"SELECT job_id FROM job_sources WHERE source_url=?", (item["source_url"],))
                nonunique_source = not candidate.source_url_is_identity or shared_url(connection, item["source_url"])
                if legacy and legacy[0]["job_id"] != job_id and not nonunique_source:
                    raise ValueError("Conflicting legacy source URL")
                if not legacy and not nonunique_source:
                    connection.execute(insert(JobSourceORM.__table__).values(
                        job_id=job_id,source_name=candidate.job.source_name,
                        source_url=item["source_url"],external_id=item["external_id"]))
                connection.exec_driver_sql("""UPDATE coverage_inventory SET canonical_job_id=?,
                    canonical_url=?,dedupe_status=? WHERE inventory_id=?""",
                    (job_id,candidate.canonical_url,decision.action,last_id))
                imported += 1
        refresh_counters(connection,run_id)
        if imported or reviewed:
            event(connection,run_id,"canonical_batch_imported")
        # A committed import receipt is authoritative only through its database row.
        # A crash before commit may leave an unreferenced, harmless artifact.
        counts = one(connection,"SELECT canonical_new,canonical_reused FROM coverage_runs WHERE run_id=?", (run_id,))
        outbox_count = one(connection,"SELECT COUNT(*) AS n FROM coverage_scoring_outbox WHERE run_id=?", (run_id,))["n"]
        receipt = {"kind":"import","run_id":run_id,"source_id":run["source_id"],
                   "counts":counts,"needs_scoring_count":outbox_count,
                   "needs_scoring_query":{"table":"coverage_scoring_outbox","run_id":run_id},
                   "scorer_invoked":False}
        path,digest = store.write(run["source_id"],run_id,"receipts",receipt)
        connection.exec_driver_sql("""INSERT INTO coverage_receipts
            (run_id,kind,terminal_state,path,sha256,created_at) VALUES (?,'import','committed',?,?,?)
            ON CONFLICT(run_id,kind,sha256) DO NOTHING""", (run_id,path,digest,utc_now()))
        event(connection,run_id,"import_receipt_committed")
        return {**counts,"needs_scoring_count":outbox_count,"receipt_path":path,"receipt_sha256":digest}
