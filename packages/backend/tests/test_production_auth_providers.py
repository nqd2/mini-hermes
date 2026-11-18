"""Production sign-in offers local accounts and Google OAuth only."""

from unittest.mock import AsyncMock

import pytest

from weai import config
from weai.routers.auths import (
    OAUTH_CONFIG_KEYS,
    OAuthConfigForm,
    get_oauth_config_values,
    oauth_config_updates,
)


@pytest.mark.asyncio
async def test_only_google_oauth_is_registered_even_if_other_credentials_are_present(monkeypatch):
    monkeypatch.setattr(config.Config, 'get_namespace', AsyncMock(return_value={
        'oauth.google.client_id': 'google-id',
        'oauth.google.client_secret': 'google-secret',
        'oauth.microsoft.client_id': 'microsoft-id',
        'oauth.microsoft.client_secret': 'microsoft-secret',
        'oauth.microsoft.tenant_id': 'tenant-id',
        'oauth.github.client_id': 'github-id',
        'oauth.github.client_secret': 'github-secret',
        'oauth.client_id': 'oidc-id',
        'oauth.client_secret': 'oidc-secret',
        'oauth.provider_url': 'https://id.example.invalid',
        'oauth.feishu.client_id': 'feishu-id',
        'oauth.feishu.client_secret': 'feishu-secret',
    }))
    await config.load_oauth_providers()
    assert set(config.OAUTH_PROVIDERS) == {'google'}


def test_retired_identity_provider_configuration_is_not_exposed():
    assert {'oauth.google.client_id', 'oauth.google.client_secret', 'oauth.google.scope',
            'oauth.google.redirect_uri'} <= set(config.DEFAULT_CONFIG)
    assert {'oauth.google.client_id', 'oauth.google.client_secret', 'oauth.google.scope',
            'oauth.google.redirect_uri'} <= set(OAUTH_CONFIG_KEYS.values())
    retired_prefixes = ('oauth.microsoft.', 'oauth.github.', 'oauth.feishu.', 'ldap.')
    retired_keys = {'oauth.client_id', 'oauth.client_secret', 'oauth.provider_url',
                    'oauth.redirect_uri', 'oauth.scopes', 'oauth.provider_name'}
    for mapping in (config.DEFAULT_CONFIG, OAUTH_CONFIG_KEYS.values()):
        assert not any(key.startswith(retired_prefixes) or key in retired_keys for key in mapping)
    assert not ({'OAUTH_CLIENT_ID', 'OAUTH_CLIENT_SECRET', 'OPENID_PROVIDER_URL'}
                & set(OAuthConfigForm.model_fields))


@pytest.mark.asyncio
async def test_google_admin_configuration_round_trips_to_provider_keys(monkeypatch):
    stored = {
        'oauth.google.client_id': 'google-id',
        'oauth.google.client_secret': 'google-secret',
        'oauth.google.scope': 'openid email profile',
        'oauth.google.redirect_uri': 'https://office.example.invalid/oauth/google/callback',
    }
    monkeypatch.setattr(config.Config, 'get_many', AsyncMock(return_value=stored))
    values = await get_oauth_config_values()
    assert values['GOOGLE_CLIENT_ID'] == 'google-id'
    assert values['GOOGLE_CLIENT_SECRET'] == 'google-secret'
    assert values['GOOGLE_OAUTH_SCOPE'] == 'openid email profile'
    assert values['GOOGLE_REDIRECT_URI'] == stored['oauth.google.redirect_uri']
    assert {key: value for key, value in oauth_config_updates(values).items() if key in stored} == stored
