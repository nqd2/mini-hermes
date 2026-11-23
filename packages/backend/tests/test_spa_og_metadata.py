from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from weai.main import SPAStaticFiles, _resolve_request_origin

REPO_ROOT = Path(__file__).resolve().parents[3]


def _make_spa_client(tmp_path: Path, html: str) -> TestClient:
    (tmp_path / 'index.html').write_text(html, encoding='utf-8')
    app = FastAPI()
    app.mount('/', SPAStaticFiles(directory=tmp_path, html=True), name='spa')
    return TestClient(app)


def test_spa_index_rewrites_og_and_twitter_urls_from_forwarded_headers(tmp_path: Path):
    html = (
        '<html><head>'
        '<meta property="og:type" content="website" />'
        '<meta property="og:url" content="/" />'
        '<meta property="og:image" content="/static/og-image.jpeg" />'
        '<meta name="twitter:image" content="/static/og-image.jpeg" />'
        '</head><body></body></html>'
    )
    client = _make_spa_client(tmp_path, html)

    resp = client.get(
        '/',
        headers={
            'Host': 'mini-hermes.ducnq.io.vn',
            'X-Forwarded-Proto': 'https',
        },
    )
    assert resp.status_code == 200
    assert resp.headers['cache-control'] == 'no-cache'
    assert '<meta property="og:url" content="https://mini-hermes.ducnq.io.vn/" />' in resp.text
    assert (
        '<meta property="og:image" content="https://mini-hermes.ducnq.io.vn/static/og-image.jpeg" />'
        in resp.text
    )
    assert (
        '<meta name="twitter:image" content="https://mini-hermes.ducnq.io.vn/static/og-image.jpeg" />'
        in resp.text
    )
    assert int(resp.headers['content-length']) == len(resp.content)


def test_spa_index_resolves_https_from_cf_visitor_and_supports_head(tmp_path: Path):
    html = (
        '<html><head>'
        '<meta property="og:url" content="/" />'
        '<meta property="og:image" content="/static/og-image.jpeg" />'
        '</head><body></body></html>'
    )
    client = _make_spa_client(tmp_path, html)

    get_resp = client.get(
        '/auth',
        headers={
            'Host': 'mini-hermes.ducnq.io.vn',
            'CF-Visitor': '{"scheme":"https"}',
        },
    )
    assert get_resp.status_code == 200
    assert 'https://mini-hermes.ducnq.io.vn/static/og-image.jpeg' in get_resp.text

    head_resp = client.head(
        '/',
        headers={
            'Host': 'mini-hermes.ducnq.io.vn',
            'CF-Visitor': '{"scheme":"https"}',
        },
    )
    assert head_resp.status_code == 200
    assert head_resp.content == b''
    assert int(head_resp.headers['content-length']) == len(get_resp.content)


def test_resolve_request_origin_rejects_unsafe_host_headers():
    scope = {
        'type': 'http',
        'scheme': 'https',
        'headers': [(b'host', b'evil.com"><script>alert(1)</script>')],
    }
    assert _resolve_request_origin(scope) is None


def test_robots_and_app_html_allow_link_preview_crawlers():
    for rel_path in (
        'packages/frontend/static/robots.txt',
        'packages/backend/weai/static/robots.txt',
    ):
        robots = (REPO_ROOT / rel_path).read_text(encoding='utf-8')
        assert robots.strip() == 'User-agent: *\nAllow: /'

    app_html = (REPO_ROOT / 'packages/frontend/src/app.html').read_text(encoding='utf-8')
    assert '<meta name="robots"' not in app_html
    assert '<meta name="googlebot" content="noindex,nofollow" />' in app_html
    assert '<meta property="og:url" content="/" />' in app_html
    assert '<meta property="og:image" content="/static/og-image.jpeg" />' in app_html
