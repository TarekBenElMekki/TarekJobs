from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from src.ui_server import app

client = TestClient(app)

health = client.get("/api/health")
health.raise_for_status()
assert health.json()["version"] == "0.5.0", health.json()

overview = client.get("/api/overview")
overview.raise_for_status()
metrics = overview.json()["metrics"]
assert "total_opportunities" in metrics
assert "job_opportunities" in metrics
assert "phd_opportunities" in metrics

jobs = client.get("/api/jobs?page_size=1&freshness=all")
jobs.raise_for_status()
body = jobs.json()
if body["items"]:
    item = body["items"][0]
    assert item["opportunity_type"] in {"job", "phd"}
    assert "priority" in item
    assert "eligibility_status" in item
    for key in ["technical", "experience", "education", "domain", "visa", "salary", "location", "language", "overall"]:
        assert key in item["scores"], (key, item["scores"])

# Type-scoped endpoints must remain valid even if there are no PhDs yet.
for kind in ["job", "phd"]:
    r = client.get(f"/api/jobs?opportunity_type={kind}&page_size=1&freshness=all")
    r.raise_for_status()

profile = client.get("/api/profile")
profile.raise_for_status()
assert profile.json()["settings"]["matching"]["eligibility_is_separate_from_fit"] is True

print(
    "V0.5.0 GLOBAL CORE SMOKE OK | "
    f"total={metrics['total_opportunities']} jobs={metrics['job_opportunities']} phds={metrics['phd_opportunities']}"
)
