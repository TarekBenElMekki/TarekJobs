import importlib.util
import sqlite3
import sys

from datetime import datetime, timezone
from pathlib import Path

import pytest

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src import database
from src.application_assistant import apply_migration
from src.database import JobORM


@pytest.fixture
def api(tmp_path, monkeypatch):

    db_path = (
        tmp_path
        / "jobs.db"
    )

    engine = database.get_engine(
        str(db_path)
    )

    database.init_db(
        engine
    )

    # Generic active-job routes require the mandatory new-AI gate.
    # Keep this fixture table intentionally minimal and test-local.
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """CREATE TABLE IF NOT EXISTS ttea_job_decisions (
                canonical_job_id INTEGER PRIMARY KEY,
                gate_decision TEXT NOT NULL
                    CHECK (gate_decision IN ('KEEP', 'ELIMINATE')),
                score INTEGER
            )"""
        )

    with sqlite3.connect(
        db_path
    ) as connection:

        connection.execute(
            "PRAGMA foreign_keys = ON"
        )

        apply_migration(
            connection
        )

        now = datetime.now(
            timezone.utc
        ).isoformat()

        connection.execute(
            """
            INSERT INTO document_library (
                document_key,
                kind,
                language,
                niche,
                format,
                file_path,
                sha256,
                is_generated,
                active,
                created_at
            )
            VALUES (
                'cv:EN:software',
                'cv',
                'EN',
                'software',
                'pdf',
                'C:/fixture/software.pdf',
                'aaaaaaaa',
                0,
                1,
                ?
            )
            """,
            (now,),
        )

        connection.execute(
            """
            INSERT INTO document_library (
                document_key,
                kind,
                language,
                niche,
                format,
                file_path,
                sha256,
                is_generated,
                active,
                created_at
            )
            VALUES (
                'motivation_letter:FR:general',
                'motivation_letter',
                'FR',
                'general',
                'docx',
                'C:/fixture/letter.docx',
                'bbbbbbbb',
                0,
                1,
                ?
            )
            """,
            (now,),
        )

        connection.commit()

    with Session(
        engine
    ) as session:

        job = JobORM(
            title="Software Engineer",
            company="Example Company",
            location="Tunis",
            canonical_url="https://example.test/job/1",
            norm_title="software engineer",
            norm_company="example company",
            norm_location="tunis",
        )

        session.add(
            job
        )

        session.commit()
        session.refresh(
            job
        )

        job_id = int(
            job.id
        )

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            INSERT INTO ttea_job_decisions (
                canonical_job_id,
                gate_decision,
                score
            )
            VALUES (?, 'KEEP', 0)
            """,
            (job_id,),
        )

    monkeypatch.setattr(
        database,
        "get_engine",
        lambda _path: engine,
    )

    module_name = (
        "src._application_assistant_api_test"
    )

    spec = importlib.util.spec_from_file_location(
        module_name,
        Path(__file__).parents[1]
        / "src"
        / "ui_server.py",
    )

    module = importlib.util.module_from_spec(
        spec
    )

    monkeypatch.setitem(
        sys.modules,
        module_name,
        module,
    )

    assert spec.loader is not None

    spec.loader.exec_module(
        module
    )

    with TestClient(
        module.app
    ) as client:

        yield {
            "client": client,
            "db_path": db_path,
            "job_id": job_id,
        }

    engine.dispose()


def test_state_contract(api):

    response = api[
        "client"
    ].get(
        "/api/application-assistant/states"
    )

    assert response.status_code == 200

    payload = response.json()

    assert len(
        payload["states"]
    ) == 15

    assert "awaiting_response" in payload["states"]
    assert "assessment" in payload["states"]
    assert "hired" in payload["states"]
    assert "not_interested" in payload["states"]

    assert payload[
        "transitions"
    ][
        "not_started"
    ] == [
        "application_preparing",
        "applied",
        "expired",
        "not_interested",
        "ready_to_apply",
    ]


def test_document_library_filters(api):

    client = api[
        "client"
    ]

    response = client.get(
        "/api/application-assistant/documents"
    )

    assert response.status_code == 200
    assert response.json()["total"] == 2

    response = client.get(
        "/api/application-assistant/documents?kind=cv"
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["niche"] == "software"

    response = client.get(
        "/api/application-assistant/documents?language=FR"
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["kind"] == "motivation_letter"

    response = client.get(
        "/api/application-assistant/documents?kind=invalid"
    )

    assert response.status_code == 400


def test_read_does_not_create_application(api):

    client = api[
        "client"
    ]

    job_id = api[
        "job_id"
    ]

    response = client.get(
        f"/api/jobs/{job_id}/application"
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["application"] is None
    assert payload["events"] == []
    assert payload["documents"] == []

    with sqlite3.connect(
        api["db_path"]
    ) as connection:

        assert connection.execute(
            "SELECT COUNT(*) FROM applications"
        ).fetchone()[0] == 0


def test_ensure_is_idempotent(api):

    client = api[
        "client"
    ]

    job_id = api[
        "job_id"
    ]

    first = client.post(
        f"/api/jobs/{job_id}/application",
        json={},
    )

    assert first.status_code == 200

    assert first.json()[
        "application"
    ][
        "current_state"
    ] == "not_started"

    assert len(
        first.json()["events"]
    ) == 1

    second = client.post(
        f"/api/jobs/{job_id}/application",
        json={},
    )

    assert second.status_code == 200

    assert len(
        second.json()["events"]
    ) == 1

    with sqlite3.connect(
        api["db_path"]
    ) as connection:

        assert connection.execute(
            "SELECT COUNT(*) FROM applications"
        ).fetchone()[0] == 1

        assert connection.execute(
            "SELECT COUNT(*) FROM application_events"
        ).fetchone()[0] == 1


def test_invalid_transition_is_400_and_atomic(api):

    client = api[
        "client"
    ]

    job_id = api[
        "job_id"
    ]

    # Commit the initial application first.
    ensured = client.post(
        f"/api/jobs/{job_id}/application",
        json={},
    )

    assert ensured.status_code == 200

    response = client.post(
        f"/api/jobs/{job_id}/application/transition",
        json={
            "state": "offer",
            "source": "test",
        },
    )

    assert response.status_code == 400

    current = client.get(
        f"/api/jobs/{job_id}/application"
    ).json()

    assert current[
        "application"
    ][
        "current_state"
    ] == "not_started"

    assert len(
        current["events"]
    ) == 1


def test_valid_lifecycle_and_legacy_cache(api):

    client = api[
        "client"
    ]

    job_id = api[
        "job_id"
    ]

    transitions = (
        "application_preparing",
        "ready_to_apply",
        "applied",
        "awaiting_response",
    )

    for state in transitions:

        response = client.post(
            f"/api/jobs/{job_id}/application/transition",
            json={
                "state": state,
                "source": "test",
                "note": (
                    "transition to "
                    + state
                ),
            },
        )

        assert response.status_code == 200

    final = client.get(
        f"/api/jobs/{job_id}/application"
    )

    assert final.status_code == 200

    payload = final.json()

    assert payload[
        "application"
    ][
        "current_state"
    ] == "awaiting_response"

    assert payload[
        "application"
    ][
        "submitted_at"
    ] is not None

    assert len(
        payload["events"]
    ) == 5

    assert payload[
        "events"
    ][0][
        "event_type"
    ] == "application.created"

    assert payload[
        "events"
    ][-1][
        "to_state"
    ] == "awaiting_response"

    with sqlite3.connect(
        api["db_path"]
    ) as connection:

        legacy = connection.execute(
            """
            SELECT
                application_status,
                application_date
            FROM jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()

    assert legacy[0] == "awaiting_response"
    assert legacy[1] is not None


def test_unknown_job_returns_404(api):

    client = api[
        "client"
    ]

    assert client.get(
        "/api/jobs/999999/application"
    ).status_code == 404

    assert client.post(
        "/api/jobs/999999/application",
        json={},
    ).status_code == 404

def test_eliminated_job_hidden_but_application_history_preserved(api):
    client, job_id = api["client"], api["job_id"]
    assert client.post(f"/api/jobs/{job_id}/application", json={}).status_code == 200
    with sqlite3.connect(api["db_path"]) as connection:
        connection.execute("UPDATE ttea_job_decisions SET gate_decision='ELIMINATE', score=NULL")
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert client.get("/api/jobs").json()["items"] == []
    assert client.get(f"/api/jobs/{job_id}/application").json()["application"]["job_id"] == job_id
    assert client.get("/api/application-assistant/tracker").json()["total"] == 1


def test_analytics_activation_and_session_lifecycle(api):
    from uuid import uuid4
    client = api["client"]
    assert client.get("/api/application-assistant/analytics/sessions").status_code == 503
    with sqlite3.connect(api["db_path"]) as connection:
        connection.executescript((Path(__file__).parents[1] / "migrations/003_application_analytics.sql").read_text(encoding="utf-8"))
    token = str(uuid4())
    payload = {"client_id": str(uuid4()), "session_token": token, "timezone": "Africa/Tunis"}
    assert client.post("/api/application-assistant/sessions/start", json=payload).status_code == 200
    response = client.patch(f"/api/jobs/{api['job_id']}", json={"review_status": "shortlisted"}, headers={"X-TTEA-Session-Token": token})
    assert response.status_code == 200
    report = client.get("/api/application-assistant/analytics/sessions").json()
    assert report["items"][0]["shortlisted"] == 1
    assert token not in str(report)
    assert client.get("/api/application-assistant/analytics/days?timezone=Africa/Tunis").json()["items"][0]["shortlisted"] == 1
    assert client.post(f"/api/application-assistant/sessions/{token}/end", json={"reason": "last_window_closed"}).status_code == 200
    assert client.post("/api/application-assistant/sessions/start", json=payload).json()["session"]["ended_at"] is not None
