# TTEA

T/TEA provides a local job dashboard, manual Application Assistant, immutable
canonical batch evidence, coverage tracking, and read-only HUNT observation.

The current active job pool is admitted exclusively by persisted new-AI KEEP
decisions. ELIMINATE records remain accessible through application history;
a missing decision table never enables an unrestricted active pool.

See [the current repository guide](docs/REPOSITORY_GUIDE.md) for architecture,
explicit migration dependencies, offline verification, and local-only artifacts.
The [Application Assistant extension](extensions/ttea_application_assistant/README.md)
uses the loopback API. Analytics migration 003 is activated separately and is
never applied automatically at startup.

Use the project virtual environment for dependencies and tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

Tests use scratch databases and fixture/mock acquisition. Do not use legacy
collector, migration/rescore, or browser-capture commands as release verification.

## Historical release notes

The version-specific notes below describe earlier releases. The current repository
guide and committed source supersede their workflow and schema descriptions.

Personal job-market aggregation and matching prototype.

## V0.1 scope

The first dry run deliberately uses only a few small, low-friction collectors:

- Jobicy public remote-jobs API
- Remotive public remote-jobs API
- Arbeitnow public job-board API
- Keejob public HTML category pages, only when robots.txt allows the request

The pipeline:

1. fetches a maximum of 100 raw listings;
2. stores raw snapshots under `data/raw/`;
3. normalizes each source into one `JobRecord` model;
4. scores technical, eligibility, location and experience fit separately;
5. deduplicates canonical jobs while preserving every source URL;
6. stores all jobs in SQLite, including poor matches;
7. marks postings older than 45 days as stale when a publication date exists;
8. exports ranked live jobs to `data/exports/top_matches.csv`.

No API keys, paid AI model, account logins, CAPTCHA bypass, Playwright, or automated job applications are used in this first version.

## Run again

Open PowerShell in the project folder:

```powershell
.\.venv\Scripts\python.exe -m src.main dry-run --limit 100
```

Normal runs respect each source's configured minimum poll interval.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Database

SQLite database: `data/jobs.db`

Main tables:
- `jobs`
- `job_sources`

Every job begins with `application_status = discovered`.

## Next development phase

After this smoke test is stable, add source families separately:
- Tunisia: TanitJobs, Fabskill, company career sites and additional local boards.
- ATS: Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Teamtailor, Workday.
- Discovery: search-engine discovery and company-domain enumeration.
- JavaScript pages: Playwright, only where needed and without CAPTCHA/access-control bypass.
- UI: local/desktop dashboard first, remotely accessible dashboard after the data layer is stable.
## Visual dashboard

V0.2 adds a local FastAPI dashboard backed directly by `data/jobs.db`.

Launch it like a desktop app:

```powershell
.\scripts\start_dashboard_app.ps1
```

Or keep it in a normal browser:

```powershell
.\scripts\start_dashboard.ps1
```

Local URL: `http://127.0.0.1:8765`

The dashboard provides:
- live market metrics and top matches;
- searchable/filterable job inventory;
- job detail view with technical/eligibility/location/experience score breakdown;
- application status, notes and CV-version editing;
- application board;
- collector/source coverage and cooldown status;
- UI-launched collection runs with source-by-source progress and live logs;
- run history;
- read-only profile and ranking configuration overview.

`start_dashboard_lan.ps1` exposes the same dashboard on the local network. It has no authentication, so it must not be port-forwarded or exposed directly to the public internet.