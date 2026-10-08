import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from src import database


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = database.get_engine(str(tmp_path / "ui.db"))
    try:
        legacy_tables = {"jobs", "job_sources"}
        database.Base.metadata.create_all(
            engine, tables=[database.JobORM.__table__, database.JobSourceORM.__table__],
        )
        assert set(inspect(engine).get_table_names()) == legacy_tables

        # Inject the temporary engine before executing the private server module.
        monkeypatch.setattr(database, "get_engine", lambda _path: engine)
        name = "src._ui_api_test"
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).parents[1] / "src" / "ui_server.py",
        )
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)

        assert module.engine is engine
        assert set(inspect(engine).get_table_names()) == legacy_tables

        # Active UI queries require the mandatory new-AI gate.
        # Production retains the full provenance/FK contract.
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """CREATE TABLE ttea_job_decisions (
                    canonical_job_id INTEGER PRIMARY KEY,
                    gate_decision TEXT NOT NULL
                        CHECK (gate_decision IN ('KEEP', 'ELIMINATE')),
                    score INTEGER
                )"""
            )

        # The context manager runs normal FastAPI startup/lifespan initialization.
        with TestClient(module.app) as test_client:
            assert set(inspect(engine).get_table_names()) == legacy_tables | {
                "ttea_runs", "ttea_assessments", "ttea_job_decisions",
            }
            yield test_client
    finally:
        engine.dispose()


def test_dashboard_health(client):
    response = client.get('/api/health')
    assert response.status_code == 200
    assert response.json()['ok'] is True


def test_dashboard_overview_reads_database(client):
    response = client.get('/api/overview')
    assert response.status_code == 200
    body = response.json()
    assert 'metrics' in body
    assert 'total_jobs' in body['metrics']
    assert body['metrics']['total_jobs'] == 0
    assert 'sources' in body


def test_dashboard_jobs_endpoint(client):
    response = client.get('/api/jobs?page_size=5')
    assert response.status_code == 200
    body = response.json()
    assert 'items' in body
    assert body['items'] == []
    assert body['page_size'] == 5
