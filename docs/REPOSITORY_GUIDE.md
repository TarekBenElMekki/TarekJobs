# T/TEA repository guide

## Current implementation

`src/ui_server.py` serves the local dashboard. Generic active job lists and details
require the new-AI KEEP predicate frozen in checkpoint
`27b5bff9c6fefa61077ee18add823f92d08cb757`. Missing gate storage fails closed.
Application-specific history, tracker and followups preserve archived/ELIMINATE
records. Canonical batch routes expose immutable historical evidence rather than
admitting records to the active pool.

`src/application_assistant.py` owns application states, transitions, immutable
events, document registrations and manually saved application URLs.
`src/application_analytics.py` records optional browser-session activity and
reports local-calendar-day totals. Missing analytics storage does not block core
application mutations. Session tokens and request payloads are not logged.

`src/production_write_guard.py` verifies a rolling SQLite backup before protected
production mutation. It holds a process-local lock through commit or rollback.
Dashboard writes, coverage writes and canonical batch imports use this guard.
It does not coordinate independent processes or retrofit legacy maintenance scripts.
Never run old repair/rescore scripts as part of repository cleanup.

`src/coverage` contains generic source contracts, normalization, deduplication,
provenance, durable outcomes, checkpoints and explicit adapters. Native HTTP
adapters enforce robots rules, bounded requests and terminal challenge handling.
Tests use local fixtures and injected transports. The Tanitjobs browser-capture
importer consumes hash-verified saved evidence, permits only scratch databases,
and never claims that a manual capture set exhausts the site.

`src/ttea_batch_import.py` validates complete canonical artifacts before an atomic
import. It preserves manual state and existing assessments. Importing evidence
never creates a KEEP admission decision or invokes a model.

`src/ttea_live.py` reads an explicitly configured observation directory using
`TTEA_JOB_LIVE_ROOT` (or `TTEA_REPO/data/job_hunter_live`). It bounds JSON and event
tails, projects public fields, removes URL credentials/query strings, rejects
path traversal, and renders only passive HTML. The observer uses a sandboxed
iframe and restrictive CSP. HUNT polling slows when idle or unavailable and stops
when navigating away. Viewing HUNT does not launch acquisition or scoring.

## Database setup and migrations

For a new scratch database, initialize the ORM base using `src.database.init_db`,
then explicitly apply migrations in numeric order:

1. 001: immutable canonical batch evidence (depends on jobs/job_sources).
2. 002: normalized applications, events and document library (depends on jobs).
3. 003: optional session analytics (depends on 002; explicit activation only).
4. 004: coverage state and provenance (depends on the ORM base).
5. 005: coverage record outcomes (depends on 004).
6. 006: persisted AI decisions and eliminated outbox state (depends on 004 and ttea_runs).

Migration 006 is a one-time schema transition; do not blindly replay it against an
already migrated database. Migration 003 is never activated by server startup.
`tests/test_release_contract.py` exercises the complete chain on an empty scratch
SQLite file, checks required columns and decision foreign keys, and runs
`PRAGMA integrity_check` and `PRAGMA foreign_key_check`.

Use `.venv/Scripts/python.exe` on Windows. `requirements.txt` includes `tzdata` for
IANA timezone reports on Windows. The project name in `pyproject.toml` is `ttea`.
No separate unrelated dependency reconciliation is required.

## Release verification

Run `python -m pytest tests -q` through the project `.venv`. API tests inject scratch
engines before server lifespan execution. Acquisition tests use fixture adapters
and mock transports. Production data is not a test fixture. For staged/clean-tree
verification, export the Git tree to a temporary directory and run it with the
same `.venv` interpreter; prevent external socket connections and production DB
access. Allow Python's internal Windows socketpair needed by the ASGI test client.

The consolidation release additionally hash-guards production before and after
verification and checks the live canonical counts and API KEEP set through a
read-only SQLite engine without running server startup. No production migration,
model call, acquisition, or application submission belongs in this release gate.

## Branding and historical material

The active wordmark and icon are `ui/ttea-wordmark.svg` and `ui/ttea-icon.svg`.
They are deterministic native assets and use no model service. Supplied renamed
PNG/ICO files retain the historical raster artwork and are preserved for provenance;
they are not the active dashboard brand. Historical raster build/patch scripts
remain legacy maintenance tools, not the source of the current SVG identity.

`TTEA_CONTEXT.txt` preserves the user's renamed 2026-08-24 source export. Its
historical source copies are not implementation inputs. Current source and this
guide are authoritative. TN0/TN1 implementation reports and JSON evidence record
past handoffs; statements such as TESTS_RUN=0 describe those historical handoffs,
not the current consolidation regression. Old TarekJobs paths/names in evidence
are preserved rather than rewritten.

## Local-only artifacts

Production databases and SQLite sidecars, logs, coverage locks, caches, local
backups, raw runtime acquisition, OCR output, exports, polling state and UI run
state/history are excluded from the current Git tree. Previously tracked runtime
and backup files are removed from the index only; local bytes are preserved.
`tests/fixtures` remains the explicit offline fixture library. Legacy browser
captures under `data/fixtures` are local-only: four old captures contain
API-key-shaped values from captured website content and are not test inputs.
No production database contents are added to Git. Historical commits are retained;
repository hygiene does not rewrite the frozen checkpoint or earlier history.
