# TN-1 Tunisia Native Acquisition

Implementation handoff, 2026-10-05. **Tests, CLI smoke checks, migrations, and full acquisitions were not run.** This is implementation evidence for independent certification, not certification or production activation.

## Architecture and general TN-0 extension

All adapters implement the existing SourceAdapter plan/inventory/detail boundary. TN-0 continues to own run identity, persistence, checkpoints, normalization, Global Dedupe V2, provenance, canonical inserts, scoring outbox and receipts. Existing scores and reviewed jobs are never rewritten by this path. There is no scorer/model invocation and no runtime T/TEA dependency.

Migration 004 is unchanged. Additive migration **005_coverage_record_outcomes.sql** is required after 004 for native acquisition. It adds only acquisition bookkeeping: explicit excluded/expanded record outcomes, parent/child relationships, nonunique document URLs, page fingerprints, overlap observations and failure-artifact references. No job/scoring columns are changed. The CLI requires the extension before native acquisition. Legacy fixture commands can still use 004 alone. The scratch initializer is the only provided migration command; it refuses production paths and aliases.

Contract additions are optional/defaulted: inventory evidence/fingerprint/error code; explicit detail disposition/emissions/evidence; and a structured acquisition failure. The service uses an optional overlap hook. Outcome parents are accounted for without becoming fabricated jobs. Pending child details and failed records remain durable. The importer retains its transactional guard and authoritative source-key provenance.

Canonical URL/source URL are strong evidence only when the document represents one vacancy. Inline positions share their actual article URL and declare that URL nonunique. These URLs are persisted in coverage_nonunique_urls and excluded from URL-based strong matching, including later appearances. Stable source keys/external IDs and tenant-scoped requisition IDs still apply. Descriptive similarity remains review-only. Because legacy job_sources enforces one row per source URL, shared-article positions use coverage_provenance rather than forcing conflicting legacy rows.

## Shared HTTP behavior

PublicHttp uses a truthful TTEACoverage/1.0 User-Agent, HTTPS source-host allowlists, no authenticated session, no retained cookies, and no environment proxy credentials. Default bounds: 10-second connect timeout, 20-second read timeout, 5-second minimum spacing, 3 attempts, 3 redirects, 2 MiB decoded response limit, 60-second maximum retry delay and a 90-second request-processing deadline. Transient network/status failures alone receive bounded retries. Retry-After accepts seconds or HTTP dates. Keejob retains immediate stop on 429 and no redirect following.

Robots is fetched per origin and cached for one hour. Longest-match Allow/Disallow rules support wildcard and end-anchor semantics; specific User-Agent groups, Crawl-delay and Request-rate are respected. Missing robots (404/410) is recorded explicitly; inaccessible/redirecting robots stops acquisition. Excessive crawl delay requires a later manual schedule. Apply/login routes are never requested by adapters.

401/403, exhausted or unserviceable 429 and robots restrictions produce blocked evidence. Challenge/human-verification pages stop at manual_only. Timeouts, bad charset/content type, oversized responses, malformed layout, pagination jumps/cycles/repeated content and unexpected detail identity remain unresolved failures, never exhaustion. Redirect destinations must remain permitted and robots-allowed.

Fetch artifacts preserve decoded response text plus charset, byte count, SHA256, timestamps, status, requested/final URL, attempts, optional ETag/Last-Modified and robots evidence. HTTP audit JSONL contains fixed event metadata, never response bodies, credentials, cookies or application emails. HTTP is injectable; test fixtures use MockTransport or RecordedHttp only.

## Sources and public observations

Inspected before implementation using ordinary public HTTP and limited public-page lookup. Eleven direct, bounded inspection requests were made; no full scan or bypass was attempted. Page totals are snapshots, never hardcoded limits.

| Adapter | Source ID | Version | Inventory route and observation |
| --- | --- | --- | --- |
| tanitjobs | tanitjobs_tn | tn1.1 | https://www.tanitjobs.com/jobs/ â€” direct robots/listing requests returned 403/challenge |
| keejob | keejob_tn | tn1.2-port | https://www.keejob.com/offres-emploi/ â€” public listing/detail/page 2 accessible |
| tunisietravail | tunisietravail_tn | tn1.1 | https://www.tunisietravail.net/category/offres-d-emploi-et-recrutement/ â€” archive/details accessible |

Provider metadata reports observed filter availability, but this batch intentionally plans one broad tunisia-all partition; narrower work-mode/region scopes are rejected. It does not multiply city/category searches or assume keywords define complete inventory.

### Tanitjobs

Ordinary access returned Cloudflare-style 403/challenge for listing and robots. No session forgery, CAPTCHA solution, browser automation or alternative private endpoint was attempted. Diagnostics and normal acquisition persist that access result and cannot claim exhaustion.

Public indexed evidence supports numeric /job/<id>/<slug>/ links and /jobs/?action=search&page=N&searchId=... pagination. The adapter preserves semantic listing fields, structured JobPosting and detail-description fallbacks, explicit application destinations and stable numeric keys. The intended parser contract has synthetic offline fixtures. **Current live detail parsing was not verified because ordinary access was blocked.** No current result total or complete Tanitjobs inventory is claimed.

### Keejob: local port, not a new collector

Read-only origins:
- C:\TTEA\repo\src\ttea\jobs\sources\keejob.py
- C:\TTEA\repo\src\ttea\jobs\keejob_batches.py
- C:\TTEA\repo\tests\test_keejob_pagination.py
- C:\TTEA\repo\tests\test_jobs_keejob_batches.py
- C:\TTEA\repo\data\keejob_baseline\session.json

Relevant history: 3fe294e, bdedaf6, 8bcb5ea, plus the inspected working-tree improvements. The baseline session records exhausted, 575 processed references in six batches (five 100s, final 75), 507 detail requests, 68 listing requests and 577 total source requests. The supplied historical handoff also confirms a subsequent zero-new verification pass. That pass is user-provided historical evidence; no new pass was run here.

keejob_parsing.py locally ports the pure HTML parser, public URL validation, numeric-ID discovery, heading-ranked card selection, single-offer container isolation, company links, detail extraction, challenge recognition and strict immediate-next links. Documented adaptations allow Unicode/encoded slugs, bound pure page parsing at 1000 cards and retain unknown companies. Neither T/TEA profile-based triage/scoring nor its session persistence was imported.

KeejobAdapter maps the ported records into TN-0. TN-0 inventory uniqueness is the durable seen set; successful details are not repeated on resume. For unfinished inventory, the optional overlap hook revisits max(1,last_page-1) through last_page, stores observations separately, stages only newly discovered keys and then follows the committed next cursor. The hook does not rewind checkpoints or duplicate successful details. The ordinary page budget counts newly checkpointed pages; overlap may add at most two listing fetches per resume invocation.

The inspected broad listing reported 850 results, approximately 15 per page and 57 pages; page 2 declared page 3. Those observations do not cap future scans. Strict observed pagination can advance beyond historical page 66 (bounded public URL validation permits page numbers through 10000; reaching a rejected link is a failure, not exhaustion). Reported counts, unique discovered IDs, final next-link disappearance and page fingerprints support exhaustion evidence. A moving result total is recorded; a final unique-count deficit prevents exhaustion.

Current details expose JobPosting JSON-LD, full .prose text, h3/value fields, salary, education, experience and account-required /postuler/ links. Apply URLs are preserved but never fetched. The broad listing also exposes foreign vacancies, so explicit non-Tunisia country/location evidence is classified as excluded at detail acquisition. Unknown geography is preserved.

Keejob robots permits the inspected public listing/detail routes while restricting API, login, applications, recruiter/control/private routes and paginate_by queries. No restricted route was fetched.

**Historical exhaustion is baseline evidence only. No 575-reference historical set is preseeded. A fresh current Keejob scan remains required after certification.**

### Tunisie Travail

The inspected WordPress-style recruitment archive uses article/h2 links and page-number/next navigation; the visible last archive page was 615. Month-only labels such as Oct,2026 are retained as raw evidence and never converted into invented full dates.

Details use .PostContent; full article text takes precedence over short structured descriptions. Explicit employer patterns, dates, labelled requirements, country/categories and application links/instructions/emails are retained. A literal onclick navigation URL is extracted without executing JavaScript. No application URL is invented: external apply, native apply, then source-detail fallback. Account requirements remain metadata.

Immigration/advice/news/competition content and explicit foreign-only opportunities are excluded with evidence. Unknown geography remains unknown. Campaign blocks explicitly labelled Postes queue their individually identified linked articles for detail acquisition. Multiple structured JobPosting entries or complete, explicitly labelled Poste/Position headings emit distinct child records; generic headings, bold text and slash-separated titles do not justify splitting. Unsafe segmentation preserves the article-level posting.

Inline keys use article ID plus a stable digest of normalized title/location, independent of section order. Children retain the parent article relationship and nonunique URL marker. Duplicate ambiguous inline signatures fail safely. Structured children do not inherit another child's country/salary/employer; foreign children are excluded with evidence. Changes to title/location may yield new keys and conservative review; no fuzzy source-identity guessing is introduced.

## Exhaustion and recovery

Inventory and details are separate. --inventory-only freezes acquired inventory with max_details=0. Resume skips completed inventory/details; explicitly failed details need --retry-failed. Page error artifacts retain evidence; final receipts include fingerprints through stored pages, boundary evidence, outcome relationships, overlap observations, failure references and record manifests.

Natural pagination boundaries certify inventory only after count/unique-ID checks. Missing required detail/import state or ambiguous dedupe prevents run exhaustion. An inventory access stop after earlier pages preserves the acquired records and terminal access status even when details remain pending. The receipt records unresolved records and never claims exhaustion. A completed blocked/manual checkpoint is not silently reopened by resume; a later explicit fresh run can re-check legitimately changed access. Detail-only access failures remain partial and require explicit failed-detail retry.

No disappearance deletes jobs or aggressively expires them. Explicit closed/expired source messages are retained; successful details record verified_live, and 404/410 records missing_from_source failure evidence.

## CLI handoff examples â€” NOT EXECUTED

Use the project .venv only. Every command requires an explicit --db; no typo falls back to jobs.db. Commands below are for a later independently authorized scratch certification:

~~~powershell
.\.venv\Scripts\python.exe -m src.coverage --db C:\scratch\tn1.db adapter-list
.\.venv\Scripts\python.exe -m src.coverage --db C:\scratch\tn1.db adapter-info --adapter keejob
.\.venv\Scripts\python.exe -m src.coverage --db C:\scratch\tn1.db diagnose-source --adapter keejob --artifacts C:\scratch\tn1-diagnostic
.\.venv\Scripts\python.exe -m src.coverage --db C:\scratch\tn1.db init-native-scratch
.\.venv\Scripts\python.exe -m src.coverage --db C:\scratch\tn1.db register-source --adapter keejob
.\.venv\Scripts\python.exe -m src.coverage --db C:\scratch\tn1.db start-source --adapter keejob --artifacts C:\scratch\tn1-artifacts --dry-run
.\.venv\Scripts\python.exe -m src.coverage --db C:\scratch\tn1.db start-source --adapter keejob --artifacts C:\scratch\tn1-artifacts --inventory-only --max-pages 2
.\.venv\Scripts\python.exe -m src.coverage --db C:\scratch\tn1.db resume-source RUN_ID --adapter keejob --inventory-only
.\.venv\Scripts\python.exe -m src.coverage --db C:\scratch\tn1.db resume-source RUN_ID --adapter keejob --max-details 100
.\.venv\Scripts\python.exe -m src.coverage --db C:\scratch\tn1.db resume-source RUN_ID --adapter keejob --retry-failed
.\.venv\Scripts\python.exe -m src.coverage --db C:\scratch\tn1.db receipt RUN_ID
~~~

Adapter keys: tanitjobs, keejob, tunisietravail. Metadata/dry-run commands make no requests. Diagnostics request one listing and at most one sample detail (plus bounded robots/retry/redirect requests), write only to the supplied artifact root, and do not initialize/write a DB. Production DB targets and production data artifact directories are rejected for diagnostics. Native register/start/resume require explicit --allow-production for any future production mutation and still use the existing write guard; this implementation did not use that option. There is no production migration command.

## Test source handoff â€” NOT RUN

- test_coverage_native_http.py: access status, challenges, Retry-After, timeouts, bounded retries, spacing, charset/type/size, redirect guards and robots.
- test_coverage_native_parsers.py: ported Keejob pagination/IDs/details, >66 pagination, scope, salary/application extraction, Tanit contract, archive dates, multi-position/linked campaign parsing and malformed input.
- test_coverage_native_integration.py: disposable 004+005 setup, inventory/detail separation, overlap/seen exclusion, resume/version pinning, failures/no false exhaustion, shared URL canonical isolation, retries and synthetic 575/final-75/zero-new shape.
- test_coverage_native_cli.py: required DB, invalid keys, metadata/dry-run no acquisition and production diagnostic refusal.
- tests/fixtures/tn1/: sanitized synthetic HTML, not a recorded current crawl.

Existing TN-0 tests are unchanged. No pytest, CLI smoke check, source import probe or migration was executed to validate the implementation. Final review is limited to source/diff and hash inspection. Independent certification must assess the new general extension and current provider access before activation.

## Safety evidence

TN1_SAFETY_EVIDENCE.json contains pre/post protected-file SHA256 values and TN-0 file hashes. TN1_KEEJOB_PORT_EVIDENCE.json records read-only origin hashes/history/baseline. TN1_IMPLEMENTATION_REPORT.md is the final handoff.

PRODUCTION_WRITES=0
PRODUCTION_MIGRATIONS=0
FULL_LIVE_CRAWLS=0
MODEL_CALLS=0
TESTS_RUN=0
