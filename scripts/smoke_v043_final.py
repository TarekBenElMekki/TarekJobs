from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from src.ui_server import app


def as_aware_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


client = TestClient(app)

paths = [
    "/api/health",
    "/api/overview",
    "/api/jobs?page_size=100&freshness=all",
    "/api/jobs?page_size=100&review=unreviewed&verdict=reviewable&freshness=live",
    "/api/applications",
    "/api/sources",
    "/api/profile",
    "/api/run/status",
    "/api/run/history",
]

payloads = {}

for path in paths:
    response = client.get(path)
    if response.status_code != 200:
        raise RuntimeError(
            f"{path} -> HTTP {response.status_code}: {response.text[:500]}"
        )
    payloads[path] = response.json()
    print(f"OK  {response.status_code}  {path}")

health = payloads["/api/health"]
assert health["version"] == "0.4.3", health

all_jobs = payloads["/api/jobs?page_size=100&freshness=all"]["items"]
reviewable = payloads[
    "/api/jobs?page_size=100&review=unreviewed&verdict=reviewable&freshness=live"
]["items"]

assert all(job["verdict"] != "blocked" for job in reviewable), reviewable

now = datetime.now(timezone.utc)
future_limit = now + timedelta(days=1)

flash_phone = None
leasing_job = None

for job in all_jobs:
    posted_at = job.get("posted_at")
    if posted_at:
        assert as_aware_utc(posted_at) <= future_limit, job

    if job.get("company") == "FLASH PHONE":
        flash_phone = job

    if job.get("company") == "TUNISIE LEASING ET FACTORING":
        leasing_job = job

assert flash_phone is not None, "FLASH PHONE job not found"
assert (flash_phone.get("posted_at") or "").startswith("2026-08-11"), flash_phone

assert leasing_job is not None, "TUNISIE LEASING ET FACTORING job not found"
assert (leasing_job.get("posted_at") or "").startswith("2026-08-13"), leasing_job
assert leasing_job["verdict"] == "actionable", leasing_job

sources = payloads["/api/sources"]["items"]
assert len(sources) >= 20
assert all("priority" in source for source in sources)
assert any(source.get("state") == "active" for source in sources)
assert any(source.get("state") == "planned" for source in sources)

print()
print(
    "V0.4.3 FINAL SMOKE OK | "
    f"jobs={len(all_jobs)} "
    f"review_queue={len(reviewable)} "
    f"sources={len(sources)}"
)
