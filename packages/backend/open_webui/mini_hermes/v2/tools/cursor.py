"""D03c Authenticated Cursor Codec for Mini-Hermes V2 (Feature 27).

Encodes and verifies HMAC-SHA256 signed pagination cursors conforming to:
- Token format: ASCII mhv2c1.<kid>.<payload>.<tag> (max 4096 characters).
- Payload schema: docs/v2/phase0/schemas/cursor-payload.schema.json.
- Scope digest: Lowercase hex SHA-256 of 'mhv2:cursor-scope:v1\n' + canonical JSON.
- 15-minute strict TTL (900 seconds).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any


DEFAULT_KID = "k1"
DEFAULT_DEV_SECRET = "mini-hermes-dev-cursor-secret-key-32bytes!!"
CURSOR_TTL_SECONDS = 900
MAX_CURSOR_TOKEN_LENGTH = 4096

KID_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
SHA256_HEX_REGEX = re.compile(r"^[a-f0-9]{64}$")
ALLOWED_CURSOR_OPERATIONS = frozenset({
    "read_document_range",
    "read_spreadsheet_range",
    "search_document",
})


@dataclass
class CursorKeyEntry:
    kid: str
    secret: bytes
    state: str = "active"  # "active", "verify_only", "revoked"
    activated_at: int = 0
    last_issue_at: int | None = None


_KEY_REGISTRY: dict[str, CursorKeyEntry] = {}


def register_cursor_key(
    kid: str,
    secret: str | bytes,
    *,
    state: str = "active",
    activated_at: int = 0,
    last_issue_at: int | None = None,
) -> None:
    """Registers or updates a cursor signing key for rotation/revocation."""
    if not KID_REGEX.match(kid):
        raise CursorInvalidError(f"Invalid key ID format: {kid}")
    if state not in ("active", "verify_only", "revoked"):
        raise ValueError(f"Invalid key state: {state}")
    secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    _KEY_REGISTRY[kid] = CursorKeyEntry(
        kid=kid,
        secret=secret_bytes,
        state=state,
        activated_at=int(activated_at),
        last_issue_at=int(last_issue_at) if last_issue_at is not None else None,
    )


def revoke_cursor_key(kid: str) -> None:
    """Marks a key ID as revoked so all live cursors signed by it fail verification."""
    if kid in _KEY_REGISTRY:
        _KEY_REGISTRY[kid].state = "revoked"
    else:
        _KEY_REGISTRY[kid] = CursorKeyEntry(
            kid=kid,
            secret=b"",
            state="revoked",
        )


def reset_cursor_key_registry() -> None:
    """Resets in-memory key registry to defaults."""
    _KEY_REGISTRY.clear()


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for k, v in pairs:
        if k in obj:
            raise CursorInvalidError(f"Duplicate JSON key in cursor payload: {k}")
        obj[k] = v
    return obj


class CursorError(Exception):
    """Base exception for cursor codec failures."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


CursorValidationError = CursorError


class CursorInvalidError(CursorError):
    def __init__(self, message: str = "Invalid cursor token"):
        super().__init__("CURSOR_INVALID", message, status_code=400)


class CursorExpiredError(CursorError):
    def __init__(self, message: str = "Cursor token has expired (15-minute TTL)"):
        super().__init__("CURSOR_EXPIRED", message, status_code=410)


class CursorScopeMismatchError(CursorError):
    def __init__(self, message: str = "Cursor scope does not match requested resource or range"):
        super().__init__("CURSOR_SCOPE_MISMATCH", message, status_code=400)


class CursorBuildRetiredError(CursorError):
    def __init__(self, message: str = "Cursor references a retired or superseded index build"):
        super().__init__("CURSOR_BUILD_RETIRED", message, status_code=400)


def b64url_encode(data: bytes) -> str:
    """Encodes bytes to unpadded base64url string."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(token: str) -> bytes:
    """Decodes unpadded base64url string to bytes and enforces canonical encoding."""
    if not token or "=" in token or not re.match(r"^[A-Za-z0-9_-]+$", token):
        raise CursorInvalidError("Non-canonical or padded base64url segment")
    pad = "=" * (-len(token) % 4)
    raw = base64.urlsafe_b64decode(token + pad)
    if b64url_encode(raw) != token:
        raise CursorInvalidError("Non-canonical base64url padding bits")
    return raw


def canonical_json_bytes(data: Any) -> bytes:
    """Serializes data using D03b canonical JSON profile."""
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def get_cursor_key(kid: str, *, for_signing: bool = False) -> tuple[bytes, CursorKeyEntry | None]:
    """Resolves secret key and metadata for given key ID."""
    if kid in _KEY_REGISTRY:
        entry = _KEY_REGISTRY[kid]
        if entry.state == "revoked":
            raise CursorInvalidError(f"Cursor key ID '{kid}' has been revoked")
        if for_signing and entry.state != "active":
            raise CursorInvalidError(f"Cursor key ID '{kid}' is '{entry.state}' and cannot sign new cursors")
        return entry.secret, entry

    env_var = f"MH_CURSOR_SECRET_KEY_{kid.upper()}"
    secret = os.environ.get(env_var) or (os.environ.get("MH_CURSOR_SECRET_KEY") if kid == DEFAULT_KID else None)
    if secret:
        return secret.encode("utf-8"), None
    if kid == DEFAULT_KID:
        return DEFAULT_DEV_SECRET.encode("utf-8"), None
    raise CursorInvalidError(f"Unknown or revoked cursor key ID: {kid}")


def compute_scope_digest(
    operation: str,
    document_id: str,
    revision: int,
    source_sha256: str,
    build_id: str,
    scope: dict[str, Any],
) -> str:
    """Computes canonical scope digest per D03c specification."""
    scope_envelope = {
        "schema": "mhv2.cursor-scope.v1",
        "operation": operation,
        "document_id": str(document_id),
        "revision": int(revision),
        "source_sha256": source_sha256.lower(),
        "build_id": str(build_id),
        "scope": scope,
    }
    canonical_bytes = canonical_json_bytes(scope_envelope)
    prefixed = b"mhv2:cursor-scope:v1\n" + canonical_bytes
    return hashlib.sha256(prefixed).hexdigest().lower()


def _is_strict_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def validate_cursor_payload_dict(payload: dict[str, Any]) -> None:
    """Validates structural conformance against cursor-payload.schema.json."""
    if not isinstance(payload, dict):
        raise CursorInvalidError("Payload must be a JSON object")

    # Required fields per schema
    required_fields = {
        "v", "principal_id", "tenant_id", "operation",
        "document_id", "revision", "source_sha256", "build_id",
        "scope_sha256", "position", "iat", "exp",
    }
    if set(payload.keys()) != required_fields:
        raise CursorInvalidError(f"Cursor payload keys mismatch: {set(payload.keys())} != {required_fields}")

    if not _is_strict_int(payload["v"]) or payload["v"] != 1:
        raise CursorInvalidError(f"Unsupported cursor version: {payload['v']}")

    if not isinstance(payload["principal_id"], str) or len(payload["principal_id"]) < 1:
        raise CursorInvalidError("principal_id must be non-empty string")
    if not isinstance(payload["tenant_id"], str) or len(payload["tenant_id"]) < 1:
        raise CursorInvalidError("tenant_id must be non-empty string")
    if not isinstance(payload["operation"], str) or payload["operation"] not in ALLOWED_CURSOR_OPERATIONS:
        raise CursorInvalidError(f"Unsupported cursor operation: {payload.get('operation')}")
    if not isinstance(payload["document_id"], str) or len(payload["document_id"]) < 1:
        raise CursorInvalidError("document_id must be non-empty string")
    if not _is_strict_int(payload["revision"]) or payload["revision"] < 1:
        raise CursorInvalidError("revision must be integer >= 1")
    if not isinstance(payload["build_id"], str) or len(payload["build_id"]) < 1:
        raise CursorInvalidError("build_id must be non-empty string")

    # Lowercase SHA-256 strings
    for field_name in ("source_sha256", "scope_sha256"):
        val = payload[field_name]
        if not isinstance(val, str) or not SHA256_HEX_REGEX.match(val):
            raise CursorInvalidError(f"{field_name} must be 64-char lowercase hex SHA-256")

    # Timestamps
    if not _is_strict_int(payload["iat"]) or not _is_strict_int(payload["exp"]):
        raise CursorInvalidError("iat and exp must be integers")
    if payload["iat"] < 0 or payload["exp"] < 1:
        raise CursorInvalidError("iat and exp must be non-negative")
    if payload["exp"] - payload["iat"] != CURSOR_TTL_SECONDS:
        raise CursorInvalidError("exp must be exactly iat + 900 seconds")

    # Position
    pos = payload["position"]
    if not isinstance(pos, dict):
        raise CursorInvalidError("position must be an object")
    if set(pos.keys()) != {"item_ordinal", "fragment"}:
        raise CursorInvalidError("position keys must be item_ordinal and fragment")
    if not _is_strict_int(pos["item_ordinal"]) or pos["item_ordinal"] < 0:
        raise CursorInvalidError("position.item_ordinal must be integer >= 0")

    fragment = pos["fragment"]
    if fragment is not None:
        if not isinstance(fragment, dict):
            raise CursorInvalidError("position.fragment must be null or object")
        if set(fragment.keys()) != {"field", "codepoint_offset"}:
            raise CursorInvalidError("position.fragment keys must be field and codepoint_offset")
        if not isinstance(fragment["field"], str) or len(fragment["field"]) < 1:
            raise CursorInvalidError("fragment.field must be non-empty string")
        if not _is_strict_int(fragment["codepoint_offset"]) or fragment["codepoint_offset"] < 0:
            raise CursorInvalidError("fragment.codepoint_offset must be integer >= 0")


def sign_cursor(
    *,
    principal_id: str,
    tenant_id: str = "default",
    operation: str,
    document_id: str,
    revision: int,
    source_sha256: str,
    build_id: str,
    scope_sha256: str,
    item_ordinal: int,
    fragment: dict[str, Any] | None = None,
    kid: str = DEFAULT_KID,
    now: int | None = None,
) -> str:
    """Generates an authenticated cursor token ASCII string."""
    if not KID_REGEX.match(kid):
        raise CursorInvalidError(f"Invalid key ID format: {kid}")

    iat = int(now if now is not None else time.time())
    exp = iat + CURSOR_TTL_SECONDS

    secret_key, key_entry = get_cursor_key(kid, for_signing=True)
    if key_entry is not None:
        if iat < key_entry.activated_at:
            raise CursorInvalidError(f"Cursor iat ({iat}) precedes key activation ({key_entry.activated_at})")
        if key_entry.last_issue_at is not None and iat > key_entry.last_issue_at:
            raise CursorInvalidError(f"Cursor iat ({iat}) exceeds key last_issue_at ({key_entry.last_issue_at})")

    payload_dict = {
        "v": 1,
        "principal_id": str(principal_id),
        "tenant_id": str(tenant_id),
        "operation": str(operation),
        "document_id": str(document_id),
        "revision": int(revision),
        "source_sha256": source_sha256.lower(),
        "build_id": str(build_id),
        "scope_sha256": scope_sha256.lower(),
        "position": {
            "item_ordinal": int(item_ordinal),
            "fragment": fragment,
        },
        "iat": iat,
        "exp": exp,
    }
    validate_cursor_payload_dict(payload_dict)

    payload_bytes = canonical_json_bytes(payload_dict)
    payload_b64 = b64url_encode(payload_bytes)

    signed_header = f"mhv2c1.{kid}.{payload_b64}".encode("ascii")
    msg = b"mhv2:cursor:v1\n" + signed_header

    tag_bytes = hmac.new(secret_key, msg, hashlib.sha256).digest()
    tag_b64 = b64url_encode(tag_bytes)

    token = f"mhv2c1.{kid}.{payload_b64}.{tag_b64}"
    if len(token) > MAX_CURSOR_TOKEN_LENGTH:
        raise CursorInvalidError(f"Cursor token length ({len(token)}) exceeds {MAX_CURSOR_TOKEN_LENGTH} chars")
    return token


def verify_cursor(
    token: str,
    *,
    expected_principal_id: str,
    expected_tenant_id: str = "default",
    expected_operation: str,
    expected_document_id: str,
    expected_revision: int,
    expected_source_sha256: str,
    expected_build_id: str,
    expected_scope_sha256: str,
    now: int | None = None,
) -> dict[str, Any]:
    """Verifies and decodes a D03c cursor token.

    Returns decoded canonical payload dictionary.
    """
    if not isinstance(token, str):
        raise CursorInvalidError("Cursor token must be a string")
    if len(token) > MAX_CURSOR_TOKEN_LENGTH:
        raise CursorInvalidError(f"Cursor token exceeds max length of {MAX_CURSOR_TOKEN_LENGTH} chars")
    try:
        token.encode("ascii")
    except UnicodeEncodeError as exc:
        raise CursorInvalidError("Cursor token must be ASCII") from exc

    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "mhv2c1":
        raise CursorInvalidError("Cursor token must match format mhv2c1.<kid>.<payload>.<tag>")

    kid, payload_b64, tag_b64 = parts[1], parts[2], parts[3]
    if not KID_REGEX.match(kid):
        raise CursorInvalidError(f"Invalid kid in cursor: {kid}")

    # Verify HMAC tag in constant time
    try:
        provided_tag = b64url_decode(tag_b64)
    except CursorError:
        raise
    except Exception as exc:
        raise CursorInvalidError("Malformed base64url tag") from exc

    if len(provided_tag) != 32:
        raise CursorInvalidError("Tag must be 32 bytes HMAC-SHA256")

    secret_key, key_entry = get_cursor_key(kid, for_signing=False)
    signed_header = f"mhv2c1.{kid}.{payload_b64}".encode("ascii")
    msg = b"mhv2:cursor:v1\n" + signed_header
    expected_tag = hmac.new(secret_key, msg, hashlib.sha256).digest()

    if not hmac.compare_digest(provided_tag, expected_tag):
        raise CursorInvalidError("Cursor signature verification failed")

    # Decode and parse payload with duplicate-key rejection
    try:
        payload_bytes = b64url_decode(payload_b64)
        payload = json.loads(
            payload_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except CursorError:
        raise
    except Exception as exc:
        raise CursorInvalidError("Malformed cursor payload") from exc

    validate_cursor_payload_dict(payload)

    if key_entry is not None:
        if payload["iat"] < key_entry.activated_at:
            raise CursorInvalidError("Cursor iat precedes key activation time")
        if key_entry.last_issue_at is not None and payload["iat"] > key_entry.last_issue_at:
            raise CursorInvalidError("Cursor iat exceeds key last permitted issuance time")

    # Time validation (15-minute TTL: iat <= now < exp)
    current_ts = int(now if now is not None else time.time())
    if current_ts < payload["iat"]:
        raise CursorInvalidError("Cursor iat is in the future")
    if current_ts >= payload["exp"]:
        raise CursorExpiredError("Cursor token has expired (15-minute TTL)")

    # Scope & Binding validation
    if payload["principal_id"] != expected_principal_id:
        raise CursorScopeMismatchError("Cursor principal does not match current caller")
    if payload["tenant_id"] != expected_tenant_id:
        raise CursorScopeMismatchError("Cursor tenant does not match current tenant")
    if payload["operation"] != expected_operation:
        raise CursorScopeMismatchError("Cursor operation does not match current operation")
    if payload["document_id"] != str(expected_document_id):
        raise CursorScopeMismatchError("Cursor document_id does not match current document")
    if payload["revision"] != int(expected_revision):
        raise CursorScopeMismatchError("Cursor revision does not match current revision")
    if payload["source_sha256"] != expected_source_sha256.lower():
        raise CursorScopeMismatchError("Cursor source_sha256 does not match current source")
    if payload["scope_sha256"] != expected_scope_sha256.lower():
        raise CursorScopeMismatchError("Cursor scope_sha256 does not match request scope")

    # Build check
    if payload["build_id"] != str(expected_build_id):
        raise CursorBuildRetiredError("Cursor references a retired or superseded index build")

    return payload
