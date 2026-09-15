from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from src.ui_server import app

c = TestClient(app)

checks = [
    "/api/health",
    "/api/overview",
    "/api/jobs?page_size=100&freshness=all",
    "/api/jobs?page_size=20&review=unreviewed",
    "/api/jobs?page_size=20&verdict=blocked",
    "/api/applications",
    "/api/sources",
    "/api/profile",
    "/api/run/status",
    "/api/run/history",
]

payloads = {}
for path in checks:
    r = c.get(path)
    if r.status_code != 200:
        raise RuntimeError(f"{path} -> {r.status_code}: {r.text[:500]}")
    payloads[path] = r.json()
    print(f"OK  {r.status_code}  {path}")

health = payloads["/api/health"]
assert health["version"] == "0.4.1", health

sources = payloads["/api/sources"]["items"]
assert len(sources) >= 20
assert any(x.get("state") == "active" for x in sources)
assert any(x.get("state") == "planned" for x in sources)

jobs = payloads["/api/jobs?page_size=100&freshness=all"]["items"]

for job in jobs:
    location = (job.get("location") or "").lower()
    company = (job.get("company") or "").lower()

    if "sousse" in location or "zaghouan" in location:
        assert job["verdict"] == "blocked", job
        assert job["scores"]["location"] == 0, job

    assert company not in {
        "informatique / tÃ©lÃ©coms",
        "informatique / telecoms",
        "banque / finance / assurances",
    }, job

print()
print(
    f"V0.4.1 API OK | version={health['version']} "
    f"sources={len(sources)} jobs_checked={len(jobs)}"
)