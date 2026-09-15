from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from src.ui_server import app


def aware(value: str):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


client = TestClient(app)

health = client.get("/api/health")
health.raise_for_status()
assert health.json()["version"] == "0.4.4"

jobs_response = client.get("/api/jobs?page_size=100&freshness=all")
jobs_response.raise_for_status()
jobs = jobs_response.json()["items"]

review_response = client.get(
    "/api/jobs?page_size=100&review=unreviewed&verdict=reviewable&freshness=live"
)
review_response.raise_for_status()
reviewable = review_response.json()["items"]

assert all(job["verdict"] != "blocked" for job in reviewable)

future_limit = datetime.now(timezone.utc) + timedelta(days=1)
for job in jobs:
    if job.get("posted_at"):
        assert aware(job["posted_at"]) <= future_limit, job

office = next(
    (
        job for job in jobs
        if job["title"] == "Remote Office Assistant"
        and "Coalition Technologies" in job["company"]
    ),
    None,
)
assert office is not None, "Coalition Technologies Remote Office Assistant not found"
assert (office.get("posted_at") or "").startswith("2026-08-11"), office
assert office.get("seniority") != "intern", office
assert office.get("employment_type") == "full-time", office
assert office["verdict"] == "blocked", office

flash = next((job for job in jobs if job["company"] == "FLASH PHONE"), None)
assert flash is not None
assert (flash.get("posted_at") or "").startswith("2026-08-11"), flash

leasing = next(
    (
        job for job in jobs
        if job["company"] == "TUNISIE LEASING ET FACTORING"
    ),
    None,
)
assert leasing is not None
assert (leasing.get("posted_at") or "").startswith("2026-08-13"), leasing

print()
print(
    "V0.4.4 DATA-INTEGRITY SMOKE OK | "
    f"jobs={len(jobs)} review_queue={len(reviewable)}"
)
