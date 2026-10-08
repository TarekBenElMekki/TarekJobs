import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from src import database


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TTEA_JOB_LIVE_ROOT", str(tmp_path / "live"))
    monkeypatch.delenv("TTEA_REPO", raising=False)
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

        # Active UI queries now require the mandatory new-AI gate.
        # This is a deliberately minimal test-only compatibility table;
        # the production table retains its full provenance/FK contract.
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


@pytest.fixture
def live_channel(tmp_path, monkeypatch):
    root = tmp_path / "live"
    directory = root / "runs/run_fixture"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TTEA_JOB_LIVE_ROOT", str(root))
    (root / "latest.json").write_text('{"run_id":"run_fixture"}', encoding="utf-8")
    (directory / "live_state.json").write_text(json.dumps({
        "run_id": "run_fixture", "status": "running", "current_stage": "cloud_scoring",
        "current_action": "Scoring", "source_name": "keejob", "source_url": "https://example.test/jobs?secret=token",
        "stages": {"cloud_scoring": {"status": "running", "current": 1, "total": 3}},
        "counters": {"cloud_scored": 1}, "api_key": "PRIVATE",
        "current_item": {"title": "Python", "score": 70, "candidate_profile": "PRIVATE"},
    }), encoding="utf-8")
    return root, directory


def test_live_unconfigured_returns_idle(client, monkeypatch):
    monkeypatch.delenv("TTEA_JOB_LIVE_ROOT", raising=False)
    monkeypatch.delenv("TTEA_REPO", raising=False)
    response = client.get('/api/ttea/live')
    assert response.status_code == 200
    assert response.json()['status'] == 'idle'
    assert response.json()['configured'] is False
    assert 'NOT CONFIGURED' in response.json()['message']


def test_live_state_is_safe_and_parsed(client, live_channel):
    response = client.get('/api/ttea/live')
    body = response.json()
    assert body['run_id'] == 'run_fixture'
    assert body['stages']['cloud_scoring']['total'] == 3
    assert body['counters']['cloud_scored'] == 1
    assert body['current_item']['title'] == 'Python'
    assert 'PRIVATE' not in response.text
    assert 'secret=' not in response.text


@pytest.mark.parametrize('content', ['', '{', '[]', '{"run_id":"run_fixture","stages":[]}'])
def test_live_malformed_state_is_controlled(client, live_channel, content):
    (live_channel[1] / 'live_state.json').write_text(content, encoding='utf-8')
    response = client.get('/api/ttea/live')
    assert response.status_code == 200
    assert response.json()['status'] == 'idle'


def test_live_missing_artifacts_are_controlled(client):
    assert client.get('/api/ttea/live').json()['status'] == 'idle'
    assert client.get('/api/ttea/live/events').json()['items'] == []
    assert client.get('/api/ttea/live/observer').status_code == 404


def test_live_event_tail_is_bounded_and_ignores_partial_line(client, live_channel):
    directory = live_channel[1]
    with (directory / 'events.jsonl').open('w', encoding='utf-8') as stream:
        for index in range(2000):
            stream.write(json.dumps({'run_id':'run_fixture','timestamp':'2026-09-24T10:00:00Z',
                                     'stage':'cloud_scoring','event_type':'cloud_score_completed',
                                     'message':str(index), 'headers':'PRIVATE'}) + '\n')
        stream.write('{"run_id":')
    body = client.get('/api/ttea/live/events?limit=12').json()
    assert len(body['items']) == 12
    assert body['items'][-1]['message'] == '1999'
    assert 'PRIVATE' not in json.dumps(body)


def test_live_observer_is_sanitized_and_sandboxed(client, live_channel):
    directory = live_channel[1] / 'observer'
    directory.mkdir()
    (directory / 'detail.html').write_text('<h1 onclick="bad()">Job</h1><script>evil()</script><form><input></form><img src="https://evil.test"><a href="https://evil.test">Link</a>', encoding='utf-8')
    response = client.get('/api/ttea/live/observer?run_id=run_fixture&page=detail')
    assert response.status_code == 200
    assert '<h1>Job</h1>' in response.text
    for forbidden in ('onclick', '<script', '<form', '<input', '<img', 'href=', 'https://evil'):
        assert forbidden not in response.text
    assert "default-src 'none'" in response.headers['content-security-policy']
    assert 'sandbox' in response.headers['content-security-policy']
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('query', ['run_id=../../config', 'run_id=run_fixture&page=../../config/settings.yaml', 'run_id=C:/Windows', 'run_id=run_fixture&page=detail.html'])
def test_live_observer_rejects_traversal(client, live_channel, query):
    assert client.get('/api/ttea/live/observer?' + query).status_code == 404


def test_hunt_navigation_and_static_security_contract():
    root = Path(__file__).parents[1]
    html = (root / 'ui/index.html').read_text(encoding='utf-8')
    js = (root / 'ui/app.js').read_text(encoding='utf-8')
    css = (root / 'ui/styles.css').read_text(encoding='utf-8')
    for view in ('overview','jobs','applications','sources','pipeline','profile','hunt'):
        assert f'data-view="{view}"' in html
        assert f'id="view-{view}"' in html
    assert '<span>07</span> HUNT' in html
    assert 'sandbox=""' in html
    assert 'allow-scripts' not in html
    for endpoint in ('/api/ttea/live', '/api/ttea/live/events', '/api/ttea/live/observer'):
        assert endpoint in js
    assert 'prefers-reduced-motion' in css
    assert '.hunt-stage.running' in css
    assert '.hunt-stage.failed' in css
    assert '.hunt-stage.pending' in css
