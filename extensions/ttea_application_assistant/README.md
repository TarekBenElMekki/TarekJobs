# TTEA Application Assistant V1.3.1

A compact Chrome side panel for manually reviewing one TTEA job at a time.
TTEA remains the source of truth; the extension keeps only an in-memory queue.

## Load unpacked

1. Use Chrome 114 or later and open `chrome://extensions`.
2. Enable **Developer mode** and choose **Load unpacked**.
3. Select `extensions/ttea_application_assistant` in this repository.
4. Click the extension toolbar icon to open the side panel.

The local TTEA server must be running at `http://127.0.0.1:8765`.
The existing Application Assistant foundation must already be installed. This
extension does not create or apply migrations. `API_BASE` in `sidepanel.js` is the
single API setting; localhost:8765 is also allowed by the manifest.
If the server is unreachable, start it yourself and choose RETRY.

## Review flow

Loading or REFRESH QUEUE retrieves all pages of `/api/jobs` using `page_size=100`,
`review=unreviewed`, `status=not_started`, `verdict=reviewable`, and `freshness=live`.
The existing backend defines reviewable as non-blocked and live as non-stale.
The queue is then a stable in-memory snapshot. Concurrent changes elsewhere are
not live-synchronized; refresh manually to reload. Closing/reloading the panel
may discard the snapshot. Position counts the remaining jobs.

PREVIOUS and NEXT only navigate that snapshot. OPEN JOB navigates the **active
current tab in the panel's window** to the canonical URL; it never creates a tab
or changes a status. The side panel remains available. No page content is read.
The sidePanel, tabs, storage and alarms permissions and the two loopback API host grants are requested;
tabs enables explicit URL capture after cross-site browsing without broad website host access.

Successful status actions remove the current card and show the next remaining
card (or the previous remaining card when completing the last position). When
none remain, QUEUE COMPLETE appears. Failed actions retain the card and show an
inline error. Buttons are locked during requests. Requests time out after 15
seconds; a timed-out write may have committed, so retry the same action or refresh.
HTTP/schema errors are distinguished from an unreachable server. No automatic
write retries occur.

## Actions

| Action | Backend operation |
| --- | --- |
| APPLIED | Normalized transition directly to `applied`; records today's submission time if absent. |
| ALREADY APPLIED | Normalized `applied` transition with `historical_application: true` and `application_date_unknown: true`; no inferred submission date. |
| NOT MATCHING | Legacy job PATCH: `review_status=dismissed`; no application creation. |
| NOT ELIGIBLE (MANUAL) | Legacy job PATCH: `review_status=not_eligible`; no application creation or lifecycle/scoring changes. |
| SAVE FOR LATER | Legacy job PATCH: `review_status=shortlisted`; no application creation. |
| JOB EXPIRED | Normalized transition to `expired`. |
| NOT INTERESTED | Normalized transition to `not_interested`. |

Normalized transitions ensure an application within the existing API transaction
and persist events with source `chrome_sidepanel_v1`. Historical reports preserve
any existing known dates; when absent, `submitted_at` and legacy
`application_date` remain null. An unknown historical start time is also not
invented. Event/activity timestamps still record when the report was received.
Both historical flags must be boolean true and are supported only for `applied`.
Existing application states remain available to other clients. A repeated
transition to the same state remains the backend's existing no-op.

## Manual application link catcher

After browsing manually to the employer/ATS/application page you want to keep,
click **SAVE CURRENT PAGE** below OPEN JOB. This is the only action that reads the
active tab URL, and it uses the active tab in the side panel's window. A saved
link is shown compactly and the button becomes **REPLACE WITH CURRENT PAGE**.
The full saved URL is available in the text tooltip. Saving does not advance the
queue or change review/application status. All existing queue filters are unchanged.

The panel loads the existing read-only application bundle when a card first
needs its link, caches it in memory, and clears the cache on REFRESH QUEUE.
Viewing a card never creates an application. Stale asynchronous results cannot
overwrite another card's link. Reload this unpacked extension after upgrading
and accept the new tabs permission if Chrome prompts.

`PATCH /api/jobs/{job_id}/application/link` accepts
`{"url":"https://example.com/jobs/123/apply","source":"chrome_sidepanel_v1"}`
and returns `{"application": ...}` including the backend-normalized URL.
The service validates before ensuring an application, saves the URL and activity
timestamps, and appends an immutable `application.link_saved` or
`application.link_replaced` event in the same transaction. Metadata contains only
source, previous_url and application_url. It leaves lifecycle and review state
unchanged. Invalid URLs return HTTP 400; unknown jobs return HTTP 404.

Normalization is deterministic and performs no HTTP requests or redirects:

- Accept absolute HTTP/HTTPS URLs only; reject credentials, invalid ports,
  missing hosts, embedded whitespace/control characters and backslashes.
- Trim surrounding whitespace, lowercase scheme/hostname, omit default ports
  (HTTP 80 / HTTPS 443), and retain non-default ports and the path except the explicit referral-tail rule below.
- Remove query keys beginning with `utm_` and exact keys `fbclid`, `gclid`,
  `msclkid`, `li_fat_id`, case-insensitively after decoding the key.
- Preserve all other query components verbatim, including order, duplicates,
  encoding, job IDs, language, tenant and routing information. Remove empty
  separators; retain unknown empty-valued parameters and bare flags because
  they may have routing semantics.
- Preserve all nonempty fragments conservatively, including hash-based SPA
  routes. Omit empty query/fragment delimiters.

No DOM, HTML, forms, page titles, unrelated tabs or browsing history are read or
stored. There are no content scripts, script injection, page detection, ATS
analysis, application-site requests or model calls. Links may retain meaningful
routing tokens: normalization deliberately does not guess which unknown keys
are safe to remove.

## V1.2 modes and follow-up

The panel opens in REVIEW. Its existing queue query and all previous actions
remain unchanged. NOT ELIGIBLE (MANUAL) records the user's review decision as
`review_status=not_eligible`, removes that card and advances. It does not create
an application, change lifecycle status or alter any eligibility/suitability score.
The source schema defines review_status as unconstrained VARCHAR(40); no migration
is introduced.

FOLLOW-UP loads `GET /api/application-assistant/followups`, a read-only join of
normalized applications to jobs, ordered by latest application activity descending
(with application ID as the tie-breaker). It includes all active normalized states:
`applied`, `awaiting_response`, `assessment`, `interview`, `technical_test`,
`hr_interview`, and `offer`. It does not use the review queue filters or legacy
application status to decide membership. A saved link alone does not qualify.
`hired`, `rejected`, `withdrawn`, `expired`, and `not_interested` are inactive;
`not_started`, `application_preparing`, and `ready_to_apply` are excluded too.

The card shows CURRENT STATUS and the submitted date, or explicitly states that
the date is unknown. Status buttons are the intersection of the live
`GET /api/application-assistant/states` transition mapping and these supported
labels: AWAITING RESPONSE, ASSESSMENT, INTERVIEW, TECHNICAL TEST, HR INTERVIEW,
OFFER, HIRED, REJECTED, WITHDRAWN. No permissive client transition table is used.
JOB EXPIRED remains a REVIEW action.

Updates use the existing normalized transition endpoint and source
`chrome_sidepanel_v1`, preserving lifecycle validation and immutable events.
An active result updates the current card in place and refreshes its buttons;
a terminal result removes it and advances. No full queue reload occurs on status
updates. OPEN APPLICATION uses the saved application URL in the current tab;
without a saved link, OPEN JOB uses the canonical URL. Manual link saving also
remains available. Navigation and mode switching do not mutate backend state.
Switching modes clears errors, shows loading and retrieves the selected queue.
Mode and queue state are in memory only; REFRESH QUEUE reloads the selected mode.

### Conservative referral-tail cleanup

Only a final literal segment from `linkedin`, `indeed`, `glassdoor`, `facebook`,
or `google` immediately following a literal `apply` segment is stripped, with
case-insensitive comparisons. For example `/apply/LinkedIn` becomes `/apply`.
`/jobs/123/LinkedIn`, `/apply/company-specific-token`, `/apply/12345`, encoded
segments and trailing-slash variants remain unchanged. Query normalization and
fragment preservation otherwise retain their existing behavior. This applies
when a link is saved, not retroactively to stored URLs. There is no Workday-specific
logic, redirect following, website request, page analysis or GPT/model use.

V1.3 adds storage and alarms permissions; website host grants remain unchanged.
The existing application schema and local API are prerequisites. No production
data or historical events were read, rewritten or migrated during implementation.
## V1.3 tracker, sessions and daily analytics

### Activation and scope

Migration `migrations/003_application_analytics.sql` is supplied but **not applied**.
Apply it separately through your normal controlled migration process, after the
existing 002 foundation, before enabling analytics. Neither server startup nor a
GET request applies it. No production database was opened during implementation.
There is no historical activity/session backfill. Existing applications and their
immutable history remain valid and appear in the tracker independently of 003.

`src/application_analytics.py` isolates session accounting and reporting from
application lifecycle rules. `requirements.txt` adds `tzdata` so Python `zoneinfo`
can resolve IANA zones on Windows. Install project dependencies through `.venv`
when activating; no dependencies were installed in this implementation step.
Reload the unpacked extension to activate version 1.3.0 and its storage/alarms
permissions. The API remains loopback-only; there are no additional website hosts.

### Browser-session ownership

The service worker stores a stable random `client_id` in `chrome.storage.local`
and the current random `session_token` in `chrome.storage.session`. The latter
survives service-worker suspension but is cleared when Chrome restarts. The first
side-panel opening ensures a backend session. Closing/reopening the panel,
changing tabs, and navigating do not start another session. Separate normal
windows in the same Chrome profile share one session.

A Chrome alarm sends a heartbeat once per minute while a session exists. Closing
the last normal Chrome window is detected by `windows.onRemoved` followed by
`windows.getAll({windowTypes:['normal']})`. The worker clears its runtime token,
cancels the alarm, and requests an explicit end with `last_window_closed`.
Closing only one of several windows does not end the session.

Process kill, OS shutdown, background suspension and failed end delivery can
prevent cleanup. There is no claim that `onSuspend` guarantees delivery. When a
new token starts for the same persistent client, any unfinished predecessor is
closed at its **previous last_seen_at**, with `recovered_after_disconnect`.
Completed sessions are never reopened or rewritten. Start/end requests are
idempotent; heartbeat changes only active sessions. The worker serializes lifecycle
operations so concurrent panels/alarms do not create duplicate sessions.

The side panel shows a local elapsed timer; once-per-second display updates do
not contact the API. It checks the worker at opening, before mutations and once
per minute; the alarm owns heartbeat delivery even with the panel closed.
Session startup failures show a small warning and do not block core job actions.
No session token is sent when session registration is unavailable, so those
successful actions remain valid but are not retrospectively counted in analytics.

Only client identity, runtime session identity and at most 100 diagnostic records
(timestamp and fixed event name) are retained in extension storage. No URLs,
page content, tab lists, browsing history or application database are stored there.
Backend diagnostics use rotating `logs/application_analytics.log` without tokens,
client IDs, URLs, payloads or exception text. Activity itself is persisted in the
append-only database ledger. Service-worker/extension reloads that clear session
storage may start a new token; the server recovers its predecessor conservatively.

### API and transaction semantics

- `POST /api/application-assistant/sessions/start`: UUID client/token, IANA timezone,
  extension version; returns `{session: ...}`. Uses one open session per client.
- `POST /api/application-assistant/sessions/{token}/heartbeat`: active heartbeat.
- `POST /api/application-assistant/sessions/{token}/end`: `{reason:"last_window_closed"}`.
- `GET /api/application-assistant/tracker`: all normalized applications, with `state`,
  `search` (title/company/location), `company` as a search alias, `limit` (default 50,
  max 200), and `offset`. Returns filtered total and global current-state counts.
  Score/verdict reuse existing presentation; historical status is derived from
  recorded application-event metadata, never inferred from the current date.
- `GET /api/application-assistant/analytics/sessions?limit=30&timezone=Africa/Tunis`:
  recent sessions, explicit/open effective end, duration and action counters.
- `GET /api/application-assistant/analytics/days?days=14&timezone=Africa/Tunis`:
  recent local calendar days, duration and action counters. Maximum 366 days.

Every extension workflow mutation attempts session attribution through
`X-TTEA-Session-Token`. Successful changes record:
`review.applied`, `review.already_applied`, `review.not_matching`,
`review.shortlisted`, `review.expired`, `review.not_interested`,
`review.not_eligible`, `application.link_saved`, `application.link_replaced`,
and `followup.transition`. Follow-up metadata records from_state/to_state;
already-applied metadata preserves both historical/unknown-date flags.

Analytics inserts use a savepoint inside the existing core mutation transaction.
Missing analytics storage or inactive session attribution is isolated and logged;
core state can still commit. If core persistence rolls back, its activity rolls
back too. Repeated unchanged review states, same lifecycle states and saves of
the same normalized URL do not add activity counts. Existing link-event behavior
is preserved independently. Non-application review decisions do not create
application rows; their activity has a job ID and a null application ID.
Navigation, refreshing, rendering and mode switching never add activity events.

### Reporting and dashboard

The existing APPLICATIONS view is now a read-only normalized tracker with state
counts, status filter, text search, pagination and dates. OPEN prefers the saved
application URL, falling back to the canonical URL. Lifecycle controls remain in
the side panel. Shortlisted jobs without application records are still review
decisions and are not fabricated into tracker rows.

ANALYTICS shows today's summary, 30 recent sessions and 14 days of daily metrics.
The UI uses the browser IANA timezone with a UTC fallback when resolution fails.
All persisted timestamps are UTC. Action counts use each event's local date;
sessions_started uses the local start date. Session duration is split at local
midnight with UTC elapsed-time arithmetic, including daylight-saving changes.
Open sessions end effectively at last_seen_at, never at an unobserved current time.
No rows are manufactured before the first recorded V1.3 session.

Session time measures browser-session presence, including idle/background time,
not focused work. Concurrent sessions from different clients contribute their
own durations. Unique jobs are deduplicated per session/day, not summed from
session subtotals. APPLIED counts new applied actions separately from historical
ALREADY APPLIED. Recovered sessions may omit the unobserved interval after the
last heartbeat. Missing migration/timezone data is reported as an error rather
than silently presenting incorrect totals. Manual REFRESH reloads the reports.
## V1.3.1 automatic production pre-write guard

`src/production_write_guard.py` supplies one centralized context for production
mutations. The configured production path is derived from the project root plus
`config/settings.yaml` -> `storage.sqlite_path` (default `data/jobs.db`). The server
keeps that resolved path separate from the current engine's database target.
Only matching resolved paths trigger safety backups. An injected scratch/test or
verification database at a different path, and in-memory SQLite, do not trigger
production backups. Changing configuration requires restarting the server to
refresh its configured production identity.

Before each guarded operation, a process-wide `threading.RLock` covers this entire
interval: fresh backup -> independent verification -> rolling publication ->
write connection/transaction -> commit or rollback/close -> lock release.
The guard is entered outside best-effort analytics handling. A backup failure
therefore blocks the core write, rather than becoming a skipped analytics event.
Existing analytics savepoint behavior applies only after the guard admits a write.

Rolling files are stored at:

```text
%USERPROFILE%\TTEASafety\automatic_prewrite\jobs_prewrite_latest.db
%USERPROFILE%\TTEASafety\automatic_prewrite\jobs_prewrite_latest.json
```

If USERPROFILE is unavailable, the user's home directory is used. Each operation
creates a new temporary database in that same directory and opens production via
a read-only SQLite URI. `sqlite3.Connection.backup()` produces a consistent snapshot,
including committed WAL content; no raw database file copy is used. Connections
are closed and a separate read-only connection verifies the temporary backup:
`PRAGMA integrity_check` must return exactly `ok`, and `PRAGMA foreign_key_check`
must return zero violations. The backup is a standalone DB with DELETE journal
mode. Backup copy has a 30-second deadline to fail closed on prolonged contention.

The verified file is hashed with SHA256 and flushed before `os.replace` atomically
publishes it as the rolling DB. A temporary flushed JSON receipt is separately
published with `os.replace`. It records guard version, UTC creation time, fixed
operation reason, resolved production/backup paths, backup SHA256 and both file
sizes. The production main-file size is informational and may exclude WAL data;
the backup hash is not claimed to equal a hash of the production main file.

The old verified backup is never deleted ahead of replacement. Both publication
steps must complete before writing is admitted. These are two atomic file
replacements, not a multi-file atomic transaction: if receipt publication fails,
the new verified DB can coexist with the old receipt, but **no core write occurs**.
Do not trust a mismatched receipt; a later successful guard refreshes both files.
Temporary files are cleaned up best-effort; milestone/timestamped backups are
untouched. No permanent timestamped backup is created every heartbeat. Guard
outcomes are recorded in rotating `logs/production_write_guard.log`; reasons
contain no session tokens or browser data.

Guarded Application Assistant operations:

- Session start, every once-per-minute heartbeat, and session end.
- `PATCH /api/jobs/{job_id}` (all updates through the existing endpoint).
- `POST /api/jobs/{job_id}/application` (ensure/create).
- `PATCH /api/jobs/{job_id}/application/link`.
- `POST /api/jobs/{job_id}/application/transition`.

The server's initialization and profile-update/rescoring scope also use the same
guard. A server-launched collector holds it from its pre-run backup until the child
exits, including failure cleanup, so it cannot interleave with assistant writes.
Its backup is before the collection run as a whole. A failed collection admission
is recorded by the existing run-failure path. No scoring algorithm was changed.
No-op write requests may conservatively refresh the backup. GET endpoints and
read-only reports do not acquire the write guard or produce backups.

Request-time safety failures return HTTP 503:
`Production write blocked: verified pre-write backup unavailable.`
Startup safety failure prevents initialization rather than allowing unprotected
startup writes. Heartbeat failures leave last_seen_at unchanged; later disconnect
recovery still uses that last known timestamp. User mutations fail instead of
advancing without the required safety backup. Session lifecycle, heartbeat
frequency, historical-application semantics, links and queues are unchanged.

The RLock coordinates one server process and its managed collector, not unrelated
CLI writers or multiple server worker processes. Run one server worker and avoid
independent writers during guarded operation. Standalone maintenance/import tools
must adopt the guard (or their own coordinated safety workflow); this patch does
not modify frozen scoring/import tooling or claim cross-process exclusion.
Collection holds the lock for the full run, so concurrent write requests may wait
or time out; a timed-out request may still complete and should be reconciled by
refreshing. No server, endpoint, database or backup operation was executed during
implementation. No migration is introduced or applied by V1.3.1.
## Explicitly out of scope

No automatic application-link detection, page detection, DOM
analysis, content scripts, scraping, CV recommendation/selection, motivation
letters, application questions, form filling, email, or model/provider calls.
No frameworks, external chart dependencies, persistent extension application database, dashboard
redesign, speculative endpoints, automatic migrations, or scoring changes.

Implementation was reviewed as source only. No tests, compilation, extension
execution, Chrome launch, server launch, production API calls, database queries,
or database writes were performed for this change.
