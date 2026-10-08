-- TTEA Migration 006
-- AI gate decision persistence.
-- Explicit migration; never an automatic startup migration.
--
-- Extends coverage_scoring_outbox with a real terminal
-- 'eliminated' state and persists KEEP/ELIMINATE decisions.
--
-- Semantic invariant:
--   ELIMINATE => score NULL, no ttea_assessment
--   KEEP      => scored result may be persisted
--
PRAGMA foreign_keys = ON;

CREATE TABLE coverage_scoring_outbox_v2 (
    canonical_job_id INTEGER PRIMARY KEY
        REFERENCES jobs(id),

    run_id TEXT NOT NULL
        REFERENCES coverage_runs(run_id),

    status TEXT NOT NULL
        DEFAULT 'needs_scoring'
        CHECK (
            status IN (
                'needs_scoring',
                'scored',
                'eliminated'
            )
        ),

    created_at TEXT NOT NULL
);

INSERT INTO coverage_scoring_outbox_v2 (
    canonical_job_id,
    run_id,
    status,
    created_at
)
SELECT
    canonical_job_id,
    run_id,
    status,
    created_at
FROM coverage_scoring_outbox;

DROP TABLE coverage_scoring_outbox;

ALTER TABLE coverage_scoring_outbox_v2
RENAME TO coverage_scoring_outbox;

CREATE TABLE ttea_job_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    canonical_job_id INTEGER NOT NULL
        REFERENCES jobs(id),

    coverage_run_id TEXT NOT NULL
        REFERENCES coverage_runs(run_id),

    ttea_run_id INTEGER NOT NULL
        REFERENCES ttea_runs(id),

    gate_decision TEXT NOT NULL
        CHECK (
            gate_decision IN (
                'KEEP',
                'ELIMINATE'
            )
        ),

    gate_confidence REAL NOT NULL
        CHECK (
            gate_confidence >= 0
            AND gate_confidence <= 1
        ),

    gate_reason_code TEXT NOT NULL,

    gate_payload_json TEXT NOT NULL
        CHECK (json_valid(gate_payload_json)),

    score INTEGER,

    score_band TEXT,

    score_payload_json TEXT
        CHECK (
            score_payload_json IS NULL
            OR json_valid(score_payload_json)
        ),

    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    profile_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,

    CHECK (
        (
            gate_decision='ELIMINATE'
            AND score IS NULL
            AND score_band IS NULL
            AND score_payload_json IS NULL
        )
        OR
        (
            gate_decision='KEEP'
            AND score IS NOT NULL
            AND score BETWEEN 0 AND 100
            AND score_band IN (
                'reject',
                'maybe',
                'strong'
            )
            AND score_payload_json IS NOT NULL
        )
    ),

    UNIQUE (
        canonical_job_id,
        coverage_run_id
    )
);

CREATE INDEX ix_ttea_job_decisions_job
ON ttea_job_decisions (
    canonical_job_id
);

CREATE INDEX ix_ttea_job_decisions_gate
ON ttea_job_decisions (
    gate_decision
);

CREATE INDEX ix_ttea_job_decisions_coverage_run
ON ttea_job_decisions (
    coverage_run_id
);
