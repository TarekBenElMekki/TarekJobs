TN-0_IMPLEMENTATION_STATUS=COMPLETE

Implementation is complete in `C:\Users\tarek\Projects\TarekJobs`.
This is an implementation handoff, not independent TN-0 certification.

Repository state before/after:
- The working tree was already dirty: requirements.txt, src/ui_server.py,
  tests/test_ui.py, ui/app.js, ui/index.html, ui/styles.css were modified, with
  pre-existing untracked Application Assistant, guard, import, migration and log files.
- All pre-existing source/configuration/UI changes were preserved. No existing
  source file was edited for TN-0. Coverage Core is additive.
- T/TEA was inspected read-only; its pre-existing working-tree changes were preserved.
- Full TarekJobs before/after status and T/TEA after status are in
  [TN0_SAFETY_EVIDENCE.json](TN0_SAFETY_EVIDENCE.json).
- Existing Application Assistant tests emit their normal runtime analytics log.
  No frozen source file or acquisition batch was modified. No commit was made.

Files added:
- migrations/004_coverage_core.sql
- src/coverage/__init__.py
- src/coverage/__main__.py
- src/coverage/contracts.py
- src/coverage/artifacts.py
- src/coverage/storage.py
- src/coverage/normalization.py
- src/coverage/dedupe.py
- src/coverage/fixture.py
- src/coverage/importer.py
- src/coverage/service.py
- tests/test_coverage_core.py
- tests/fixtures/coverage/success.jsonl
- tests/fixtures/coverage/cross_source.jsonl
- tests/fixtures/coverage/detail_failure.jsonl
- docs/TN0_COVERAGE_CORE.md
- docs/TN0_CLI_SMOKE.json
- docs/TN0_SAFETY_EVIDENCE.json
- docs/TN0_IMPLEMENTATION_REPORT.md

Existing source files modified: **none**.
Runtime side effect: existing tests appended normal events to
logs/application_analytics.log. Test databases/artifacts used disposable temp paths.

Migration 004 is additive, was applied only to disposable databases, and creates:

- coverage_checkpoints
- coverage_dedupe_candidates
- coverage_errors
- coverage_events
- coverage_inventory
- coverage_inventory_pages
- coverage_provenance
- coverage_receipts
- coverage_runs
- coverage_scopes
- coverage_scoring_outbox
- coverage_sources

Named indexes:

- ix_coverage_errors_run
- ix_coverage_events_run
- ix_coverage_inventory_pending
- ix_coverage_provenance_ats
- ix_coverage_provenance_external
- ix_coverage_provenance_job
- ix_coverage_provenance_url
- ix_coverage_runs_source

SQLite-generated primary-key/unique indexes:

- sqlite_autoindex_coverage_checkpoints_1
- sqlite_autoindex_coverage_dedupe_candidates_1
- sqlite_autoindex_coverage_inventory_1
- sqlite_autoindex_coverage_inventory_pages_1
- sqlite_autoindex_coverage_inventory_pages_2
- sqlite_autoindex_coverage_provenance_1
- sqlite_autoindex_coverage_receipts_1
- sqlite_autoindex_coverage_runs_1
- sqlite_autoindex_coverage_scopes_1
- sqlite_autoindex_coverage_scopes_2
- sqlite_autoindex_coverage_sources_1

Full DDL and schema inspection results are saved in the safety evidence.

Implemented Coverage Core:
- contracts.py: typed metadata, source scope, partitions, inventory/page, details,
  checkpoint and terminal evidence; SourceAdapter Protocol with inspectable
  capabilities, limits and canonical hints.
- fixture.py: deterministic streaming local JSONL adapter; multiple pages,
  duplicate records, detail failures, terminal evidence; content fingerprint pins
  the adapter version. Three runnable fixtures exercise success, cross-source
  duplication and failure.
- storage.py: explicit paths, read-only inspection, foreign keys, scratch-only
  migration and existing production_write_guard integration.
- service.py: source/coverage planning and registration, durable inventory before
  details, transactional checkpoints/counters, bounded detail processing, restart,
  error resolution, import, terminal receipt publication and process-safe run lock.
- normalization.py: existing JobRecord/JobORM boundary and existing text/date
  helpers; source evidence preserved as hashed artifacts, no scorer invocation.
- dedupe.py: strong identifier reuse, explicit ATS tenant scoping, conflicting
  identities and descriptive similarity sent to persistent review candidates.
  Company/title equality never automatically merges vacancies.
- importer.py: one guard, transaction, commit and receipt per pending bulk import;
  rollback covers canonical jobs, provenance, outbox and counters. New canonical IDs
  enter a unique needs-scoring outbox. Reuse never changes scores or review state.
- Provenance is unique per source/record and many-to-one with canonical jobs.
  Existing job_sources also receives compatible appearances.
- artifacts.py: deterministic UTF-8 JSON, SHA256, atomic publication, per-run paths.
  Receipts include checkpoint evidence, counters, errors, boundary classifications,
  page references and hashed bounded record manifests.
- Exhaustion requires explicit adapter boundaries with no unresolved errors,
  detail work or ambiguous review records. Ordinary failures produce partial.
  Blocked/manual_only/unavailable require explicit supporting evidence.
- __main__.py: init-scratch, register-source, list-sources, status, fixture start,
  resume, run/checkpoint inspection, receipt and needs-scoring. Dry-run does not
  acquire or write. DB paths are required; CLI cannot opt into production mutation.

Operational examples and adapter extension guidance:
[TN0_COVERAGE_CORE.md](TN0_COVERAGE_CORE.md).

Commands/checks and exact results:
- `.\.venv\Scripts\python.exe -m compileall -q src/coverage`: exit 0.
- `.\.venv\Scripts\python.exe -m pytest tests/test_coverage_core.py -q -ra`:
  **25 passed**, exit 0.
- `.\.venv\Scripts\python.exe -m pytest -ra -o addopts=''`:
  **176 passed, 3 failed, 1 warning in 24.15s**, exit 1.
- The three failures were independently rerun without any Coverage Core test:
  `.\.venv\Scripts\python.exe -m pytest tests/test_application_assistant_api.py::test_state_contract tests/test_ui_information_architecture.py::test_applications_has_empty_state_destination_and_board tests/test_ui_information_architecture.py::test_v043_ascii_empty_placeholders_only -q -o addopts=''`:
  **3 failed, 1 warning in 1.64s**, exit 1.
- These failures are in untouched code: application transitions include applied
  where an older assertion excludes it; existing UI lacks NO ACTIVE APPLICATIONS;
  existing ui/app.js contains replacement characters. Frozen UI/assistant behavior
  was intentionally preserved. The warning is the existing FastAPI/Starlette httpx
  TestClient deprecation.
- CLI smoke: **12 commands, all exit 0**, using a disposable DB and temp artifact
  root. Verified new=2 with duplicate inventory, restart from page 2, receipt,
  outbox, cross-source reuse=1/new=0, and detail failure=partial.
  Exact subprocess argument lists are in [TN0_CLI_SMOKE.json](TN0_CLI_SMOKE.json).
- `git diff --check`: exit 0; Git printed existing LF/CRLF conversion notices.
- Scratch schema inventory and `PRAGMA foreign_key_check`: no foreign-key violations.
- Read-only production acquisition inspection: frozen Batches001â€“004 each remain
  100 records. No migrations, rebuilds, imports or scoring touched those batches.
- SHA256 checks: all six protected files match their baseline bytes.

SHA256 before/after (each value below is identical before and after):

- C:\Users\tarek\Projects\TarekJobs\data\jobs.db
  407864E84CD88FB16E9771F85084F3F40EE0AD49C2FC73172836048599B32AE9

- C:\TTEA\repo\src\ttea\jobs\hunting\academic_positions_scoring.py
  CA40C70D4BBDDD6CCDF0645BF6298706F2D962F61E3E2E5D74EDF75EE9069FD9

- C:\TTEA\repo\examples\jobs\tarek_profile.json
  DB3CAC20ED366F8B09E19C4D17B86950FC640FBE3954BEC855DCA6F357C05706

- C:\Users\tarek\Projects\TarekJobs\src\production_write_guard.py
  CD20BCB01E71910A4F10340AC546A1F6A685FB94E0DFD43561BB9A8316892BE7

- C:\Users\tarek\Projects\TarekJobs\src\ui_server.py
  6D8D0AEB34254EE5A3935BCD17D59AEFBADC50EBCE1E7E4508C02E62AE65E085

- C:\Users\tarek\Projects\TarekJobs\extensions\tarekjobs_application_assistant\README.md
  8E8F76AC4E7961CCFDE053AF087BFDE2D653ED3C1A12F49F74EDED31078EB743

Confirmation:

```text
PRODUCTION_WRITES=0
REAL_WEB_REQUESTS=0
MODEL_CALLS=0
```

No runtime AI/model call, real source acquisition or suitability scoring occurred.
Migration 004 was not applied to production.

Unresolved concerns: the three existing regression failures above remain.
No TN-0 implementation blocker is known. Persistent ambiguous dedupe decisions
deliberately await future explicit adjudication; they are never auto-merged.
No TN-1 work was begun. The implementation is ready for the architect/orchestrator's
independent certification.
