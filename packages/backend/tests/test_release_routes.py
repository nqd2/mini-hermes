from fastapi import FastAPI
from fastapi.testclient import TestClient

from weai.releases import release_routes


def test_release_api_and_legacy_notes(monkeypatch):
    monkeypatch.setenv('WEAI_RELEASE_VERSION', '0.1.0')
    app = FastAPI()
    app.include_router(release_routes.router, prefix='/api/v1/agent-runtime')
    app.dependency_overrides[release_routes.get_verified_user] = lambda: object()
    response = TestClient(app).get('/api/v1/agent-runtime/release')
    assert response.status_code == 200
    release = response.json()
    assert release == {
        'version': '0.1.0',
        'released_at': '2026-11-18',
        'sections': release_routes.configured_release().sections,
    }
    assert release_routes.local_changelog()['0.1.0']['added'][0]['content'] == release['sections']['Added'][0]


def test_invalid_release_returns_503(monkeypatch):
    monkeypatch.setenv('WEAI_RELEASE_VERSION', '99.0.0')
    app = FastAPI()
    app.include_router(release_routes.router)
    app.dependency_overrides[release_routes.get_verified_user] = lambda: object()
    assert TestClient(app).get('/release').status_code == 503


def test_releases_history_returns_sorted_releases_up_to_configured(monkeypatch):
    monkeypatch.setenv('WEAI_RELEASE_VERSION', '0.1.2')
    app = FastAPI()
    app.include_router(release_routes.router, prefix='/api/v1/agent-runtime')
    app.dependency_overrides[release_routes.get_verified_user] = lambda: object()
    response = TestClient(app).get('/api/v1/agent-runtime/releases')
    assert response.status_code == 200
    assert [entry['version'] for entry in response.json()] == ['0.1.2', '0.1.1', '0.1.0']

    monkeypatch.setenv('WEAI_RELEASE_VERSION', '0.1.3')
    latest_res = TestClient(app).get('/api/v1/agent-runtime/releases')
    assert latest_res.status_code == 200
    assert [entry['version'] for entry in latest_res.json()] == ['0.1.3', '0.1.2', '0.1.1', '0.1.0']
