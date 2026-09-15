from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from src.ui_server import app

client = TestClient(app)

health = client.get("/api/health")
health.raise_for_status()
assert health.json()["version"] == "0.4.3"

review = client.get(
    "/api/jobs?page_size=100&review=unreviewed&verdict=reviewable&freshness=live"
)
review.raise_for_status()
review_jobs = review.json()["items"]
assert all(job["verdict"] != "blocked" for job in review_jobs)

all_jobs = client.get("/api/jobs?page_size=100&freshness=all")
all_jobs.raise_for_status()
jobs = all_jobs.json()["items"]

now = datetime.now(timezone.utc)
for job in jobs:
    posted = job.get("posted_at")
    if posted:
        dt = datetime.fromisoformat(posted.replace("Z", "+00:00"))
        assert dt <= now + timedelta(days=1), job

    if job["title"] == "Technicien Maintenance Smartphone":
        assert job["company"] == "FLASH PHONE", job
        assert (job.get("posted_at") or "").startswith("2026-08-11"), job

    if job["title"] == "Ing\u00e9nieur infrastructure":
        assert job["company"] == "TUNISIE LEASING ET FACTORING", job
        assert (job.get("posted_at") or "").startswith("2026-08-13"), job

sources = client.get("/api/sources")
sources.raise_for_status()
assert all("priority" in row for row in sources.json()["items"])

print(
    f"V0.4.3 API OK | review_queue={len(review_jobs)} "
    f"jobs={len(jobs)} sources={len(sources.json()['items'])}"
)
