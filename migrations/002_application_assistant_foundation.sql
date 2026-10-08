-- TarekJobs Application Assistant — normalized foundation.
-- Additive only: no existing jobs are modified or backfilled.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    job_id INTEGER NOT NULL UNIQUE
        REFERENCES jobs(id),

    current_state TEXT NOT NULL DEFAULT 'not_started'
        CHECK (
            current_state IN (
                'not_started',
                'application_preparing',
                'ready_to_apply',
                'applied',
                'awaiting_response',
                'assessment',
                'interview',
                'technical_test',
                'hr_interview',
                'offer',
                'hired',
                'rejected',
                'withdrawn',
                'expired',
                'not_interested'
            )
        ),

    application_url TEXT,
    platform TEXT,

    next_action TEXT,
    next_action_at TEXT,
    follow_up_at TEXT,

    started_at TEXT,
    submitted_at TEXT,
    last_activity_at TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS
    ix_applications_state
ON applications(current_state);

CREATE INDEX IF NOT EXISTS
    ix_applications_next_action
ON applications(next_action_at);

CREATE INDEX IF NOT EXISTS
    ix_applications_follow_up
ON applications(follow_up_at);


CREATE TABLE IF NOT EXISTS application_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    application_id INTEGER NOT NULL
        REFERENCES applications(id)
        ON DELETE CASCADE,

    event_type TEXT NOT NULL,

    from_state TEXT,
    to_state TEXT,

    actor TEXT NOT NULL DEFAULT 'user',
    source TEXT NOT NULL DEFAULT 'tarekjobs',

    note TEXT,

    metadata_json TEXT NOT NULL DEFAULT '{}'
        CHECK(json_valid(metadata_json)),

    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS
    ix_application_events_application
ON application_events(application_id, occurred_at);

CREATE INDEX IF NOT EXISTS
    ix_application_events_type
ON application_events(event_type);


-- Application history is evidence.
CREATE TRIGGER IF NOT EXISTS
    application_events_no_update
BEFORE UPDATE ON application_events
BEGIN
    SELECT RAISE(
        ABORT,
        'Application events are immutable'
    );
END;

CREATE TRIGGER IF NOT EXISTS
    application_events_no_delete
BEFORE DELETE ON application_events
BEGIN
    SELECT RAISE(
        ABORT,
        'Application events are immutable'
    );
END;


CREATE TABLE IF NOT EXISTS document_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    document_key TEXT NOT NULL,

    kind TEXT NOT NULL
        CHECK (
            kind IN (
                'cv',
                'motivation_letter'
            )
        ),

    language TEXT NOT NULL,

    niche TEXT NOT NULL,

    format TEXT NOT NULL
        CHECK (
            format IN (
                'pdf',
                'docx'
            )
        ),

    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,

    is_generated INTEGER NOT NULL DEFAULT 0
        CHECK(is_generated IN (0, 1)),

    generated_for_job_id INTEGER
        REFERENCES jobs(id),

    parent_document_id INTEGER
        REFERENCES document_library(id),

    active INTEGER NOT NULL DEFAULT 1
        CHECK(active IN (0, 1)),

    created_at TEXT NOT NULL,

    UNIQUE(file_path, sha256)
);

CREATE INDEX IF NOT EXISTS
    ix_document_library_key
ON document_library(document_key);

CREATE INDEX IF NOT EXISTS
    ix_document_library_kind
ON document_library(kind);

CREATE INDEX IF NOT EXISTS
    ix_document_library_niche
ON document_library(niche);

CREATE INDEX IF NOT EXISTS
    ix_document_library_generated_job
ON document_library(generated_for_job_id);


CREATE TABLE IF NOT EXISTS application_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    application_id INTEGER NOT NULL
        REFERENCES applications(id)
        ON DELETE CASCADE,

    document_id INTEGER NOT NULL
        REFERENCES document_library(id),

    role TEXT NOT NULL
        CHECK (
            role IN (
                'cv',
                'motivation_letter',
                'other'
            )
        ),

    selection_mode TEXT NOT NULL
        CHECK (
            selection_mode IN (
                'recommended',
                'manual',
                'generated'
            )
        ),

    active INTEGER NOT NULL DEFAULT 1
        CHECK(active IN (0, 1)),

    attached_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS
    ix_application_documents_application
ON application_documents(application_id);

CREATE INDEX IF NOT EXISTS
    ix_application_documents_document
ON application_documents(document_id);