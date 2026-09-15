from __future__ import annotations

import sys
from pathlib import Path

# When Python directly executes a file inside scripts/, sys.path[0] points to
# scripts/ rather than the project root. Explicitly insert the project root
# so `import src...` works no matter where PowerShell was launched from.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
project_root_text = str(PROJECT_ROOT)
if project_root_text not in sys.path:
    sys.path.insert(0, project_root_text)

from fastapi.testclient import TestClient
from src.ui_server import app

client = TestClient(app)

checks = [
    ("/", 200),
    ("/assets/styles.css", 200),
    ("/assets/app.js", 200),
    ("/api/health", 200),
    ("/api/overview", 200),
    ("/api/jobs?page_size=5", 200),
    ("/api/sources", 200),
    ("/api/profile", 200),
    ("/api/run/status", 200),
    ("/api/run/history", 200),
]

for path, expected in checks:
    response = client.get(path)
    if response.status_code != expected:
        raise RuntimeError(
            f"{path}: expected HTTP {expected}, got "
            f"{response.status_code}: {response.text[:500]}"
        )
    print(f"OK  {response.status_code}  {path}")

overview_response = client.get("/api/overview")
overview_response.raise_for_status()
overview = overview_response.json()
metrics = overview.get("metrics", {})

print()
print(
    "API OK | "
    f"total={metrics.get('total_jobs', 0)} "
    f"live={metrics.get('live_jobs', 0)} "
    f"strong={metrics.get('strong_matches', 0)} "
    f"active_pipeline={metrics.get('active_pipeline', 0)}"
)