"""Namespace-isolated Object Storage and Index management for Agent Runtime V2.

Enforces:
1. Object storage keys are strictly isolated under prefix 'v2/' (or dedicated bucket 'agent-runtime-v2-objects').
2. Search indexes are strictly isolated under namespace 'ar_v2_indexes'.
3. Stores immutable bytes addressed by SHA-256 with checksum verification.
4. Manages and verifies 'v2/env_marker.json' storage safety marker.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from weai.persistence.v2.models import utcnow

log = logging.getLogger(__name__)

DEFAULT_V2_STORAGE_PREFIX = "v2/"
DEFAULT_V2_INDEX_NAMESPACE = "ar_v2_indexes"
STORAGE_MARKER_KEY = "v2/env_marker.json"
APPROVED_V2_STORAGE_PREFIXES = ("v2/", "agent-runtime-v2-objects/")


class StorageChecksumMismatchError(ValueError):
    """Raised when data written or read does not match expected SHA-256."""


class StorageMarkerMissingError(RuntimeError):
    """Raised when v2/env_marker.json is not found in storage."""


class StorageNamespaceViolationError(ValueError):
    """Raised when an attempt is made to access an object outside the v2 namespace."""


class V2ObjectStore:
    """Namespace-isolated ObjectStore client for Agent Runtime V2."""

    def __init__(
        self,
        base_dir: str | Path | None = None,
        bucket_name: str | None = None,
        prefix: str | None = None,
        index_namespace: str | None = None,
    ) -> None:
        # 1. Determine raw prefix without silent fallback if explicitly passed empty
        if prefix is not None:
            raw_prefix = prefix
        elif "AGENT_RUNTIME_V2_STORAGE_PREFIX" in os.environ:
            raw_prefix = os.environ["AGENT_RUNTIME_V2_STORAGE_PREFIX"]
        else:
            raw_prefix = DEFAULT_V2_STORAGE_PREFIX

        # 2. Reject empty, root, or whitespace prefix
        if not raw_prefix or not raw_prefix.strip() or not raw_prefix.strip("/\\"):
            raise StorageNamespaceViolationError(
                f"Storage prefix cannot be empty or root: {raw_prefix!r}. "
                "Agent Runtime V2 requires a non-empty, strictly isolated namespace prefix."
            )

        # 3. Normalize separators and reject path traversal sequences
        normalized_prefix = raw_prefix.replace("\\", "/").strip()
        if ".." in normalized_prefix:
            raise StorageNamespaceViolationError(
                f"Storage prefix cannot contain traversal sequences '..': {raw_prefix!r}."
            )

        # 4. Fail-closed on legacy V1 or legacy namespace references
        lower_prefix = normalized_prefix.lower()
        if "v1" in lower_prefix:
            raise StorageNamespaceViolationError(
                f"Storage prefix cannot reference legacy 'v1' namespace: {raw_prefix!r}. "
                "Agent Runtime V2 enforces Greenfield persistence and strictly forbids legacy V1 storage."
            )
        if "legacy" in lower_prefix:
            raise StorageNamespaceViolationError(
                f"Storage prefix cannot reference 'legacy' namespace: {raw_prefix!r}."
            )

        # 5. Enforce approved V2 namespace prefix
        clean_prefix = normalized_prefix.strip("/") + "/"
        if not any(clean_prefix.startswith(approved) for approved in APPROVED_V2_STORAGE_PREFIXES):
            raise StorageNamespaceViolationError(
                f"Storage prefix '{raw_prefix}' is invalid. "
                f"Prefix must start with an approved V2 namespace {APPROVED_V2_STORAGE_PREFIXES}."
            )

        self.prefix = clean_prefix

        # 6. Validate bucket name
        raw_bucket = bucket_name or os.getenv("AGENT_RUNTIME_V2_STORAGE_BUCKET", "agent-runtime-v2-objects")
        if not raw_bucket or not raw_bucket.strip():
            raise StorageNamespaceViolationError("Storage bucket name cannot be empty.")
        if "v1" in raw_bucket.lower() or "legacy" in raw_bucket.lower() or ".." in raw_bucket:
            raise StorageNamespaceViolationError(
                f"Storage bucket name cannot reference legacy namespace: {raw_bucket!r}."
            )
        self.bucket_name = raw_bucket

        # 7. Validate index namespace
        raw_index = index_namespace or os.getenv("AGENT_RUNTIME_V2_INDEX_NAMESPACE", DEFAULT_V2_INDEX_NAMESPACE)
        if not raw_index or not raw_index.strip():
            raise StorageNamespaceViolationError("Search index namespace cannot be empty.")
        if "v1" in raw_index.lower() or "legacy" in raw_index.lower() or ".." in raw_index:
            raise StorageNamespaceViolationError(
                f"Search index namespace cannot reference legacy namespace: {raw_index!r}."
            )
        if not raw_index.startswith("ar_v2_"):
            raise StorageNamespaceViolationError(
                f"Search index namespace must begin with 'ar_v2_': got {raw_index!r}."
            )
        self.index_namespace = raw_index

        # 8. Base filesystem directory for local storage mode
        data_dir = base_dir or os.getenv("DATA_DIR", "data")
        self.local_root = Path(data_dir) / "v2_storage"
        self.local_root.mkdir(parents=True, exist_ok=True)
        (self.local_root / self.prefix).mkdir(parents=True, exist_ok=True)

    def _scoped_key(self, key: str) -> str:
        """Ensure key is strictly scoped under the V2 namespace prefix and free of traversal."""
        if not key or not str(key).strip():
            raise StorageNamespaceViolationError("Storage key cannot be empty.")

        normalized_key = str(key).replace("\\", "/").strip()

        # Reject path traversal tokens
        key_parts = [p for p in normalized_key.split("/") if p]
        if ".." in key_parts:
            raise StorageNamespaceViolationError(
                f"Path traversal detected in storage key: {key!r}."
            )

        clean_key = normalized_key.lstrip("/")

        # Reject explicit legacy prefixes in key
        if clean_key.startswith("v1/") or clean_key.startswith("legacy/"):
            raise StorageNamespaceViolationError(
                f"Storage key cannot reference legacy namespace: {key!r}."
            )

        if not clean_key.startswith(self.prefix):
            clean_key = f"{self.prefix}{clean_key}"
        return clean_key

    def _local_path(self, scoped_key: str) -> Path:
        """Resolve filesystem path for local storage mode, enforcing namespace confinement."""
        normalized_key = str(scoped_key).replace("\\", "/").strip()
        parts = [p for p in normalized_key.split("/") if p]
        if ".." in parts:
            raise StorageNamespaceViolationError(
                f"Path traversal detected in scoped key: {scoped_key!r}."
            )

        resolved_root = self.local_root.resolve()
        target_path = (self.local_root / scoped_key).resolve()

        if not target_path.is_relative_to(resolved_root):
            raise StorageNamespaceViolationError(
                f"Storage path traversal attempt: resolved path '{target_path}' "
                f"escapes local root '{resolved_root}'."
            )

        if target_path == resolved_root:
            raise StorageNamespaceViolationError(
                f"Storage key cannot resolve to root directory: {scoped_key!r}."
            )

        target_path.parent.mkdir(parents=True, exist_ok=True)
        return target_path

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Store bytes under a scoped key and return the full scoped key."""
        scoped_key = self._scoped_key(key)
        path = self._local_path(scoped_key)
        path.write_bytes(data)
        return scoped_key

    def put_immutable(self, data: bytes, expected_sha256: str | None = None) -> tuple[str, str]:
        """Store immutable bytes addressed by SHA-256 digest.

        Returns (scoped_key, sha256).
        """
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if expected_sha256 and actual_sha256.lower() != expected_sha256.lower():
            raise StorageChecksumMismatchError(
                f"SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
            )

        key = f"blobs/{actual_sha256}"
        scoped_key = self.put_bytes(key, data)
        return scoped_key, actual_sha256

    def get_bytes(self, key: str) -> bytes:
        """Retrieve bytes for a scoped key."""
        scoped_key = self._scoped_key(key)
        path = self._local_path(scoped_key)
        if not path.exists():
            raise FileNotFoundError(f"Storage key not found: {scoped_key}")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        """Check if a scoped key exists in storage."""
        scoped_key = self._scoped_key(key)
        path = self._local_path(scoped_key)
        return path.exists()

    def write_storage_marker(
        self,
        environment_id: str = "development",
        schema_version: str = "v2.0",
    ) -> dict[str, Any]:
        """Write the storage environment marker to v2/env_marker.json."""
        marker = {
            "environment_id": environment_id,
            "schema_version": schema_version,
            "object_namespace": self.prefix,
            "index_namespace": self.index_namespace,
            "created_at": utcnow().isoformat(),
        }
        marker_bytes = json.dumps(marker, indent=2).encode("utf-8")
        self.put_bytes(STORAGE_MARKER_KEY, marker_bytes, content_type="application/json")
        log.info("Storage marker written to %s", STORAGE_MARKER_KEY)
        return marker

    def verify_storage_marker(self, expected_environment_id: str | None = None) -> dict[str, Any]:
        """Verify that v2/env_marker.json exists and strictly adheres to V2 isolation invariants."""
        if not self.exists(STORAGE_MARKER_KEY):
            raise StorageMarkerMissingError(
                f"Storage marker '{STORAGE_MARKER_KEY}' is missing. Storage is uninitialized."
            )

        raw_bytes = self.get_bytes(STORAGE_MARKER_KEY)
        try:
            marker_data = json.loads(raw_bytes.decode("utf-8"))
        except Exception as exc:
            raise StorageNamespaceViolationError(
                f"Storage marker '{STORAGE_MARKER_KEY}' is corrupted or invalid JSON: {exc}"
            ) from exc

        if not isinstance(marker_data, dict):
            raise StorageNamespaceViolationError(
                f"Storage marker '{STORAGE_MARKER_KEY}' must contain a JSON object."
            )

        # 1. Verify environment ID
        expected_env = expected_environment_id or os.getenv("AGENT_RUNTIME_ENVIRONMENT_ID", "development")
        marker_env = marker_data.get("environment_id")
        if marker_env != expected_env:
            raise StorageNamespaceViolationError(
                f"Storage environment mismatch: marker is '{marker_env}', "
                f"expected '{expected_env}'."
            )

        # 2. Strictly verify object_namespace begins with "v2/" or approved prefix
        obj_ns = marker_data.get("object_namespace")
        if not obj_ns or not isinstance(obj_ns, str):
            raise StorageNamespaceViolationError(
                f"Storage marker missing valid 'object_namespace': {obj_ns!r}."
            )
        if not (obj_ns.startswith("v2/") or any(obj_ns.startswith(approved) for approved in APPROVED_V2_STORAGE_PREFIXES)):
            raise StorageNamespaceViolationError(
                f"Storage marker 'object_namespace' must strictly begin with 'v2/': got '{obj_ns}'."
            )
        if "v1" in obj_ns.lower() or "legacy" in obj_ns.lower() or ".." in obj_ns:
            raise StorageNamespaceViolationError(
                f"Storage marker 'object_namespace' contains forbidden namespace or traversal: '{obj_ns}'."
            )

        # 3. Verify object_namespace matches store's configured prefix
        if obj_ns != self.prefix:
            raise StorageNamespaceViolationError(
                f"Storage namespace mismatch: marker has '{obj_ns}', "
                f"but store initialized with '{self.prefix}'."
            )

        # 4. Verify search index namespace
        idx_ns = marker_data.get("index_namespace")
        if not idx_ns or not isinstance(idx_ns, str) or not idx_ns.startswith("ar_v2_"):
            raise StorageNamespaceViolationError(
                f"Storage marker 'index_namespace' must begin with 'ar_v2_': got '{idx_ns}'."
            )
        if "v1" in idx_ns.lower() or "legacy" in idx_ns.lower():
            raise StorageNamespaceViolationError(
                f"Storage marker 'index_namespace' contains forbidden keyword: '{idx_ns}'."
            )

        # 5. Verify schema generation / version
        schema_ver = marker_data.get("schema_version")
        if not schema_ver or not isinstance(schema_ver, str) or not schema_ver.startswith("v2"):
            raise StorageNamespaceViolationError(
                f"Storage marker 'schema_version' must begin with 'v2': got '{schema_ver}'."
            )

        return marker_data


def validate_storage_startup_or_exit(
    base_dir: str | Path | None = None,
    expected_environment_id: str | None = None,
) -> None:
    """Fail-closed startup validation for V2 storage namespace and environment marker."""
    if os.getenv("AGENT_RUNTIME_SKIP_STARTUP_VALIDATION", "").strip().lower() in ("true", "1", "yes"):
        log.info("Storage startup validation skipped via AGENT_RUNTIME_SKIP_STARTUP_VALIDATION.")
        return
    try:
        store = V2ObjectStore(base_dir=base_dir)
        store.verify_storage_marker(expected_environment_id=expected_environment_id)
        log.info("V2 storage environment validation passed.")
    except Exception as exc:
        sys.stderr.write(
            f"\n{'=' * 80}\n"
            f"FATAL: Agent Runtime V2 Storage Startup Validation Failed!\n"
            f"Reason: {exc}\n"
            f"Agent Runtime V2 enforces fail-closed storage startup to protect data integrity.\n"
            f"{'=' * 80}\n"
        )
        sys.stderr.flush()
        sys.exit(1)
