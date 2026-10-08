-- Add immutable canonical source snapshots; reuse existing jobs and job_sources.
CREATE TABLE IF NOT EXISTS ttea_canonical_batches (
 batch_id TEXT PRIMARY KEY, artifact_sha256 TEXT NOT NULL, imported_at TEXT NOT NULL,
 record_count INTEGER NOT NULL CHECK(record_count = 100)
);
CREATE TABLE IF NOT EXISTS ttea_canonical_records (
 batch_id TEXT NOT NULL REFERENCES ttea_canonical_batches(batch_id),
 external_id TEXT NOT NULL, job_id INTEGER NOT NULL REFERENCES jobs(id),
 record_json TEXT NOT NULL CHECK(json_valid(record_json)), record_sha256 TEXT NOT NULL,
 PRIMARY KEY(batch_id, external_id), UNIQUE(batch_id, job_id),
 CHECK(json_extract(record_json, '$.external_id') = external_id),
 CHECK(json_type(record_json, '$.original_model_score') = 'integer'),
 CHECK(json_extract(record_json, '$.original_model_score') BETWEEN 0 AND 100)
);
CREATE TRIGGER IF NOT EXISTS ttea_canonical_records_no_update BEFORE UPDATE ON ttea_canonical_records BEGIN SELECT RAISE(ABORT, 'Canonical evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS ttea_canonical_records_no_delete BEFORE DELETE ON ttea_canonical_records BEGIN SELECT RAISE(ABORT, 'Canonical evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS ttea_canonical_batches_no_update BEFORE UPDATE ON ttea_canonical_batches BEGIN SELECT RAISE(ABORT, 'Canonical batch is immutable'); END;
CREATE TRIGGER IF NOT EXISTS ttea_canonical_batches_no_delete BEFORE DELETE ON ttea_canonical_batches BEGIN SELECT RAISE(ABORT, 'Canonical batch is immutable'); END;
CREATE VIEW IF NOT EXISTS ttea_canonical_job_details AS
 SELECT r.batch_id, r.external_id, r.job_id, r.record_json,
 json_extract(r.record_json, '$.original_model_score') AS original_model_score,
 json_extract(r.record_json, '$.category') AS category,
 json_extract(r.record_json, '$.semantic_status') AS semantic_status,
 json_extract(r.record_json, '$.semantic_flags') AS semantic_flags,
 json_extract(r.record_json, '$.flag_classification') AS flag_classification,
 json_extract(r.record_json, '$.assessment_version') AS assessment_version,
 json_extract(r.record_json, '$.scoring_provider') AS scoring_provider,
 json_extract(r.record_json, '$.model') AS scoring_model,
 json_extract(r.record_json, '$.scored_at') AS scored_at,
 json_extract(r.record_json, '$.strengths') AS strengths,
 json_extract(r.record_json, '$.gaps') AS gaps,
 json_extract(r.record_json, '$.hard_requirements') AS hard_requirements,
 json_extract(r.record_json, '$.reasoning') AS reasoning,
 json_extract(r.record_json, '$.repair_actions') AS repair_actions,
 json_extract(r.record_json, '$.raw_response_reference') AS raw_response_reference,
 j.review_status AS review_status,
 CASE WHEN json_extract(r.record_json, '$.original_model_score') >= 70 THEN 'apply'
      WHEN json_extract(r.record_json, '$.original_model_score') >= 40 THEN 'review'
      ELSE 'reject' END AS action_bucket
 FROM ttea_canonical_records r JOIN jobs j ON j.id = r.job_id;
