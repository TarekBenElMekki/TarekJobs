"""Persistent page/detail checkpoints and terminal evidence orchestration."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from .artifacts import ArtifactStore, canonical_json, identifier, stable_id, utc_now, run_lock
from .contracts import AcquisitionFailure, Checkpoint, InventoryRecord, Partition, Scope, SourceAdapter
from .record_outcomes import (available as outcomes_available, require as require_outcomes,
    persist_outcome, unresolved_filter, receipt_extension)
from .importer import import_pending, refresh_counters
from .normalization import normalize
from .storage import Database, event, one, rows, is_production


def register_source(connection, adapter: SourceAdapter):
    metadata = asdict(adapter.metadata)
    identifier(metadata["source_id"])
    now = utc_now()
    existing = rows(connection,"SELECT * FROM coverage_sources WHERE source_id=?", (metadata["source_id"],))
    if existing:
        previous = existing[0]
        if any(previous[key] != metadata[key] for key in
               ("adapter_key","source_name","source_type","acquisition_method","base_url")):
            raise ValueError("Stable source ID already belongs to another source")
    connection.exec_driver_sql("""INSERT INTO coverage_sources
        (source_id,source_name,source_type,acquisition_method,adapter_key,adapter_version,base_url,
         capabilities_json,limits_json,canonical_hints_json,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_id) DO UPDATE SET adapter_version=excluded.adapter_version,
         capabilities_json=excluded.capabilities_json, limits_json=excluded.limits_json,
         canonical_hints_json=excluded.canonical_hints_json,updated_at=excluded.updated_at""",
        (metadata["source_id"],metadata["source_name"],metadata["source_type"],
         metadata["acquisition_method"],metadata["adapter_key"],metadata["adapter_version"],metadata["base_url"],
         canonical_json(metadata["capabilities"]),canonical_json(metadata["limits"]),
         canonical_json(metadata["canonical_hints"]),now,now))
    event(connection,None,"source_registered")
    return metadata["source_id"]


class CoverageService:
    def __init__(self, database: Database, artifact_root: str | Path):
        self.database = database
        self.store = ArtifactStore(artifact_root)

    def register(self, adapter):
        with self.database.write("coverage_register_source") as connection:
            return register_source(connection,adapter)

    def start(self, adapter: SourceAdapter, scope: Scope = Scope(), *,
              scan_kind="full", run_id: str | None = None) -> str:
        if scan_kind not in {"full","delta"}:
            raise ValueError("Invalid scan kind")
        run_id = identifier(run_id or uuid4().hex)
        partitions = tuple(adapter.plan(scope))
        if not partitions or len({p.key for p in partitions}) != len(partitions):
            raise ValueError("Plan must have unique, nonempty partitions")
        with self.database.write("coverage_start_run") as connection:
            source_id = register_source(connection,adapter)
            source = one(connection,"SELECT * FROM coverage_sources WHERE source_id=?", (source_id,))
            if source["status"] != "enabled":
                raise ValueError("Source is disabled")
            now = utc_now()
            connection.exec_driver_sql("""INSERT INTO coverage_runs
                (run_id,source_id,scope_json,adapter_version,adapter_metadata_json,scan_kind,artifact_root,status,started_at)
                VALUES (?,?,?,?,?,?,?,'planned',?)""",
                (run_id,source_id,canonical_json(asdict(scope)),adapter.metadata.adapter_version,
                 canonical_json(asdict(adapter.metadata)),scan_kind,str(self.store.root),now))
            for partition in partitions:
                if not partition.key:
                    raise ValueError("Empty partition key")
                coverage_id = stable_id([source_id,asdict(scope),partition.key])
                existing = rows(connection,"SELECT * FROM coverage_scopes WHERE coverage_id=?", (coverage_id,))
                if existing and existing[0]["partition_metadata_json"] != canonical_json(partition.metadata):
                    raise ValueError("Partition semantics changed; use a new partition key")
                active = rows(connection,"""SELECT r.run_id FROM coverage_checkpoints c JOIN coverage_runs r USING(run_id)
                    WHERE c.coverage_id=? AND r.status IN ('planned','running','partial','failed')""", (coverage_id,))
                if active:
                    raise ValueError("Coverage already has a resumable run")
                connection.exec_driver_sql("""INSERT INTO coverage_scopes
                    (coverage_id,source_id,scope_country,scope_work_mode,scope_region,partition_key,
                     partition_metadata_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(coverage_id) DO UPDATE SET status='planned',updated_at=excluded.updated_at""",
                    (coverage_id,source_id,scope.country,scope.work_mode,scope.region,partition.key,
                     canonical_json(partition.metadata),now,now))
                connection.exec_driver_sql("""INSERT INTO coverage_checkpoints
                    (run_id,partition_key,coverage_id,partition_metadata_json,adapter_version,updated_at)
                    VALUES (?,?,?,?,?,?)""",
                    (run_id,partition.key,coverage_id,canonical_json(partition.metadata),adapter.metadata.adapter_version,now))
            history = one(connection,"""SELECT MIN(last_full_scan) AS full,MIN(last_delta_scan) AS delta
                FROM coverage_scopes WHERE coverage_id IN
                (SELECT coverage_id FROM coverage_checkpoints WHERE run_id=?)""", (run_id,))
            connection.exec_driver_sql("UPDATE coverage_runs SET last_full_scan=?,last_delta_scan=? WHERE run_id=?",
                                      (history["full"],history["delta"],run_id))
            event(connection,run_id,"run_planned")
        return run_id

    def inspect(self, run_id):
        with self.database.read() as connection:
            run = one(connection,"SELECT * FROM coverage_runs WHERE run_id=?", (run_id,))
            run["checkpoints"] = rows(connection,"SELECT * FROM coverage_checkpoints WHERE run_id=? ORDER BY partition_key", (run_id,))
            run["needs_scoring"] = rows(connection,"SELECT canonical_job_id,status FROM coverage_scoring_outbox WHERE run_id=? ORDER BY canonical_job_id", (run_id,))
            return run

    def receipt(self, run_id):
        run = self.inspect(run_id)
        if not run["receipt_path"]:
            raise ValueError("Run does not have a terminal receipt")
        return ArtifactStore(run["artifact_root"]).read(run["receipt_path"],run["receipt_sha256"])

    def _error(self, run_id, stage, key, code, evidence=None):
        with self.database.write("coverage_record_error") as connection:
            inserted = connection.exec_driver_sql("""INSERT INTO coverage_errors
                (run_id,stage,item_key,code,occurred_at) VALUES (?,?,?,?,?)""",
                (run_id,stage,key,code,utc_now()))
            if evidence and outcomes_available(connection):
                run = one(connection,"SELECT source_id FROM coverage_runs WHERE run_id=?", (run_id,))
                path,digest = self.store.write(run["source_id"],run_id,"details",
                    {"stage":stage,"code":code,"evidence":evidence})
                connection.exec_driver_sql("""INSERT INTO coverage_failure_evidence
                    (error_id,artifact_path,artifact_sha256) VALUES (?,?,?)""", (inserted.lastrowid,path,digest))
            event(connection,run_id,"acquisition_error")

    def _page(self, adapter, scope, run_id, checkpoint):
        partition = Partition(checkpoint["partition_key"],json.loads(checkpoint["partition_metadata_json"]))
        cursor = json.loads(checkpoint["cursor_json"]) if checkpoint["cursor_json"] else None
        typed = Checkpoint(run_id,partition,cursor,checkpoint["pages_processed"],checkpoint["adapter_version"])
        page = adapter.inventory(scope,partition,typed)
        if page.error_code:
            raise AcquisitionFailure(page.error_code, page.evidence)
        if page.terminal and page.next_cursor is not None:
            raise ValueError("Terminal page cannot have a continuation cursor")
        if not page.terminal and (page.next_cursor is None or page.next_cursor == cursor):
            raise ValueError("Inventory failed to advance")
        if page.pages_expected is not None and (type(page.pages_expected) is not int or page.pages_expected < typed.pages_processed+1):
            raise ValueError("Invalid expected page count")
        for record in page.records:
            if not record.key or not record.source_url:
                raise ValueError("Inventory identity is required")
        payload = {"partition":asdict(partition),"cursor":cursor,"page":asdict(page)}
        path,digest = self.store.write(adapter.metadata.source_id,run_id,"inventory",payload)
        with self.database.write("coverage_inventory_page") as connection:
            current = one(connection,"SELECT * FROM coverage_checkpoints WHERE run_id=? AND partition_key=?", (run_id,partition.key))
            if current["pages_processed"] != checkpoint["pages_processed"] or current["completed"]:
                return  # Another worker already persisted this page.
            if page.content_fingerprint:
                require_outcomes(connection)
                if rows(connection, """SELECT 1 FROM coverage_page_fingerprints
                    WHERE run_id=? AND partition_key=? AND fingerprint=?""",
                    (run_id,partition.key,page.content_fingerprint)):
                    raise AcquisitionFailure("repeated_inventory_content",
                        {"artifact_path":path,"artifact_sha256":digest})
            # Reject longer cursor cycles, not just immediate non-advancement.
            if page.next_cursor is not None and rows(connection,
                "SELECT 1 FROM coverage_inventory_pages WHERE run_id=? AND partition_key=? AND cursor_json=?",
                (run_id,partition.key,canonical_json(page.next_cursor))):
                raise ValueError("Pagination cursor cycle")
            for record in page.records:
                raw_path,raw_hash = self.store.write(adapter.metadata.source_id,run_id,"inventory",asdict(record))
                prior = rows(connection,"SELECT * FROM coverage_inventory WHERE run_id=? AND source_record_key=?", (run_id,record.key))
                if prior:
                    if prior[0]["source_url"] != record.source_url or prior[0]["external_id"] != record.external_id:
                        raise ValueError("Conflicting inventory identity")
                    continue
                connection.exec_driver_sql("""INSERT INTO coverage_inventory
                    (run_id,source_id,source_record_key,external_id,source_url,discovered_at,raw_artifact_path,raw_artifact_sha256)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (run_id,adapter.metadata.source_id,record.key,record.external_id,record.source_url,utc_now(),raw_path,raw_hash))
            connection.exec_driver_sql("""INSERT INTO coverage_inventory_pages
                (run_id,partition_key,page_number,cursor_json,raw_records_seen,artifact_path,artifact_sha256)
                VALUES (?,?,?,?,?,?,?)""",
                (run_id,partition.key,typed.pages_processed+1,canonical_json(cursor),len(page.records),path,digest))
            if page.content_fingerprint:
                connection.exec_driver_sql("""INSERT INTO coverage_page_fingerprints
                    (run_id,partition_key,fingerprint,page_number) VALUES (?,?,?,?)""",
                    (run_id,partition.key,page.content_fingerprint,typed.pages_processed+1))
            if page.terminal and page.terminal.state == "exhausted":
                expected = page.terminal.evidence.get("expected_unique_total")
                actual = one(connection,"SELECT COUNT(*) AS n FROM coverage_inventory WHERE run_id=?", (run_id,))["n"]
                if expected is not None and actual < expected:
                    raise AcquisitionFailure("unique_inventory_count_below_reported_total",
                        {"expected":expected,"actual":actual,"artifact_path":path,"artifact_sha256":digest})
            terminal = canonical_json(asdict(page.terminal)) if page.terminal else None
            connection.exec_driver_sql("""UPDATE coverage_checkpoints SET cursor_json=?,pages_processed=pages_processed+1,
                pages_expected=COALESCE(?,pages_expected),completed=?,terminal_json=?,updated_at=?
                WHERE run_id=? AND partition_key=?""",
                (canonical_json(page.next_cursor),page.pages_expected,int(page.terminal is not None),terminal,utc_now(),run_id,partition.key))
            connection.exec_driver_sql("UPDATE coverage_runs SET last_cursor=? WHERE run_id=?",
                                      (canonical_json({"partition":partition.key,"cursor":page.next_cursor}),run_id))
            connection.exec_driver_sql("UPDATE coverage_errors SET resolved_at=? WHERE run_id=? AND stage='inventory' AND item_key=? AND resolved_at IS NULL",
                                      (utc_now(),run_id,partition.key))
            refresh_counters(connection,run_id)
            event(connection,run_id,"inventory_page_checkpointed")

    def _detail(self, adapter, run_id, item):
        raw = self.store.read(item["raw_artifact_path"],item["raw_artifact_sha256"])
        record = InventoryRecord(**raw)
        supports_detail = adapter.metadata.capabilities.detail_pages
        with self.database.write("coverage_detail_attempt") as connection:
            current = one(connection,"SELECT * FROM coverage_inventory WHERE inventory_id=?", (item["inventory_id"],))
            if current["detail_status"] == "ready":
                return
            if supports_detail:
                connection.exec_driver_sql("UPDATE coverage_inventory SET detail_attempts=detail_attempts+1 WHERE inventory_id=?", (item["inventory_id"],))
            event(connection,run_id,"detail_attempt_started" if supports_detail else "normalization_started")
        values = record.values
        detail_path = detail_hash = None
        result = None
        disposition = "record"
        try:
            if supports_detail:
                result = adapter.detail(record)
                detail_path,detail_hash = self.store.write(adapter.metadata.source_id,run_id,"details",asdict(result))
                if result.error_code:
                    raise AcquisitionFailure(result.error_code, result.evidence)
                disposition = result.disposition
                values = {**values,**result.values}
            candidate = normalize(record,values,adapter.metadata) if disposition == "record" else None
            error_code = None
        except Exception as exc:
            candidate = None
            disposition = "record"
            error_code = exc.code if isinstance(exc, AcquisitionFailure) else "detail_or_normalization_failed"
        with self.database.write("coverage_detail_result") as connection:
            current = one(connection,"SELECT detail_status FROM coverage_inventory WHERE inventory_id=?", (item["inventory_id"],))
            if current["detail_status"] == "ready":
                return
            if not error_code and disposition in {"excluded", "expanded"}:
                persist_outcome(connection,self.store,adapter,run_id,item,result,detail_path,detail_hash)
            connection.exec_driver_sql("""UPDATE coverage_inventory SET detail_status=?,detail_artifact_path=?,
                detail_artifact_sha256=?,normalized_json=?,canonical_url=?,error_json=? WHERE inventory_id=?""",
                ("ready" if not error_code else "failed",detail_path,detail_hash,
                 canonical_json(candidate.payload()) if candidate else None,
                 candidate.canonical_url if candidate else None,
                 canonical_json({"code":error_code}) if error_code else "{}",item["inventory_id"]))
            if error_code:
                inserted = connection.exec_driver_sql("""INSERT INTO coverage_errors
                    (run_id,stage,item_key,code,occurred_at) VALUES (?,'detail',?,?,?)""",
                    (run_id,str(item["inventory_id"]),error_code,utc_now()))
                if detail_path and outcomes_available(connection):
                    connection.exec_driver_sql("""INSERT INTO coverage_failure_evidence
                        (error_id,artifact_path,artifact_sha256) VALUES (?,?,?)""",
                        (inserted.lastrowid,detail_path,detail_hash))
            else:
                connection.exec_driver_sql("UPDATE coverage_errors SET resolved_at=? WHERE run_id=? AND stage='detail' AND item_key=? AND resolved_at IS NULL",
                                          (utc_now(),run_id,str(item["inventory_id"])))
            refresh_counters(connection,run_id)
            event(connection,run_id,"detail_persisted" if not error_code else "detail_failed")
        # Safe-stop the invocation after an access restriction. Already successful
        # detail rows remain durable; retry still requires explicit caller action.
        return bool(result and result.error_code and result.evidence.get("terminal_classification"))

    def _resume_overlap(self, adapter, run_id, checkpoint):
        hook = getattr(adapter, "resume_overlap", None)
        if hook is None or checkpoint["completed"] or not checkpoint["pages_processed"]:
            return
        partition = Partition(checkpoint["partition_key"], json.loads(checkpoint["partition_metadata_json"]))
        typed = Checkpoint(run_id,partition,
            json.loads(checkpoint["cursor_json"]) if checkpoint["cursor_json"] else None,
            checkpoint["pages_processed"],checkpoint["adapter_version"],False)
        for page in hook(typed):
            if page.error_code or page.terminal:
                raise AcquisitionFailure(page.error_code or "overlap_access_stop", page.evidence)
            path,digest = self.store.write(adapter.metadata.source_id,run_id,"inventory",
                {"kind":"resume_overlap","page":asdict(page)})
            with self.database.write("coverage_resume_overlap") as connection:
                require_outcomes(connection)
                added = 0
                for record in page.records:
                    prior = rows(connection,"SELECT * FROM coverage_inventory WHERE run_id=? AND source_record_key=?", (run_id,record.key))
                    if prior:
                        if prior[0]["source_url"] != record.source_url or prior[0]["external_id"] != record.external_id:
                            raise AcquisitionFailure("overlap_identity_conflict", {"artifact_path":path,"artifact_sha256":digest})
                        continue
                    raw_path,raw_hash = self.store.write(adapter.metadata.source_id,run_id,"inventory",asdict(record))
                    connection.exec_driver_sql("""INSERT INTO coverage_inventory
                        (run_id,source_id,source_record_key,external_id,source_url,discovered_at,
                         raw_artifact_path,raw_artifact_sha256) VALUES (?,?,?,?,?,?,?,?)""",
                        (run_id,adapter.metadata.source_id,record.key,record.external_id,record.source_url,
                         utc_now(),raw_path,raw_hash))
                    added += 1
                connection.exec_driver_sql("""INSERT INTO coverage_overlap_observations
                    (run_id,partition_key,observed_at,page_number,records_seen,records_new,artifact_path,artifact_sha256)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (run_id,partition.key,utc_now(),page.evidence["page"],len(page.records),added,path,digest))
                connection.exec_driver_sql("""UPDATE coverage_errors SET resolved_at=?
                    WHERE run_id=? AND stage='overlap' AND item_key=? AND resolved_at IS NULL""",
                    (utc_now(),run_id,partition.key))
                refresh_counters(connection,run_id)
                event(connection,run_id,"resume_overlap_checkpointed")

    def run(self, adapter: SourceAdapter, run_id: str, **options):
        if is_production(self.database.path) and not self.database.allow_production:
            raise PermissionError("Production mutation requires explicit opt-in")
        # OS locks are released on process death, so crashes do not leave stale
        # leases. Lock identity includes the DB and run, independent of artifact root.
        with run_lock(self.database.path, run_id):
            return self._run(adapter,run_id,**options)

    def _run(self, adapter: SourceAdapter, run_id: str, *, max_pages: int | None = None,
            max_details: int | None = None, retry_failed: bool = False):
        for limit in (max_pages,max_details):
            if limit is not None and (type(limit) is not int or limit < 0):
                raise ValueError("Limits must be nonnegative integers")
        before = self.inspect(run_id)
        if before["adapter_metadata_json"] != canonical_json(asdict(adapter.metadata)):
            raise ValueError("Adapter identity/version mismatch; checkpoint cannot resume")
        if any(c["adapter_version"] != adapter.metadata.adapter_version for c in before["checkpoints"]):
            raise ValueError("Checkpoint adapter version mismatch")
        if self.store.root != Path(before["artifact_root"]).resolve():
            raise ValueError("Artifact root differs from pinned run configuration")
        if before["status"] in {"exhausted","blocked","manual_only","unavailable","completed"}:
            self.receipt(run_id)  # Verify published evidence even for idempotent resume.
            return before
        scope = Scope(**json.loads(before["scope_json"]))
        with self.database.write("coverage_resume_run") as connection:
            source = one(connection,"SELECT status FROM coverage_sources WHERE source_id=?", (before["source_id"],))
            if source["status"] != "enabled":
                raise ValueError("Source is disabled")
            connection.exec_driver_sql("""UPDATE coverage_runs SET status='running',completed_at=NULL,
                exhausted=0,exhaustion_reason=NULL,receipt_path=NULL,receipt_sha256=NULL WHERE run_id=?""", (run_id,))
            connection.exec_driver_sql("""UPDATE coverage_scopes SET status='running',updated_at=?
                WHERE coverage_id IN (SELECT coverage_id FROM coverage_checkpoints WHERE run_id=?)""", (utc_now(),run_id))
            event(connection,run_id,"run_resumed")
        pages_done = 0
        for checkpoint in before["checkpoints"]:
            if max_pages != 0:
                try:
                    self._resume_overlap(adapter,run_id,checkpoint)
                except Exception as exc:
                    self._error(run_id,"overlap",checkpoint["partition_key"],
                        getattr(exc,"code","overlap_failed"),getattr(exc,"evidence",None))
                    return self._finalize(run_id)
            while not checkpoint["completed"]:
                if max_pages is not None and pages_done >= max_pages:
                    return self._finalize(run_id)
                try:
                    self._page(adapter,scope,run_id,checkpoint)
                except Exception as exc:
                    self._error(run_id,"inventory",checkpoint["partition_key"],
                        getattr(exc,"code","inventory_failed"),getattr(exc,"evidence",None))
                    return self._finalize(run_id)
                pages_done += 1
                with self.database.read() as connection:
                    checkpoint = one(connection,"SELECT * FROM coverage_checkpoints WHERE run_id=? AND partition_key=?", (run_id,checkpoint["partition_key"]))
        with self.database.read() as connection:
            boundaries = rows(connection,"SELECT terminal_json FROM coverage_checkpoints WHERE run_id=?", (run_id,))
        if any(row["terminal_json"] and json.loads(row["terminal_json"])["state"] != "exhausted" for row in boundaries):
            return self._finalize(run_id)
        last_id = 0
        details_done = 0
        while max_details is None or details_done < max_details:
            with self.database.read() as connection:
                items = rows(connection,"""SELECT * FROM coverage_inventory WHERE run_id=? AND inventory_id>?
                    AND (detail_status='pending' OR (? AND detail_status='failed'))
                    ORDER BY inventory_id LIMIT 1""", (run_id,last_id,int(retry_failed)))
            if not items:
                break
            item = items[0]
            last_id = item["inventory_id"]
            try:
                access_stop = self._detail(adapter,run_id,item)
                if access_stop:
                    break
            except Exception:
                self._error(run_id,"detail",str(last_id),"artifact_or_detail_failed")
            details_done += 1
        try:
            import_pending(self.database,run_id)
            with self.database.write("coverage_import_recovered") as connection:
                connection.exec_driver_sql("UPDATE coverage_errors SET resolved_at=? WHERE run_id=? AND stage='import' AND resolved_at IS NULL", (utc_now(),run_id))
        except Exception:
            self._error(run_id,"import","batch","canonical_import_failed")
        return self._finalize(run_id)

    def _finalize(self, run_id):
        with self.database.write("coverage_finalize") as connection:
            run = one(connection,"SELECT * FROM coverage_runs WHERE run_id=?", (run_id,))
            checkpoints = rows(connection,"SELECT * FROM coverage_checkpoints WHERE run_id=? ORDER BY partition_key", (run_id,))
            errors = rows(connection,"SELECT stage,item_key,code FROM coverage_errors WHERE run_id=? AND resolved_at IS NULL ORDER BY error_id", (run_id,))
            unresolved = one(connection,"""SELECT COUNT(*) AS n FROM coverage_inventory WHERE run_id=?
                AND (detail_status<>'ready' OR dedupe_status IN ('pending','review'))""" +
                unresolved_filter(connection), (run_id,))["n"]
            complete = all(c["completed"] for c in checkpoints)
            terminals = [json.loads(c["terminal_json"]) for c in checkpoints if c["terminal_json"]]
            access_states = {t["state"] for t in terminals} & {"blocked", "manual_only", "unavailable"}
            if complete and access_states:
                # Preserve a terminal access stop even when earlier pages left
                # pending details. This run stays non-exhausted; a later explicit
                # fresh run can re-check legitimate access without a stuck scope.
                status = next(s for s in ("blocked", "unavailable", "manual_only") if s in access_states)
                reason = ";".join(sorted({t["reason"] for t in terminals if t["state"] == status}))
            elif errors or unresolved or not complete:
                status = "partial"
                reason = "unresolved_errors_or_records" if errors or unresolved else "inventory_incomplete"
            else:
                states = {t["state"] for t in terminals}
                status = next(s for s in ("blocked","unavailable","manual_only","exhausted") if s in states)
                reason = ";".join(sorted({t["reason"] for t in terminals}))
            now = utc_now()
            refresh_counters(connection,run_id)
            connection.exec_driver_sql("""UPDATE coverage_runs SET status=?,completed_at=?,exhausted=?,
                exhaustion_reason=?,errors_json=? WHERE run_id=?""",
                (status,now,int(status=="exhausted"),reason,canonical_json(errors),run_id))
            if status == "exhausted":
                column = "last_full_scan" if run["scan_kind"] == "full" else "last_delta_scan"
                connection.exec_driver_sql(f"UPDATE coverage_runs SET {column}=? WHERE run_id=?", (now,run_id))
            run = one(connection,"SELECT * FROM coverage_runs WHERE run_id=?", (run_id,))
            artifacts = rows(connection,"SELECT partition_key,page_number,artifact_path,artifact_sha256 FROM coverage_inventory_pages WHERE run_id=? ORDER BY partition_key,page_number", (run_id,))
            receipt = {"format_version":1,"run":{k:v for k,v in run.items() if k not in {"receipt_path","receipt_sha256"}},
                       "checkpoints":checkpoints,"inventory_artifacts":artifacts,
                       "unresolved_records":unresolved,"terminal_evidence":terminals,
                       "accounted_for":complete and not errors and not unresolved,
                       "detail_artifacts_query":{"table":"coverage_inventory","run_id":run_id},
                       "needs_scoring_query":{"table":"coverage_scoring_outbox","run_id":run_id}}
            receipt.update(receipt_extension(connection,run_id))
            for group in ("overlap_observations","failure_evidence"):
                for artifact in receipt.get(group,[]):
                    self.store.read(artifact["artifact_path"],artifact["artifact_sha256"])
            # Hash verification before terminal publication detects damaged evidence.
            for artifact in artifacts:
                self.store.read(artifact["artifact_path"],artifact["artifact_sha256"])
            for item in connection.exec_driver_sql(
                "SELECT raw_artifact_path,raw_artifact_sha256,detail_artifact_path,detail_artifact_sha256 FROM coverage_inventory WHERE run_id=?", (run_id,)):
                self.store.read(item[0],item[1])
                if item[2]:
                    self.store.read(item[2],item[3])
            manifests = []
            last_id = 0
            while True:
                batch = rows(connection, """SELECT inventory_id,source_record_key,external_id,
                    source_url,canonical_url,raw_artifact_path,raw_artifact_sha256,
                    detail_artifact_path,detail_artifact_sha256,detail_status,detail_attempts,
                    canonical_job_id,dedupe_status,error_json FROM coverage_inventory
                    WHERE run_id=? AND inventory_id>? ORDER BY inventory_id LIMIT 100""", (run_id,last_id))
                if not batch:
                    break
                last_id = batch[-1]["inventory_id"]
                manifest_path,manifest_hash = self.store.write(run["source_id"],run_id,"receipts",
                    {"kind":"inventory_manifest","run_id":run_id,"records":batch})
                manifests.append({"path":manifest_path,"sha256":manifest_hash,"records":len(batch)})
            receipt["record_manifests"] = manifests
            path,digest = self.store.write(run["source_id"],run_id,"receipts",receipt)
            connection.exec_driver_sql("UPDATE coverage_runs SET receipt_path=?,receipt_sha256=? WHERE run_id=?", (path,digest,run_id))
            connection.exec_driver_sql("""INSERT INTO coverage_receipts
                (run_id,kind,terminal_state,path,sha256,created_at) VALUES (?,'run',?,?,?,?)""", (run_id,status,path,digest,now))
            for checkpoint in checkpoints:
                connection.exec_driver_sql("""UPDATE coverage_scopes SET status=?,updated_at=?,evidence_json=?,
                    last_full_scan=COALESCE(?,last_full_scan),last_delta_scan=COALESCE(?,last_delta_scan)
                    WHERE coverage_id=?""",
                    (status,now,canonical_json({"run_id":run_id,"receipt_path":path,"receipt_sha256":digest}),
                     now if status=="exhausted" and run["scan_kind"]=="full" else None,
                     now if status=="exhausted" and run["scan_kind"]=="delta" else None,checkpoint["coverage_id"]))
            event(connection,run_id,"run_" + status)
        return self.inspect(run_id)
