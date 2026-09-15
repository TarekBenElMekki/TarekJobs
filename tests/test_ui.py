from fastapi.testclient import TestClient
from src.ui_server import app

client = TestClient(app)


def test_dashboard_health():
    response = client.get('/api/health')
    assert response.status_code == 200
    assert response.json()['ok'] is True


def test_dashboard_overview_reads_database():
    response = client.get('/api/overview')
    assert response.status_code == 200
    body = response.json()
    assert 'metrics' in body
    assert 'total_jobs' in body['metrics']
    assert 'sources' in body


def test_dashboard_jobs_endpoint():
    response = client.get('/api/jobs?page_size=5')
    assert response.status_code == 200
    body = response.json()
    assert 'items' in body
    assert body['page_size'] == 5
