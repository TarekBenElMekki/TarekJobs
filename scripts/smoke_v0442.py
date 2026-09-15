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
assert health.json()["version"] == "0.4.4.2"

profile_response = client.get("/api/profile")
profile_response.raise_for_status()
profile_payload = profile_response.json()
assert "salary_preferences" not in profile_payload["profile"]

review = client.get(
    "/api/jobs?page_size=100&review=unreviewed&verdict=reviewable&freshness=live"
)
review.raise_for_status()
assert all(
    job["verdict"] != "blocked"
    for job in review.json()["items"]
)

print(
    "V0.4.4.2 SMOKE OK | "
    f"review_queue={len(review.json()['items'])}"
)
