# TN-0 Coverage Core

Coverage Core is additive infrastructure in `src.coverage`. It does not import or invoke
a scorer, execute collectors, migrate production at startup, backfill historical
batches, or change Application Assistant/review semantics. All timestamps are UTC.
The implementation uses existing dependencies and the project virtual environment.

## Architecture and integration

`contracts.py` declares metadata, scope, partition, inventory page, detail,
checkpoint and terminal evidence dataclasses and the `SourceAdapter` Protocol.
`service.py` registers sources/scopes, freezes paginated inventory, checkpoints,
fetches details, normalizes, imports and publishes terminal receipts.
`normalization.py` uses the existing `JobRecord`, `clean_text`, `norm_text` and
date parser. The canonical representation is still `database.JobORM` / `jobs`.
Only descriptive JobRecord fields are inserted; existing ORM defaults apply.
New jobs retain legacy default score fields but have **no new suitability
assessment**. The outbox, not a default numeric field, identifies scoring work.

`dedupe.py` implements Global Dedupe V2, separate from the legacy upsert functions.
`importer.py` inserts jobs with SQLAlchemy Core (including existing column defaults),
links existing `job_sources` for UI compatibility, and writes richer provenance.
It never updates existing canonical job rows. `storage.py` owns explicit
connections, foreign keys, transactions and the existing V1.3.1 write guard.
`artifacts.py` publishes evidence and holds an OS run lock; process death releases
that lock. `fixture.py` streams local JSONL and uses the fixture SHA256 in its adapter
version. `__main__.py` exposes the CLI.

The existing YAML source registry remains a legacy planning/configuration input.
TN-0's persistent acquisition registry is populated explicitly through adapters;
it does not activate any of the YAML's real sources.

## Database

`migrations/004_coverage_core.sql` uses the existing additive SQL-file convention.
The scratch initializer applies it explicitly and creates missing canonical ORM
tables. It does not call legacy `init_db`, which performs historical repairs.
There is no production migration command in this batch.

Tables:
- `coverage_sources`: stable source identity, adapter metadata, capabilities,
  limits, canonical hints, enabled/disabled state.
- `coverage_scopes`: source + country/work mode/region + stable partition key;
  partition metadata, coverage state, scan timestamps and terminal evidence reference.
- `coverage_runs`: pinned adapter metadata/version and artifact root, lifecycle,
  scan kind, transactional counters, errors, current receipt reference.
- `coverage_checkpoints`: one durable cursor and boundary per run/partition.
- `coverage_inventory_pages`: page evidence, cursor uniqueness and raw record counts.
- `coverage_inventory`: per-run deduplicated source appearances, pending/ready/failed
  detail state, attempts, normalized candidate, raw/detail artifacts and import result.
- `coverage_provenance`: unique source + source-record key linked to a canonical job,
  identity evidence, timestamps and artifacts; many appearances may link one job.
- `coverage_dedupe_candidates`: persistent review evidence keyed by inventory/job.
- `coverage_scoring_outbox`: one durable needs-scoring entry per newly created job.
- `coverage_errors`: fixed diagnostic codes, stage/item identity, resolution history.
- `coverage_receipts`: immutable-by-convention import/run receipt history.
- `coverage_events`: persistent runtime events, fixed codes only (no payloads,
  URLs, credentials or exception text).

Named indexes: `ix_coverage_runs_source`, `ix_coverage_inventory_pending`,
`ix_coverage_provenance_job`, `ix_coverage_provenance_url`,
`ix_coverage_provenance_external`, `ix_coverage_provenance_ats`,
`ix_coverage_errors_run`, `ix_coverage_events_run`.
SQLite also creates indexes for primary keys and unique constraints.

## Adapter contract and planning

An adapter exposes metadata without acquisition: source identity/type/method,
adapter key/version, optional base URL; capabilities for pagination, date/country/
location/category filtering, details, official API and feed; optional exposed-result
and request-rate limits; external/application/ATS identity hints.

`plan(scope)` returns stable partitions; `inventory(scope, partition, checkpoint)`
returns bounded records, next cursor or explicit terminal evidence, and optional
expected page count. A nonterminal page must advance to a previously unused cursor.
A terminal page cannot carry a next cursor. Empty pages can continue if their cursor
advances; a cursor cycle becomes a persisted error, never exhaustion.
`detail(record)` returns descriptive values or a failure code. Sources without
detail pages normalize inventory values directly.

Use global `Scope("*", "*")` when the source can be inventoried once and classified
afterward. Scope does not automatically filter adapter results or duplicate scans.
Partitions are independent checkpoint boundaries. Their metadata cannot silently
change under an existing key. Limits are declarative in TN-0: future network adapters
must implement their rate policy; no real network scheduler is activated here.

A future real adapter implements this Protocol and calls `CoverageService`.
No engine branch should depend on its board/country. Stable source-record keys and
ATS namespaces must describe actual source identities, not guessed matches.
Changing cursor semantics requires changing adapter_version. Full metadata and all
checkpoint versions are compared before resumption; mismatches fail explicitly.

## Lifecycle and resume

`start` stores a planned run and its complete partition plan. `run` moves it to
running. Each inventory page, unique records, page counters and checkpoint commit
together. All partitions' inventory is persisted before detail processing starts.
A crash before a page commit may repeat that page; a committed page is never
rediscovered on restart. Detail attempts are recorded before acquisition, and
successful values/artifacts are persisted before canonical import. A crash can
retry an uncommitted detail request but cannot duplicate a canonical import.

The orchestrator reads bounded inventory windows. Pending details continue on
resume; failures are retried only with `--retry-failed`. Successfully persisted
details and canonical imports are skipped. Concurrent workers for one DB/run are
rejected using Windows/POSIX file locking; unrelated runs serialize DB writes with
SQLite BEGIN IMMEDIATE. Lock files are harmless and must not be deleted while a
worker holds them. Resumable runs reserve their coverage partitions.

Counter definitions: raw_records_seen counts all page appearances; raw_records_valid
counts unique successfully normalized inventory records; duplicates counts repeated
inventory appearances plus canonical_reused. Canonical new/reused count import
outcomes, with review candidates excluded. Detail attempted counts attempts including
crash/retry; completed counts successful records; failed counts currently failed
records. Active/expired counts normalized is_open values, not suitability.
Counters are recomputed transactionally from persisted evidence.

## Terminal evidence and receipts

Only explicit adapter classifications exhausted, blocked, manual_only or unavailable
can account for a scope. Ordinary inventory/detail/import failures, pending work and
ambiguous dedupe yield partial and accounted_for=false. Exhausted requires every
partition's boundary, no unresolved errors, and no pending/failed/review records.
Mixed explicit boundaries conservatively report blocked, unavailable or manual_only
ahead of exhausted. Full/delta scan timestamps advance only on clean exhaustion.

Run receipts snapshot metadata, scope, adapter version, timestamps, boundary
checkpoints, counts, errors, terminal evidence and inventory page references.
Bounded hashed record manifests include detail/raw artifact references and canonical
outcomes, so the receipt evidence can be inspected without depending on a live DB.
The scoring outbox remains the authoritative durable handoff for new IDs.
`receipt` verifies the receipt hash. Finalization also verifies all saved raw/detail
and page artifact hashes before publication.

The schema permits planned/running/completed/partial/failed and explicit terminal
states. TN-0 emits partial for recoverable errors/budgets, and explicit terminal
states for accounted boundaries. A terminal run is immutable operationally; resume
verifies and returns its existing receipt. Partial receipt history is retained
when work resumes. Review candidates require a future explicit adjudication service;
TN-0 deliberately does not automatically approve them or alter the existing review queue.

## Dedupe V2 and provenance

Strong identifiers are existing source-record key, source-scoped external ID,
canonical/source URL (including legacy jobs/job_sources), or **ATS namespace plus
requisition ID**. A requisition ID alone is not global evidence. Conflicting strong
identifiers produce review with every candidate, rather than choosing one.

URL normalization lowercases scheme/host, removes fragments and known tracking
parameters, preserves paths and identity-bearing query parameters. It does not
guess redirect equivalence. Legacy URLs are normalized on read, without backfills.
Normalized company/title/location, posted date and deterministic description-token
Jaccard similarity provide review evidence only. Company/title similarity never
auto-merges, even when description similarity is perfect. Unknown company values do
not establish descriptive identity. Candidate lookup uses existing normalized company
indexes and streams legacy URL rows; large-scale URL indexing is a later optimization.

Reuse attaches provenance, updates run accounting, and never calls scoring.
Create adds one canonical job and a unique needs-scoring outbox entry. Repeated
processing cannot add another outbox entry. Raw source values remain in artifacts
even where cleaned canonical values differ.

## Bulk-write safety and artifacts

Every write connection enables foreign keys. All canonical import work for a pending
run occurs within **one production_write_guard context, one BEGIN IMMEDIATE,
one commit and one import receipt**. Any failure rolls back canonical jobs, provenance,
review candidates, outbox and counters together. No per-job backup occurs.
Inventory/detail checkpoint transactions are separate acquisition transactions;
on an explicitly authorized production service these receive their own guard.
Read-only commands use SQLite mode=ro. Scratch paths do not create production backups.

Production mutation requires `Database(path, allow_production=True)` from an explicit
service caller. The CLI never opts in. Configured production, authoritative data/jobs.db,
resolved aliases and hardlinks are protected. Migration is always refused for
production even with service opt-in.

Artifacts are content addressed:
`<root>/<source_id>/<run_id>/{inventory,details,receipts}/<sha256>.json`.
Canonical UTF-8 JSON is atomically published via a same-directory temporary file,
flush/fsync and replacement. Different runs have different directories. Reusing an
existing artifact verifies its bytes. Raw responses live in artifacts, not large
SQLite blobs; normalized descriptive candidates are staging data.
A crash after artifact publication but before DB commit may leave an unreferenced
file. Only database-referenced receipts represent committed work; no automatic
artifact deletion occurs. Keep the DB and artifact root together for resume/audit.

## Offline operation (PowerShell)

Run from TTEA with the existing virtual environment. Choose disposable paths:

```powershell
$python = ".\\.venv\\Scripts\\python.exe"
$db = "$env:TEMP\\ttea-tn0-demo.db"
$artifacts = "$env:TEMP\\ttea-tn0-artifacts"

& $python -m src.coverage --db $db init-scratch
& $python -m src.coverage --db $db register-source --fixture tests/fixtures/coverage/success.jsonl
& $python -m src.coverage --db $db list-sources
& $python -m src.coverage --db $db start --fixture tests/fixtures/coverage/success.jsonl --artifacts $artifacts --dry-run
& $python -m src.coverage --db $db start --fixture tests/fixtures/coverage/success.jsonl --artifacts $artifacts --max-pages 1
# Copy run_id from the JSON output:
& $python -m src.coverage --db $db resume RUN_ID --fixture tests/fixtures/coverage/success.jsonl
& $python -m src.coverage --db $db run RUN_ID
& $python -m src.coverage --db $db receipt RUN_ID
& $python -m src.coverage --db $db needs-scoring RUN_ID
& $python -m src.coverage --db $db status
& $python -m src.coverage --db $db start --fixture tests/fixtures/coverage/cross_source.jsonl --artifacts $artifacts
& $python -m src.coverage --db $db start --fixture tests/fixtures/coverage/detail_failure.jsonl --artifacts $artifacts
```

success.jsonl has two pages, a repeated inventory record, successful details and an
expired example. cross_source.jsonl reuses the first employer vacancy.
detail_failure.jsonl produces partial and a persisted failure. Its failure is
deterministic, so retry alone will not repair it. JSONL starts with a format/source/
page_size header; subsequent records contain key, source_url, optional external_id,
values, detail overrides, optional partition and optional detail_error.

`--max-pages` / `--max-details` deliberately pause work for inspection/resume.
Dry-run prints metadata/plan without DB/artifact writes or acquisition.
Implementation tests are run with
`.\\.venv\\Scripts\\python.exe -m pytest tests/test_coverage_core.py`.
They are developer checks, not the architect's independent TN-0 certification.
