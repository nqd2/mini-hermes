import pytest

from weai import config as weai_config
from weai.socket import main as socket_main


def test_parse_cors_allow_origin_accepts_commas_semicolons_and_strips_slashes():
    assert weai_config.parse_cors_allow_origin(
        'https://weai.ducnq.io.vn, https://mini-hermes.ducnq.io.vn/'
    ) == ['https://weai.ducnq.io.vn', 'https://mini-hermes.ducnq.io.vn']
    assert weai_config.parse_cors_allow_origin(
        'http://127.0.0.1:5173;http://localhost:5173/'
    ) == ['http://127.0.0.1:5173', 'http://localhost:5173']
    assert weai_config.parse_cors_allow_origin('*') == ['*']
    assert weai_config.parse_cors_allow_origin('') == ['*']


def test_validate_cors_origin_rejects_paths_and_concatenated_origins():
    weai_config.validate_cors_origin('https://weai.ducnq.io.vn')
    weai_config.validate_cors_origin('http://127.0.0.1:5173')

    with pytest.raises(ValueError, match='path, query, or fragment'):
        weai_config.validate_cors_origin('https://weai.ducnq.io.vn,https://mini-hermes.ducnq.io.vn')

    with pytest.raises(ValueError, match='path, query, or fragment'):
        weai_config.validate_cors_origin('https://weai.ducnq.io.vn/chat')


def test_socketio_origin_validator_allows_configured_and_same_origin_but_blocks_foreign(monkeypatch):
    monkeypatch.setattr(socket_main, 'CORS_ALLOW_ORIGIN', ['https://allowed.example.com'])

    assert socket_main.is_allowed_socketio_origin('https://allowed.example.com', {}) is True
    assert socket_main.is_allowed_socketio_origin('https://allowed.example.com/', {}) is True
    assert socket_main.is_allowed_socketio_origin(None, {}) is True

    # Same-origin behind TLS-terminating reverse proxy
    proxied_environ = {
        'wsgi.url_scheme': 'http',
        'HTTP_HOST': 'weai.ducnq.io.vn',
        'HTTP_X_FORWARDED_PROTO': 'https',
        'HTTP_X_FORWARDED_HOST': 'weai.ducnq.io.vn',
    }
    assert socket_main.is_allowed_socketio_origin('https://weai.ducnq.io.vn', proxied_environ) is True

    # Same-origin when proxy sets Host but omits X-Forwarded-Proto
    host_only_environ = {
        'wsgi.url_scheme': 'http',
        'HTTP_HOST': 'weai.ducnq.io.vn',
    }
    assert socket_main.is_allowed_socketio_origin('https://weai.ducnq.io.vn', host_only_environ) is True

    # Foreign cross-origin must still be rejected
    assert socket_main.is_allowed_socketio_origin('https://evil.example.net', proxied_environ) is False
