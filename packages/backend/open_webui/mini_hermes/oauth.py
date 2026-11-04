"""Per-user Google OAuth credentials, encrypted at rest."""

import base64
import hashlib
import os
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select

from .models import DriveCredential


SCOPES = ' '.join([
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/presentations',
])


def _fernet():
    secret = os.getenv('MH_TOKEN_ENCRYPTION_KEY')
    if not secret:
        raise RuntimeError('MH_TOKEN_ENCRYPTION_KEY is required for Google Drive OAuth')
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def authorization_url(state: str) -> str:
    client_id = os.getenv('MH_GOOGLE_CLIENT_ID')
    redirect_uri = os.getenv('MH_GOOGLE_REDIRECT_URI')
    if not client_id or not redirect_uri:
        raise HTTPException(503, 'Google OAuth is not configured')
    return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': client_id, 'redirect_uri': redirect_uri, 'response_type': 'code',
        'scope': SCOPES, 'access_type': 'offline', 'prompt': 'consent', 'state': state,
    })


async def exchange_code(code: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post('https://oauth2.googleapis.com/token', data={
            'code': code, 'client_id': os.getenv('MH_GOOGLE_CLIENT_ID'),
            'client_secret': os.getenv('MH_GOOGLE_CLIENT_SECRET'),
            'redirect_uri': os.getenv('MH_GOOGLE_REDIRECT_URI'),
            'grant_type': 'authorization_code',
        })
        response.raise_for_status()
        return response.json()


async def save_refresh_token(db, user_id: str, token: str):
    credential = (await db.execute(select(DriveCredential).where(DriveCredential.user_id == user_id))).scalar_one_or_none()
    if credential is None:
        credential = DriveCredential(user_id=user_id, encrypted_refresh_token=b'')
        db.add(credential)
    credential.encrypted_refresh_token = _fernet().encrypt(token.encode())
    await db.commit()


async def access_token_for(db, user_id: str) -> str:
    credential = (await db.execute(select(DriveCredential).where(DriveCredential.user_id == user_id))).scalar_one_or_none()
    if not credential:
        raise RuntimeError('User has not connected Google Drive')
    refresh_token = _fernet().decrypt(credential.encrypted_refresh_token).decode()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post('https://oauth2.googleapis.com/token', data={
            'refresh_token': refresh_token, 'client_id': os.getenv('MH_GOOGLE_CLIENT_ID'),
            'client_secret': os.getenv('MH_GOOGLE_CLIENT_SECRET'), 'grant_type': 'refresh_token',
        })
        response.raise_for_status()
        return response.json()['access_token']
