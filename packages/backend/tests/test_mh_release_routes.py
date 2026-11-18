from fastapi import FastAPI
from fastapi.testclient import TestClient

from open_webui.mini_hermes import release_routes


def test_release_api_and_legacy_notes(monkeypatch):
    monkeypatch.setenv('MH_RELEASE_VERSION', '0.1.0')
    app = FastAPI()
    app.include_router(release_routes.router, prefix='/api/v1/mini-hermes')
    app.dependency_overrides[release_routes.get_verified_user] = lambda: object()
    response = TestClient(app).get('/api/v1/mini-hermes/release')
    assert response.status_code == 200
    release = response.json()
    assert release == {
        'version': '0.1.0',
        'released_at': '2026-11-18',
        'sections': release_routes.configured_release().sections,
    }
    assert release_routes.local_changelog()['0.1.0']['added'][0]['content'] == release['sections']['Added'][0]


def test_invalid_release_returns_503(monkeypatch):
    monkeypatch.setenv('MH_RELEASE_VERSION', '99.0.0')
    app = FastAPI()
    app.include_router(release_routes.router)
    app.dependency_overrides[release_routes.get_verified_user] = lambda: object()
    assert TestClient(app).get('/release').status_code == 503
