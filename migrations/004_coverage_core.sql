-- TN-0: additive only. Explicit scratch migration; never startup migration.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS coverage_sources (
 source_id TEXT PRIMARY KEY, source_name TEXT NOT NULL, source_type TEXT NOT NULL,
 acquisition_method TEXT NOT NULL, adapter_key TEXT NOT NULL, adapter_version TEXT NOT NULL,
 base_url TEXT, status TEXT NOT NULL DEFAULT 'enabled' CHECK(status IN ('enabled','disabled')),
 capabilities_json TEXT NOT NULL CHECK(json_valid(capabilities_json)),
 limits_json TEXT NOT NULL CHECK(json_valid(limits_json)),
 canonical_hints_json TEXT NOT NULL CHECK(json_valid(canonical_hints_json)),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coverage_scopes (
 coverage_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES coverage_sources(source_id),
 scope_country TEXT NOT NULL, scope_work_mode TEXT NOT NULL, scope_region TEXT NOT NULL,
 partition_key TEXT NOT NULL, partition_metadata_json TEXT NOT NULL CHECK(json_valid(partition_metadata_json)),
 status TEXT NOT NULL DEFAULT 'planned'
 CHECK(status IN ('planned','running','completed','partial','failed','exhausted','blocked','manual_only','unavailable')),
 last_full_scan TEXT, last_delta_scan TEXT,
 evidence_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(evidence_json)),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(source_id,scope_country,scope_work_mode,scope_region,partition_key)
);
CREATE TABLE IF NOT EXISTS coverage_runs (
 run_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES coverage_sources(source_id),
 scope_json TEXT NOT NULL CHECK(json_valid(scope_json)),
 adapter_version TEXT NOT NULL, adapter_metadata_json TEXT NOT NULL CHECK(json_valid(adapter_metadata_json)),
 scan_kind TEXT NOT NULL CHECK(scan_kind IN ('full','delta')), artifact_root TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('planned','running','completed','partial','failed','exhausted','blocked','manual_only','unavailable')),
 started_at TEXT NOT NULL, completed_at TEXT,
 pages_expected INTEGER CHECK(pages_expected >= 0),
 pages_processed INTEGER NOT NULL DEFAULT 0 CHECK(pages_processed >= 0),
 last_cursor TEXT CHECK(last_cursor IS NULL OR json_valid(last_cursor)),
 raw_records_seen INTEGER NOT NULL DEFAULT 0 CHECK(raw_records_seen >= 0),
 raw_records_valid INTEGER NOT NULL DEFAULT 0 CHECK(raw_records_valid >= 0),
 canonical_new INTEGER NOT NULL DEFAULT 0 CHECK(canonical_new >= 0),
 canonical_reused INTEGER NOT NULL DEFAULT 0 CHECK(canonical_reused >= 0),
 duplicates INTEGER NOT NULL DEFAULT 0 CHECK(duplicates >= 0),
 detail_pages_attempted INTEGER NOT NULL DEFAULT 0 CHECK(detail_pages_attempted >= 0),
 detail_pages_completed INTEGER NOT NULL DEFAULT 0 CHECK(detail_pages_completed >= 0),
 detail_pages_failed INTEGER NOT NULL DEFAULT 0 CHECK(detail_pages_failed >= 0),
 active_jobs INTEGER NOT NULL DEFAULT 0 CHECK(active_jobs >= 0),
 expired_jobs INTEGER NOT NULL DEFAULT 0 CHECK(expired_jobs >= 0),
 exhausted INTEGER NOT NULL DEFAULT 0 CHECK(exhausted IN (0,1)),
 exhaustion_reason TEXT, last_full_scan TEXT, last_delta_scan TEXT,
 errors_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(errors_json)),
 receipt_path TEXT, receipt_sha256 TEXT,
 CHECK((status='exhausted') = (exhausted=1)),
 CHECK(exhausted=0 OR (exhaustion_reason IS NOT NULL AND errors_json='[]')),
 CHECK((status IN ('planned','running') AND completed_at IS NULL) OR
       (status NOT IN ('planned','running') AND completed_at IS NOT NULL)),
 CHECK((receipt_path IS NULL) = (receipt_sha256 IS NULL)),
 CHECK(raw_records_valid <= raw_records_seen),
 CHECK(canonical_new+canonical_reused <= raw_records_valid)
);
CREATE INDEX IF NOT EXISTS ix_coverage_runs_source ON coverage_runs(source_id,started_at);
CREATE TABLE IF NOT EXISTS coverage_checkpoints (
 run_id TEXT NOT NULL REFERENCES coverage_runs(run_id),
 partition_key TEXT NOT NULL,
 coverage_id TEXT NOT NULL REFERENCES coverage_scopes(coverage_id),
 partition_metadata_json TEXT NOT NULL CHECK(json_valid(partition_metadata_json)),
 cursor_json TEXT CHECK(cursor_json IS NULL OR json_valid(cursor_json)),
 pages_processed INTEGER NOT NULL DEFAULT 0 CHECK(pages_processed >= 0),
 pages_expected INTEGER CHECK(pages_expected >= 0),
 adapter_version TEXT NOT NULL, updated_at TEXT NOT NULL,
 completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1)),
 terminal_json TEXT CHECK(terminal_json IS NULL OR json_valid(terminal_json)),
 PRIMARY KEY(run_id,partition_key),
 CHECK((completed=1) = (terminal_json IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS coverage_inventory_pages (
 run_id TEXT NOT NULL, partition_key TEXT NOT NULL, page_number INTEGER NOT NULL CHECK(page_number>0),
 cursor_json TEXT NOT NULL CHECK(json_valid(cursor_json)),
 raw_records_seen INTEGER NOT NULL CHECK(raw_records_seen >= 0),
 artifact_path TEXT NOT NULL, artifact_sha256 TEXT NOT NULL,
 PRIMARY KEY(run_id,partition_key,page_number),
 UNIQUE(run_id,partition_key,cursor_json),
 FOREIGN KEY(run_id,partition_key) REFERENCES coverage_checkpoints(run_id,partition_key)
);
CREATE TABLE IF NOT EXISTS coverage_inventory (
 inventory_id INTEGER PRIMARY KEY,
 run_id TEXT NOT NULL REFERENCES coverage_runs(run_id),
 source_id TEXT NOT NULL REFERENCES coverage_sources(source_id),
 source_record_key TEXT NOT NULL, external_id TEXT, source_url TEXT NOT NULL, canonical_url TEXT,
 discovered_at TEXT NOT NULL,
 detail_status TEXT NOT NULL DEFAULT 'pending' CHECK(detail_status IN ('pending','ready','failed')),
 detail_attempts INTEGER NOT NULL DEFAULT 0 CHECK(detail_attempts>=0),
 raw_artifact_path TEXT NOT NULL, raw_artifact_sha256 TEXT NOT NULL,
 detail_artifact_path TEXT, detail_artifact_sha256 TEXT,
 canonical_job_id INTEGER REFERENCES jobs(id),
 dedupe_status TEXT NOT NULL DEFAULT 'pending' CHECK(dedupe_status IN ('pending','create','reuse','review')),
 normalized_json TEXT CHECK(normalized_json IS NULL OR json_valid(normalized_json)),
 error_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(error_json)),
 UNIQUE(run_id,source_record_key),
 CHECK((detail_artifact_path IS NULL) = (detail_artifact_sha256 IS NULL)),
 CHECK((dedupe_status IN ('create','reuse')) = (canonical_job_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_coverage_inventory_pending ON coverage_inventory(run_id,detail_status,dedupe_status,inventory_id);
CREATE TABLE IF NOT EXISTS coverage_provenance (
 provenance_id INTEGER PRIMARY KEY,
 canonical_job_id INTEGER NOT NULL REFERENCES jobs(id),
 source_id TEXT NOT NULL REFERENCES coverage_sources(source_id), source_record_key TEXT NOT NULL,
 external_id TEXT, source_url TEXT NOT NULL, canonical_url TEXT,
 ats_namespace TEXT, ats_requisition_id TEXT, posted_date TEXT,
 first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
 duplicate_confidence REAL NOT NULL CHECK(duplicate_confidence BETWEEN 0 AND 1),
 dedupe_method TEXT NOT NULL, evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
 raw_artifact_path TEXT NOT NULL, raw_artifact_sha256 TEXT NOT NULL,
 detail_artifact_path TEXT, detail_artifact_sha256 TEXT,
 UNIQUE(source_id,source_record_key)
);
CREATE INDEX IF NOT EXISTS ix_coverage_provenance_job ON coverage_provenance(canonical_job_id);
CREATE INDEX IF NOT EXISTS ix_coverage_provenance_url ON coverage_provenance(canonical_url);
CREATE INDEX IF NOT EXISTS ix_coverage_provenance_external ON coverage_provenance(source_id,external_id);
CREATE INDEX IF NOT EXISTS ix_coverage_provenance_ats ON coverage_provenance(ats_namespace,ats_requisition_id);
CREATE TABLE IF NOT EXISTS coverage_dedupe_candidates (
 inventory_id INTEGER NOT NULL REFERENCES coverage_inventory(inventory_id),
 canonical_job_id INTEGER NOT NULL REFERENCES jobs(id),
 confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
 method TEXT NOT NULL, evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
 created_at TEXT NOT NULL,
 PRIMARY KEY(inventory_id,canonical_job_id)
);
CREATE TABLE IF NOT EXISTS coverage_scoring_outbox (
 canonical_job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
 run_id TEXT NOT NULL REFERENCES coverage_runs(run_id),
 status TEXT NOT NULL DEFAULT 'needs_scoring' CHECK(status IN ('needs_scoring','scored')),
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coverage_errors (
 error_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES coverage_runs(run_id),
 stage TEXT NOT NULL, item_key TEXT NOT NULL, code TEXT NOT NULL,
 occurred_at TEXT NOT NULL, resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_coverage_errors_run ON coverage_errors(run_id,resolved_at);
CREATE TABLE IF NOT EXISTS coverage_receipts (
 receipt_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES coverage_runs(run_id),
 kind TEXT NOT NULL CHECK(kind IN ('import','run')), terminal_state TEXT NOT NULL,
 path TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(run_id,kind,sha256)
);
CREATE TABLE IF NOT EXISTS coverage_events (
 event_id INTEGER PRIMARY KEY, run_id TEXT REFERENCES coverage_runs(run_id),
 event_type TEXT NOT NULL, occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_coverage_events_run ON coverage_events(run_id,event_id);
