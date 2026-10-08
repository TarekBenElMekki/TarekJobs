# TN-1 implementation report

TN1_IMPLEMENTATION_STATUS=COMPLETE

Implementation is complete in the current TarekJobs working tree, ready for independent certification. This does not claim runtime certification. No tests, import probes, CLI smoke commands, migrations, scoring or full scans were executed. Review was limited to source/diff and hashes.

## Files added

- docs/TN1_IMPLEMENTATION_REPORT.md
- docs/TN1_KEEJOB_PORT_EVIDENCE.json
- docs/TN1_SAFETY_EVIDENCE.json
- docs/TN1_TUNISIA_NATIVE_ACQUISITION.md
- migrations/005_coverage_record_outcomes.sql
- src/coverage/adapters/__init__.py
- src/coverage/adapters/base.py
- src/coverage/adapters/http.py
- src/coverage/adapters/keejob.py
- src/coverage/adapters/keejob_parsing.py
- src/coverage/adapters/parsing.py
- src/coverage/adapters/registry.py
- src/coverage/adapters/tanitjobs.py
- src/coverage/adapters/tunisietravail.py
- src/coverage/diagnostics.py
- src/coverage/native_cli.py
- src/coverage/record_outcomes.py
- tests/fixtures/tn1/README.md
- tests/fixtures/tn1/keejob_detail.html
- tests/fixtures/tn1/keejob_final.html
- tests/fixtures/tn1/keejob_listing.html
- tests/fixtures/tn1/malformed.html
- tests/fixtures/tn1/tanit_challenge.html
- tests/fixtures/tn1/tanit_detail.html
- tests/fixtures/tn1/tanit_listing.html
- tests/fixtures/tn1/travail_archive.html
- tests/fixtures/tn1/travail_links.html
- tests/fixtures/tn1/travail_multi.html
- tests/fixtures/tn1/travail_single.html
- tests/test_coverage_native_cli.py
- tests/test_coverage_native_http.py
- tests/test_coverage_native_integration.py
- tests/test_coverage_native_parsers.py

## Files modified

- src/coverage/contracts.py
- src/coverage/dedupe.py
- src/coverage/importer.py
- src/coverage/normalization.py
- src/coverage/service.py
- src/coverage/__main__.py

These are changes relative to this batch's starting working tree. Several TN-0 files were already untracked, and unrelated UI/dependency changes already existed; those are not TN-1 changes.

## Implementation

- Shared HTTP: explicit User-Agent, source allowlist, robots rules/delay/rate, request spacing, bounded transient retries and Retry-After, timeouts, redirect bounds, response-size/content-type/charset validation, SHA256/fetch metadata and persistent safe audit events. Access restrictions and challenges stop acquisition with evidence.
- Keejob: locally ported historical deterministic HTML/URL/card/detail/next-page/challenge functions. No runtime T/TEA import. Stable numeric keys and strict observed next links integrate with TN-0 inventory, seen exclusion, versioned checkpoints, detail resume, Global Dedupe V2, provenance and outbox. One-page overlap starts at last_page - 1 without rewinding committed state. Current pagination can pass page 66. Explicit foreign roles are excluded; historical exhaustion never seeds current coverage.
- Tanitjobs: metadata, bounded diagnostics, intended deterministic listing/detail contract and access handling. Ordinary robots/listing access returned 403/challenge. No bypass was attempted. Live detail parsing remains unverified and the adapter cannot claim exhaustion on access failure.
- Tunisie Travail: recruitment archive pagination, explicit non-job/foreign classification, full detail text, application links/instructions/emails, linked campaigns and safely separable inline/structured positions. Stable children retain their parent article and nonunique URL evidence; siblings cannot merge merely because they share the article URL.
- TN-0 extension: defaulted evidence/error/fingerprint/detail-emission fields, explicit excluded/expanded outcomes, generic parent/child links, nonunique URL registry, persisted page fingerprints, overlap observations and failure references. Migration 005 is additive and required after unchanged 004; neither was applied. Existing canonical/scoring schema, storage guard and scoring logic remain unchanged.
- CLI: adapter-list, adapter-info, diagnose-source, init-native-scratch, register-source --adapter, start-source, resume-source, inventory-only and acquisition budgets. Existing fixture commands remain available. Explicit --db is mandatory; production writes require opt-in and the existing guard. Diagnostics use explicit scratch artifacts only.

## Tests and documentation

Four new test modules and eleven HTML fixtures plus fixture README were added, NOT RUN. They cover HTTP access/retry/timeout restrictions, pagination/ID/detail parsing, malformed HTML, foreign scope, application destinations, article splitting, shared URL isolation, checkpoints, overlap/resume, explicit retry, exhaustion and no false exhaustion. The synthetic 575-reference/final-75/zero-new regression is code only, not fresh collection evidence.

Operational details, limitations and unexecuted handoff commands are in TN1_TUNISIA_NATIVE_ACQUISITION.md. Lineage is recorded in TN1_KEEJOB_PORT_EVIDENCE.json. File manifests and before/after hashes are in TN1_SAFETY_EVIDENCE.json.

## Live observations and access limits

Limited pre-implementation inspection made eleven direct HTTP requests, not a full crawl. Keejob listing/page 2/detail and Tunisie Travail archive/detail/campaign examples were accessible. Snapshot counts were Keejob 850 results/57 pages and Tunisie Travail archive pagination to 615; these are observations, not fixed limits or current exhaustion proof. Tanitjobs ordinary robots/listing access returned 403/challenge.

Keejob's read-only historical session records 575 processed references in six batches, final 75, exhausted. The zero-new verification pass is confirmed by the supplied historical handoff, not rerun here. History inspected: 3fe294e, bdedaf6, 8bcb5ea plus working-tree improvements. All inspected T/TEA origin hashes remain unchanged. A fresh current scan remains necessary after independent certification.

## Before/after protected SHA256

Both columns were read from the files; all values match.

| File | Before | After |
| --- | --- | --- |
| C:\Users\tarek\Projects\TarekJobs\data\jobs.db | 407864E84CD88FB16E9771F85084F3F40EE0AD49C2FC73172836048599B32AE9 | 407864E84CD88FB16E9771F85084F3F40EE0AD49C2FC73172836048599B32AE9 |
| C:\Users\tarek\Projects\TarekJobs\src\production_write_guard.py | CD20BCB01E71910A4F10340AC546A1F6A685FB94E0DFD43561BB9A8316892BE7 | CD20BCB01E71910A4F10340AC546A1F6A685FB94E0DFD43561BB9A8316892BE7 |
| C:\Users\tarek\Projects\TarekJobs\src\ui_server.py | 6D8D0AEB34254EE5A3935BCD17D59AEFBADC50EBCE1E7E4508C02E62AE65E085 | 6D8D0AEB34254EE5A3935BCD17D59AEFBADC50EBCE1E7E4508C02E62AE65E085 |
| C:\Users\tarek\Projects\TarekJobs\extensions\tarekjobs_application_assistant\README.md | 8E8F76AC4E7961CCFDE053AF087BFDE2D653ED3C1A12F49F74EDED31078EB743 | 8E8F76AC4E7961CCFDE053AF087BFDE2D653ED3C1A12F49F74EDED31078EB743 |
| C:\TTEA\repo\src\ttea\jobs\hunting\academic_positions_scoring.py | CA40C70D4BBDDD6CCDF0645BF6298706F2D962F61E3E2E5D74EDF75EE9069FD9 | CA40C70D4BBDDD6CCDF0645BF6298706F2D962F61E3E2E5D74EDF75EE9069FD9 |
| C:\TTEA\repo\examples\jobs\tarek_profile.json | DB3CAC20ED366F8B09E19C4D17B86950FC640FBE3954BEC855DCA6F357C05706 | DB3CAC20ED366F8B09E19C4D17B86950FC640FBE3954BEC855DCA6F357C05706 |
| migrations\004_coverage_core.sql | 95998C3438AE152CAB364B5D8652532B3E49F75277183AB8419278185FA1169E | 95998C3438AE152CAB364B5D8652532B3E49F75277183AB8419278185FA1169E |

Source/diff inspection found no whitespace diagnostics in the implementation files. Git's LF/CRLF conversion notices are informational. No runtime/test result is asserted.

PRODUCTION_WRITES=0
PRODUCTION_MIGRATIONS=0
FULL_LIVE_CRAWLS=0
MODEL_CALLS=0
TESTS_RUN=0

Stopped at this implementation handoff. Production is not activated; TN-2 has not begun.
