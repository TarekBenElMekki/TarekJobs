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
assert health.json()["version"] == "0.4.5"

review = client.get(
    "/api/jobs?page_size=100&review=unreviewed&verdict=reviewable&freshness=live"
)
review.raise_for_status()
items = review.json()["items"]

titles = {item["title"] for item in items}
for blocked_title in [
    "Customer Support Team Leader - Bulgarian Language",
    "Customer Support Team Leader – Bulgarian Language",
    "Inside Sales Contractor",
    "Business Development Representative",
]:
    assert blocked_title not in titles, (blocked_title, titles)

all_jobs = client.get("/api/jobs?page_size=100&freshness=all")
all_jobs.raise_for_status()
all_items = all_jobs.json()["items"]

def find_contains(needle):
    return next(
        (
            item for item in all_items
            if needle.lower() in item["title"].lower()
        ),
        None,
    )

bulgarian = find_contains("Bulgarian Language")
if bulgarian:
    assert bulgarian["verdict"] == "blocked", bulgarian

inside_sales = find_contains("Inside Sales Contractor")
if inside_sales:
    assert inside_sales["verdict"] == "blocked", inside_sales

bdr = find_contains("Business Development Representative")
if bdr:
    assert bdr["verdict"] == "blocked", bdr

print(
    "V0.4.5 FINAL MATCHING SMOKE OK | "
    f"review_queue={len(items)}"
)
