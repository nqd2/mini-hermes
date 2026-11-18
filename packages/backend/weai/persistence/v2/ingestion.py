"""Agent Runtime V2 Checksummed Ingestion Pipeline with Format Sniffing and Resource Limits."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pypdf
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from weai.persistence.v2.models import (
    FormatType,
    WeAIDocument,
    WeAIDocumentRevision,
    WeAIIngestionJob,
    WeAIObjectRegistry,
    WeAIStagedObject,
    WeAIUploadReceipt,
    PublicationState,
    utcnow,
)
from weai.persistence.v2.outbox import insert_outbox_event
from weai.persistence.v2.storage import (
    StorageChecksumMismatchError,
    StorageNamespaceViolationError,
    V2ObjectStore,
)

log = logging.getLogger(__name__)

DEFAULT_MAX_INGESTION_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_UNCOMPRESSED_OOXML_SIZE = 250 * 1024 * 1024  # 250 MB
MAX_COMPRESSION_RATIO = 100.0  # 100:1 ratio ceiling
MAX_ZIP_ENTRY_COUNT = 10_000  # Max 10k files inside archive


class IngestionError(Exception):
    """Base exception for all ingestion errors."""

    def __init__(
        self,
        message: str,
        code: str = "ingestion_error",
        status_code: int = 400,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.retryable = retryable


class FileSizeExceededError(IngestionError):
    def __init__(self, size: int, limit: int = DEFAULT_MAX_INGESTION_FILE_SIZE):
        super().__init__(
            f"File size {size} bytes exceeds maximum allowable ceiling of {limit} bytes (50MB).",
            code="FILE_SIZE_EXCEEDED",
            status_code=413,
            retryable=False,
        )


class UnsupportedFormatError(IngestionError):
    def __init__(self, ext: str):
        super().__init__(
            f"File extension or format '{ext}' is not supported.",
            code="UNSUPPORTED_FORMAT",
            status_code=422,
            retryable=False,
        )


class FormatSignatureMismatchError(IngestionError):
    def __init__(self, detail: str):
        super().__init__(
            f"Format signature verification failed: {detail}",
            code="FORMAT_SIGNATURE_MISMATCH",
            status_code=422,
            retryable=False,
        )


class ZipBombDetectedError(IngestionError):
    def __init__(self, detail: str):
        super().__init__(
            f"Zip bomb or decompression limit violation detected: {detail}",
            code="ZIP_BOMB_DETECTED",
            status_code=422,
            retryable=False,
        )


class EncryptedFileUnsupportedError(IngestionError):
    def __init__(self, detail: str = "Encrypted or password-protected documents are not supported."):
        super().__init__(
            detail,
            code="ENCRYPTED_FILE_UNSUPPORTED",
            status_code=422,
            retryable=False,
        )


class CorruptFileError(IngestionError):
    def __init__(self, detail: str):
        super().__init__(
            f"File container is damaged, corrupt, or truncated: {detail}",
            code="CORRUPT_FILE",
            status_code=422,
            retryable=False,
        )


class ChecksumMismatchError(IngestionError):
    def __init__(self, expected: str, actual: str):
        super().__init__(
            f"SHA-256 digest mismatch: expected '{expected}', computed '{actual}'.",
            code="CHECKSUM_MISMATCH",
            status_code=422,
            retryable=False,
        )


class IdempotencyConflictError(IngestionError):
    def __init__(self, key: str):
        super().__init__(
            f"Idempotency conflict: key '{key}' was previously used with a different request fingerprint.",
            code="IDEMPOTENCY_CONFLICT",
            status_code=409,
            retryable=False,
        )


class ReceiptExpiredOrInvalidError(IngestionError):
    def __init__(self, detail: str):
        super().__init__(
            f"Upload receipt invalid or expired: {detail}",
            code="RECEIPT_EXPIRED_OR_INVALID",
            status_code=404,
            retryable=False,
        )


class StorageIntegrityFailureError(IngestionError):
    def __init__(self, detail: str):
        super().__init__(
            f"Storage readback integrity verification failed: {detail}",
            code="STORAGE_INTEGRITY_FAILURE",
            status_code=500,
            retryable=True,
        )


class FormatSniffer:
    """Detects and strictly validates file container format against declared extension."""

    PDF_MAGIC = b"%PDF-"
    ZIP_MAGIC = b"PK\x03\x04"
    OOXML_CONTENT_TYPES = "[Content_Types].xml"

    @classmethod
    def sniff_format(cls, data: bytes, declared_filename: str) -> FormatType:
        ext = Path(declared_filename).suffix.lower()

        if ext not in (".xlsx", ".docx", ".pptx", ".pdf"):
            raise UnsupportedFormatError(ext)

        # 1. PDF Sniffing & Validation
        if ext == ".pdf":
            if not (data.startswith(cls.PDF_MAGIC) or cls.PDF_MAGIC in data[:1024]):
                raise FormatSignatureMismatchError(
                    "File claims '.pdf' extension but lacks '%PDF-' magic header."
                )
            try:
                reader = pypdf.PdfReader(io.BytesIO(data))
                if reader.is_encrypted:
                    raise EncryptedFileUnsupportedError("PDF document is password-protected or encrypted.")
                # Touch pages to detect truncated stream
                _ = len(reader.pages)
            except (EncryptedFileUnsupportedError, FormatSignatureMismatchError):
                raise
            except Exception as exc:
                raise CorruptFileError(str(exc)) from exc
            return FormatType.PDF

        # 2. OOXML (ZIP) Sniffing & Validation
        if ext in (".xlsx", ".docx", ".pptx"):
            if not data.startswith(cls.ZIP_MAGIC):
                raise FormatSignatureMismatchError(
                    f"File claims '{ext}' extension but lacks 'PK\\x03\\x04' ZIP header."
                )

            try:
                with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
                    entries = archive.infolist()
                    if len(entries) > MAX_ZIP_ENTRY_COUNT:
                        raise ZipBombDetectedError(
                            f"ZIP entry count {len(entries)} exceeds limit {MAX_ZIP_ENTRY_COUNT}."
                        )

                    total_uncompressed = 0
                    total_compressed = len(data)

                    namelist = set()
                    for info in entries:
                        namelist.add(info.filename)
                        if info.flag_bits & 0x1:
                            raise EncryptedFileUnsupportedError(
                                f"Archive entry '{info.filename}' is encrypted; password-protected files are not supported."
                            )
                        total_uncompressed += info.file_size
                        if total_uncompressed > MAX_UNCOMPRESSED_OOXML_SIZE:
                            raise ZipBombDetectedError(
                                f"Total uncompressed size {total_uncompressed} exceeds limit {MAX_UNCOMPRESSED_OOXML_SIZE} bytes (250MB)."
                            )

                    ratio = total_uncompressed / max(1, total_compressed)
                    if ratio > MAX_COMPRESSION_RATIO:
                        raise ZipBombDetectedError(
                            f"Decompression ratio {ratio:.1f}:1 exceeds security limit of {MAX_COMPRESSION_RATIO}:1."
                        )

                    # OOXML required part checks
                    if cls.OOXML_CONTENT_TYPES not in namelist:
                        raise FormatSignatureMismatchError(
                            "Invalid OOXML container: missing '[Content_Types].xml'."
                        )

                    if ext == ".xlsx":
                        if not any(
                            name.startswith("xl/") and name.endswith("workbook.xml")
                            for name in namelist
                        ):
                            raise FormatSignatureMismatchError(
                                "File claims '.xlsx' but missing Excel workbook part ('xl/workbook.xml')."
                            )
                        return FormatType.XLSX

                    if ext == ".docx":
                        if not any(
                            name.startswith("word/") and name.endswith("document.xml")
                            for name in namelist
                        ):
                            raise FormatSignatureMismatchError(
                                "File claims '.docx' but missing Word document part ('word/document.xml')."
                            )
                        return FormatType.DOCX

                    if ext == ".pptx":
                        if not any(
                            name.startswith("ppt/") and name.endswith("presentation.xml")
                            for name in namelist
                        ):
                            raise FormatSignatureMismatchError(
                                "File claims '.pptx' but missing PowerPoint presentation part ('ppt/presentation.xml')."
                            )
                        return FormatType.PPTX

            except zipfile.BadZipFile as exc:
                raise CorruptFileError(f"Damaged or corrupt ZIP archive: {exc}") from exc

        raise UnsupportedFormatError(ext)


def compute_ingestion_request_fingerprint(
    principal_id: str,
    expected_sha256: str,
    byte_size: int,
    filename: str,
    content_type: str,
    folder_id: str | None = None,
    weai_file_id: str | None = None,
) -> str:
    """Compute deterministic request fingerprint with domain 'mhv2:request:v1\\n'."""
    canonical_payload = {
        "schema": "mhv2.request.v1",
        "principal_id": principal_id,
        "operation": "start_ingestion",
        "request": {
            "byte_size": byte_size,
            "content_type": content_type,
            "expected_sha256": expected_sha256.lower(),
            "filename": filename,
            "folder_id": str(folder_id) if folder_id else None,
            "weai_file_id": weai_file_id,
        },
    }
    canonical_bytes = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    domain = b"mhv2:request:v1\n"
    return hashlib.sha256(domain + canonical_bytes).hexdigest()


class IngestionPipeline:
    """Orchestrates secure checksummed file ingestion into Greenfield V2 persistence."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        object_store: V2ObjectStore | None = None,
    ):
        self.session_factory = session_factory
        self.object_store = object_store or V2ObjectStore()

    async def start_ingestion(
        self,
        principal_id: str,
        filename: str,
        content_type: str,
        idempotency_key: str,
        data: bytes | None = None,
        receipt_id: uuid.UUID | None = None,
        expected_sha256: str | None = None,
        target_folder_id: uuid.UUID | None = None,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        """Ingests file bytes or upload receipt with atomic staging & publication."""
        # 1. Resolve raw bytes
        weai_file_id: str | None = None
        receipt: WeAIUploadReceipt | None = None

        if receipt_id is not None:
            async with self.session_factory() as session:
                receipt = await session.get(WeAIUploadReceipt, receipt_id)
                if not receipt:
                    raise ReceiptExpiredOrInvalidError(f"Receipt ID '{receipt_id}' not found.")
                if receipt.principal_id != principal_id:
                    raise ReceiptExpiredOrInvalidError("Receipt does not belong to authenticated principal.")
                now = utcnow()
                if receipt.expires_at < now:
                    raise ReceiptExpiredOrInvalidError(f"Receipt expired at {receipt.expires_at.isoformat()}.")
                if receipt.status == "CONSUMED":
                    raise ReceiptExpiredOrInvalidError("Receipt has already been consumed.")
                weai_file_id = receipt.weai_file_id
                if expected_sha256 is None:
                    expected_sha256 = receipt.expected_sha256

        if data is None:
            raise IngestionError("Binary file data must be provided.", code="MISSING_DATA", status_code=400)

        # 2. Check file size ceiling
        file_size = len(data)
        if file_size > DEFAULT_MAX_INGESTION_FILE_SIZE:
            raise FileSizeExceededError(file_size, DEFAULT_MAX_INGESTION_FILE_SIZE)

        # 3. Deep magic sniffing and format validation
        format_type = FormatSniffer.sniff_format(data, filename)

        # 4. Checksum calculation and validation
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if expected_sha256 and actual_sha256.lower() != expected_sha256.lower():
            raise ChecksumMismatchError(expected=expected_sha256, actual=actual_sha256)

        # 5. Compute request fingerprint
        request_fingerprint = compute_ingestion_request_fingerprint(
            principal_id=principal_id,
            expected_sha256=actual_sha256,
            byte_size=file_size,
            filename=filename,
            content_type=content_type,
            folder_id=str(target_folder_id) if target_folder_id else None,
            weai_file_id=weai_file_id,
        )

        # 6. Idempotency Gate
        idempotency_scope = principal_id
        async with self.session_factory() as session:
            stmt = select(WeAIIngestionJob).where(
                WeAIIngestionJob.idempotency_scope == idempotency_scope,
                WeAIIngestionJob.idempotency_key == idempotency_key,
            )
            res = await session.execute(stmt)
            existing_job = res.scalar_one_or_none()

            if existing_job:
                if existing_job.request_fingerprint != request_fingerprint:
                    raise IdempotencyConflictError(idempotency_key)

                # Fetch revision details
                rev = None
                if existing_job.revision_id:
                    rev = await session.get(WeAIDocumentRevision, existing_job.revision_id)

                return {
                    "job_id": str(existing_job.id),
                    "document_id": str(existing_job.document_id),
                    "revision_id": str(existing_job.revision_id) if existing_job.revision_id else None,
                    "revision_no": rev.revision_no if rev else 1,
                    "checksum_sha256": rev.checksum_sha256 if rev else actual_sha256,
                    "status": existing_job.source_state,
                    "object_key": rev.object_key if rev else f"v2/blobs/{actual_sha256}",
                    "is_idempotent_replay": True,
                }

        # 7. Immutable ObjectStore write & readback verification
        scoped_key, stored_hash = self.object_store.put_immutable(data, expected_sha256=actual_sha256)
        readback_bytes = self.object_store.get_bytes(scoped_key)
        if hashlib.sha256(readback_bytes).hexdigest() != actual_sha256:
            raise StorageIntegrityFailureError(
                f"Readback checksum verification failed for key '{scoped_key}'."
            )

        # 8. Atomic Database Publication Transaction
        doc_id = uuid.uuid4()
        rev_id = uuid.uuid4()
        job_id = uuid.uuid4()

        async with self.session_factory() as session:
            async with session.begin():
                # A. Object Registry update/insert
                obj_reg = await session.get(WeAIObjectRegistry, actual_sha256)
                if obj_reg:
                    obj_reg.ref_count += 1
                    obj_reg.last_verified_at = utcnow()
                else:
                    obj_reg = WeAIObjectRegistry(
                        checksum_sha256=actual_sha256,
                        canonical_object_key=scoped_key,
                        byte_size=file_size,
                        storage_tier="HOT",
                        ref_count=1,
                        gc_status="ACTIVE",
                        gc_generation=1,
                        last_verified_at=utcnow(),
                        created_at=utcnow(),
                    )
                    session.add(obj_reg)

                # B. Staged Object entry
                staged_obj = WeAIStagedObject(
                    id=uuid.uuid4(),
                    staged_object_key=scoped_key,
                    checksum_sha256=actual_sha256,
                    byte_size=file_size,
                    document_id=doc_id,
                    principal_id=principal_id,
                    stage_status="COMMITTED",
                    verified_at=utcnow(),
                    expires_at=utcnow(),
                    created_at=utcnow(),
                )
                session.add(staged_obj)

                # C. Document entity (initially unlinked or atomically bound)
                doc = WeAIDocument(
                    id=doc_id,
                    tenant_id=tenant_id,
                    name=filename,
                    owner_id=principal_id,
                    folder_id=target_folder_id,
                    mime_type=content_type,
                    format_type=format_type.value,
                    current_revision_id=None,
                    current_revision_no=0,
                    is_archived=False,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
                session.add(doc)
                await session.flush()

                # D. Document Revision entity
                rev = WeAIDocumentRevision(
                    id=rev_id,
                    document_id=doc_id,
                    revision_no=1,
                    object_key=scoped_key,
                    checksum_sha256=actual_sha256,
                    byte_size=file_size,
                    source_metadata={
                        "filename": filename,
                        "content_type": content_type,
                        "format_type": format_type.value,
                        "ingested_at": utcnow().isoformat(),
                    },
                    commit_status=PublicationState.COMMITTED.value,
                    committed_by=principal_id,
                    committed_at=utcnow(),
                )
                session.add(rev)
                await session.flush()

                # E. Advance document current revision pointer
                doc.current_revision_id = rev_id
                doc.current_revision_no = 1
                doc.updated_at = utcnow()

                # F. Ingestion Job entity
                job = WeAIIngestionJob(
                    id=job_id,
                    document_id=doc_id,
                    revision_id=rev_id,
                    principal_id=principal_id,
                    idempotency_scope=idempotency_scope,
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                    source_state=PublicationState.AVAILABLE.value,
                    index_state="REQUESTED",
                    fencing_token=1,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
                session.add(job)

                # G. Consume upload receipt if provided
                if receipt_id is not None:
                    stmt_rcpt = (
                        update(WeAIUploadReceipt)
                        .where(WeAIUploadReceipt.id == receipt_id)
                        .values(status="CONSUMED")
                    )
                    await session.execute(stmt_rcpt)

                # H. Emit outbox event
                insert_outbox_event(
                    session,
                    event_key=f"doc:{doc_id}:rev:1",
                    event_type="document.revision.committed",
                    payload={
                        "document_id": str(doc_id),
                        "revision_id": str(rev_id),
                        "revision_no": 1,
                        "checksum_sha256": actual_sha256,
                        "format_type": format_type.value,
                        "object_key": scoped_key,
                    },
                    aggregate_type="document",
                    aggregate_id=str(doc_id),
                )

        return {
            "job_id": str(job_id),
            "document_id": str(doc_id),
            "revision_id": str(rev_id),
            "revision_no": 1,
            "checksum_sha256": actual_sha256,
            "format_type": format_type.value,
            "status": PublicationState.AVAILABLE.value,
            "object_key": scoped_key,
            "is_idempotent_replay": False,
        }

    async def get_ingestion_status(self, job_id: uuid.UUID, principal_id: str) -> dict[str, Any]:
        """Returns the current processing status of an ingestion job."""
        async with self.session_factory() as session:
            job = await session.get(WeAIIngestionJob, job_id)
            if not job:
                raise IngestionError(f"Ingestion job '{job_id}' not found.", code="JOB_NOT_FOUND", status_code=404)
            if job.principal_id != principal_id:
                raise IngestionError("Access denied to ingestion job.", code="ACCESS_DENIED", status_code=403)

            return {
                "job_id": str(job.id),
                "document_id": str(job.document_id),
                "revision_id": str(job.revision_id) if job.revision_id else None,
                "source_state": job.source_state,
                "index_state": job.index_state,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            }
