-- TN-1 general extensions; apply explicitly AFTER certified migration 004.
-- Additive only. Never applied automatically to production.
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS coverage_record_outcomes (
 inventory_id INTEGER PRIMARY KEY REFERENCES coverage_inventory(inventory_id),
 disposition TEXT NOT NULL CHECK(disposition IN ('excluded','expanded')),
 evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coverage_record_links (
 parent_inventory_id INTEGER NOT NULL REFERENCES coverage_inventory(inventory_id),
 child_inventory_id INTEGER NOT NULL REFERENCES coverage_inventory(inventory_id),
 discovered_in_detail INTEGER NOT NULL CHECK(discovered_in_detail IN (0,1)),
 PRIMARY KEY(parent_inventory_id,child_inventory_id),
 CHECK(parent_inventory_id<>child_inventory_id)
);
CREATE INDEX IF NOT EXISTS ix_coverage_record_links_child ON coverage_record_links(child_inventory_id);
CREATE TABLE IF NOT EXISTS coverage_nonunique_urls (
 url TEXT PRIMARY KEY, reason TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coverage_page_fingerprints (
 run_id TEXT NOT NULL, partition_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
 page_number INTEGER NOT NULL,
 PRIMARY KEY(run_id,partition_key,fingerprint),
 FOREIGN KEY(run_id,partition_key,page_number)
 REFERENCES coverage_inventory_pages(run_id,partition_key,page_number)
);
CREATE TABLE IF NOT EXISTS coverage_overlap_observations (
 observation_id INTEGER PRIMARY KEY,
 run_id TEXT NOT NULL, partition_key TEXT NOT NULL,
 observed_at TEXT NOT NULL, page_number INTEGER NOT NULL,
 records_seen INTEGER NOT NULL CHECK(records_seen>=0),
 records_new INTEGER NOT NULL CHECK(records_new>=0 AND records_new<=records_seen),
 artifact_path TEXT NOT NULL, artifact_sha256 TEXT NOT NULL,
 FOREIGN KEY(run_id,partition_key) REFERENCES coverage_checkpoints(run_id,partition_key)
);
CREATE INDEX IF NOT EXISTS ix_coverage_overlap_run ON coverage_overlap_observations(run_id);
CREATE TABLE IF NOT EXISTS coverage_failure_evidence (
 error_id INTEGER PRIMARY KEY REFERENCES coverage_errors(error_id),
 artifact_path TEXT NOT NULL, artifact_sha256 TEXT NOT NULL
);
