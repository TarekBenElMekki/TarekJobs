-- V1.3 analytics begins at activation. No backfill; apply explicitly, never at startup.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS application_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_token TEXT NOT NULL UNIQUE,
    client_id TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'chrome_sidepanel',
    started_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    ended_at TEXT,
    end_reason TEXT,
    timezone TEXT,
    extension_version TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_application_sessions_client ON application_sessions(client_id);
CREATE INDEX IF NOT EXISTS ix_application_sessions_started ON application_sessions(started_at);
CREATE INDEX IF NOT EXISTS ix_application_sessions_ended ON application_sessions(ended_at);
CREATE UNIQUE INDEX IF NOT EXISTS ix_application_sessions_open_client
ON application_sessions(client_id) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS application_activity_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES application_sessions(id),
    job_id INTEGER REFERENCES jobs(id),
    application_id INTEGER REFERENCES applications(id),
    action_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json))
);
CREATE INDEX IF NOT EXISTS ix_application_activity_session
ON application_activity_events(session_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_application_activity_occurred
ON application_activity_events(occurred_at);
CREATE INDEX IF NOT EXISTS ix_application_activity_job ON application_activity_events(job_id);
CREATE TRIGGER IF NOT EXISTS application_activity_no_update
BEFORE UPDATE ON application_activity_events
BEGIN
    SELECT RAISE(ABORT, 'Application activity events are immutable');
END;
CREATE TRIGGER IF NOT EXISTS application_activity_no_delete
BEFORE DELETE ON application_activity_events
BEGIN
    SELECT RAISE(ABORT, 'Application activity events are immutable');
END;
