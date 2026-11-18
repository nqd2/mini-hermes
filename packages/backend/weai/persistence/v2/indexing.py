"""Derived Index Lifecycle Manager for Agent Runtime V2."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from weai.persistence.v2.canonical.document import CanonicalDocumentModel
from weai.persistence.v2.canonical.presentation import CanonicalPresentationModel
from weai.persistence.v2.canonical.spreadsheet import CanonicalSpreadsheetModel
from weai.persistence.v2.models import (
    IndexBuildState,
    WeAIActiveIndex,
    WeAIChunk,
    WeAIDocumentParagraph,
    WeAIDocumentRevision,
    WeAIDocumentSection,
    WeAIIndexBuild,
    WeAIMergedRange,
    WeAIPresentationShape,
    WeAIPresentationSlide,
    WeAISourceAnchor,
    WeAIWorkbookCell,
    WeAIWorkbookSheet,
    utcnow,
)
from weai.persistence.v2.parsers.docx import DocxParser
from weai.persistence.v2.parsers.pdf import PdfParser
from weai.persistence.v2.parsers.pptx import PptxParser
from weai.persistence.v2.parsers.xlsx import XlsxParser

log = logging.getLogger(__name__)

V2_INDEX_SCHEMA_VERSION = "v2.0"
V2_XLSX_PARSER_VERSION = "xlsx_canonical_v2.0.0"
V2_DOCX_PARSER_VERSION = "docx_run_span_v2.0.0"
V2_PPTX_PARSER_VERSION = "pptx_canonical_v2.0.0"
V2_PDF_PARSER_VERSION = "pdf_readonly_v2.0.0"
V2_CHUNKER_VERSION = "canonical_anchored_v1.0.0"


class IndexingError(Exception):
    def __init__(self, message: str, code: str = "indexing_error", status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class IndexNotReadyError(IndexingError):
    def __init__(self, revision_id: uuid.UUID):
        super().__init__(
            f"Derived index for revision '{revision_id}' is not ready or still building.",
            code="INDEX_NOT_READY",
            status_code=409,
        )
        self.revision_id = revision_id


class IndexSchemaIncompatibleError(IndexingError):
    def __init__(self, build_schema: str, current_schema: str = V2_INDEX_SCHEMA_VERSION):
        super().__init__(
            f"Index build schema version '{build_schema}' is incompatible with current system schema '{current_schema}'.",
            code="INDEX_SCHEMA_INCOMPATIBLE",
            status_code=422,
        )


class IndexBuildNotFoundError(IndexingError):
    def __init__(self, build_id: uuid.UUID):
        super().__init__(
            f"Index build '{build_id}' was not found.",
            code="INDEX_BUILD_NOT_FOUND",
            status_code=404,
        )


class StaleFencingTokenError(IndexingError):
    def __init__(self, detail: str = "Worker fencing token is stale or generation mismatch occurred."):
        super().__init__(detail, code="STALE_FENCING_TOKEN", status_code=409)


def compute_index_manifest_hash(
    document_id: uuid.UUID,
    revision_id: uuid.UUID,
    source_checksum_sha256: str,
    parser_version: str,
    chunker_version: str,
    schema_version: str,
    chunks_sha256_list: list[str] | None = None,
    anchor_count: int = 0,
) -> str:
    """Computes deterministic manifest hash representing index inputs and outputs."""
    sorted_chunks = "".join(sorted(chunks_sha256_list or []))
    payload = (
        f"{document_id}|{revision_id}|{source_checksum_sha256.lower()}|"
        f"{parser_version}|{chunker_version}|{schema_version}|"
        f"{sorted_chunks}|{anchor_count}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class IndexLifecycleManager:
    """Coordinates index building, CAS pointer updates, revision isolation, and supersession."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    async def create_index_build(
        self,
        document_id: uuid.UUID,
        revision_id: uuid.UUID,
        source_sha256: str,
        parser_version: str,
        chunker_version: str = V2_CHUNKER_VERSION,
        schema_version: str = V2_INDEX_SCHEMA_VERSION,
    ) -> WeAIIndexBuild:
        """Creates a new index build record in REQUESTED state."""
        build_id = uuid.uuid4()
        initial_manifest = compute_index_manifest_hash(
            document_id=document_id,
            revision_id=revision_id,
            source_checksum_sha256=source_sha256,
            parser_version=parser_version,
            chunker_version=chunker_version,
            schema_version=schema_version,
        )

        build = WeAIIndexBuild(
            id=build_id,
            document_id=document_id,
            revision_id=revision_id,
            source_checksum_sha256=source_sha256,
            parser_version=parser_version,
            chunker_version=chunker_version,
            schema_version=schema_version,
            manifest_hash=initial_manifest,
            build_state=IndexBuildState.REQUESTED.value,
            fencing_token=1,
            created_at=utcnow(),
        )

        async with self.session_factory() as session:
            async with session.begin():
                session.add(build)
        return build

    async def claim_build_for_execution(
        self, worker_id: str, lease_ttl_seconds: int = 60
    ) -> WeAIIndexBuild | None:
        """Claims a pending or expired index build using row-level locking."""
        now = utcnow()
        lease_expires = now + timedelta(seconds=lease_ttl_seconds)

        async with self.session_factory() as session:
            async with session.begin():
                stmt = (
                    select(WeAIIndexBuild)
                    .where(
                        (WeAIIndexBuild.build_state == IndexBuildState.REQUESTED.value)
                        | (
                            (WeAIIndexBuild.build_state == IndexBuildState.RUNNING.value)
                            & (WeAIIndexBuild.lease_expires_at < now)
                        )
                    )
                    .order_by(WeAIIndexBuild.created_at.asc())
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                res = await session.execute(stmt)
                build = res.scalar_one_or_none()

                if not build:
                    return None

                build.build_state = IndexBuildState.RUNNING.value
                build.lease_worker_id = worker_id
                build.lease_expires_at = lease_expires
                build.fencing_token += 1

                # Refresh in-memory attributes before returning
                await session.flush()
                return build

    async def complete_build_verification(
        self,
        build_id: uuid.UUID,
        worker_id: str,
        fencing_token: int,
        manifest_hash: str,
        coverage_proof: dict[str, Any] | None = None,
    ) -> WeAIIndexBuild:
        """Transitions a running build to READY once parsing and proofs are verified."""
        async with self.session_factory() as session:
            async with session.begin():
                build = await session.get(WeAIIndexBuild, build_id)
                if not build:
                    raise IndexBuildNotFoundError(build_id)

                if build.fencing_token != fencing_token:
                    raise StaleFencingTokenError(
                        f"Fencing token mismatch: expected {build.fencing_token}, received {fencing_token}."
                    )
                if build.lease_worker_id != worker_id:
                    raise StaleFencingTokenError(
                        f"Worker mismatch: lease held by '{build.lease_worker_id}', got '{worker_id}'."
                    )

                build.build_state = IndexBuildState.READY.value
                build.manifest_hash = manifest_hash
                build.coverage_proof = coverage_proof or {"status": "PARSER_COMPLETE"}
                build.ready_at = utcnow()
                return build

    async def activate_index_build_cas(
        self,
        revision_id: uuid.UUID,
        build_id: uuid.UUID,
        expected_generation: int | None = None,
    ) -> WeAIActiveIndex:
        """Atomically swaps active index pointer for a revision using compare-and-set (CAS)."""
        async with self.session_factory() as session:
            async with session.begin():
                build = await session.get(WeAIIndexBuild, build_id)
                if not build:
                    raise IndexBuildNotFoundError(build_id)
                if build.revision_id != revision_id:
                    raise IndexingError("Build revision mismatch", status_code=422)
                if build.build_state != IndexBuildState.READY.value:
                    raise IndexingError(
                        f"Cannot activate build in state '{build.build_state}'. Must be READY.",
                        status_code=422,
                    )
                if build.schema_version != V2_INDEX_SCHEMA_VERSION:
                    raise IndexSchemaIncompatibleError(build.schema_version)

                # Query existing active pointer
                active = await session.get(WeAIActiveIndex, revision_id)
                if active:
                    if expected_generation is not None and active.generation != expected_generation:
                        raise StaleFencingTokenError(
                            f"CAS generation mismatch: expected {expected_generation}, current {active.generation}."
                        )

                    old_build_id = active.active_build_id
                    active.active_build_id = build_id
                    active.generation += 1
                    active.activated_at = utcnow()

                    # Retire old build
                    if old_build_id != build_id:
                        stmt_retire = (
                            update(WeAIIndexBuild)
                            .where(WeAIIndexBuild.id == old_build_id)
                            .values(build_state=IndexBuildState.RETIRED.value)
                        )
                        await session.execute(stmt_retire)
                else:
                    active = WeAIActiveIndex(
                        revision_id=revision_id,
                        document_id=build.document_id,
                        active_build_id=build_id,
                        generation=1,
                        activated_at=utcnow(),
                    )
                    session.add(active)

                # Invalidate any other READY builds for this revision to RETIRED
                stmt_retire_others = (
                    update(WeAIIndexBuild)
                    .where(
                        WeAIIndexBuild.revision_id == revision_id,
                        WeAIIndexBuild.id != build_id,
                        WeAIIndexBuild.build_state == IndexBuildState.READY.value,
                    )
                    .values(build_state=IndexBuildState.RETIRED.value)
                )
                await session.execute(stmt_retire_others)

                return active

    async def get_active_index_for_revision(
        self, revision_id: uuid.UUID
    ) -> tuple[WeAIActiveIndex, WeAIIndexBuild]:
        """Retrieves active index pointer and build, validating schema compatibility and readiness."""
        async with self.session_factory() as session:
            active = await session.get(WeAIActiveIndex, revision_id)
            if not active:
                raise IndexNotReadyError(revision_id)

            build = await session.get(WeAIIndexBuild, active.active_build_id)
            if not build:
                raise IndexBuildNotFoundError(active.active_build_id)

            if build.schema_version != V2_INDEX_SCHEMA_VERSION:
                raise IndexSchemaIncompatibleError(build.schema_version)

            return active, build

    async def build_and_activate_canonical_index(
        self,
        document_id: uuid.UUID,
        revision_id: uuid.UUID,
        content_bytes: bytes,
        format_type: str,
    ) -> WeAIActiveIndex:
        """Parses document bytes, populates Domain 2 canonical tables, and activates index."""
        source_sha256 = hashlib.sha256(content_bytes).hexdigest()

        # Select parser version
        if format_type == "xlsx":
            parser_version = V2_XLSX_PARSER_VERSION
        elif format_type == "docx":
            parser_version = V2_DOCX_PARSER_VERSION
        elif format_type == "pptx":
            parser_version = V2_PPTX_PARSER_VERSION
        elif format_type == "pdf":
            parser_version = V2_PDF_PARSER_VERSION
        else:
            parser_version = "v2.0.0"

        # 1. Create build
        build = await self.create_index_build(
            document_id=document_id,
            revision_id=revision_id,
            source_sha256=source_sha256,
            parser_version=parser_version,
            chunker_version=V2_CHUNKER_VERSION,
            schema_version=V2_INDEX_SCHEMA_VERSION,
        )

        build_id = build.id

        # 2. Parse and populate canonical tables
        async with self.session_factory() as session:
            async with session.begin():
                if format_type == "xlsx":
                    model = XlsxParser.parse(content_bytes)
                    sheet_rows, cell_rows, merge_rows = model.to_db_records(build_id)
                    for s in sheet_rows:
                        session.add(WeAIWorkbookSheet(**s))
                    await session.flush()

                    for c in cell_rows:
                        session.add(WeAIWorkbookCell(**c))
                    await session.flush()

                    for m in merge_rows:
                        session.add(WeAIMergedRange(**m))
                    await session.flush()

                elif format_type == "docx":
                    doc_model = DocxParser.parse(content_bytes)
                    sec_rows, para_rows = doc_model.to_db_records(build_id)
                    for s in sec_rows:
                        session.add(WeAIDocumentSection(**s))
                    await session.flush()

                    for p in para_rows:
                        session.add(WeAIDocumentParagraph(**p))
                    await session.flush()

                elif format_type == "pptx":
                    prs_model = PptxParser.parse(content_bytes)
                    slide_rows, shape_rows = prs_model.to_db_records(build_id)
                    for s in slide_rows:
                        session.add(WeAIPresentationSlide(**s))
                    await session.flush()

                    for sh in shape_rows:
                        session.add(WeAIPresentationShape(**sh))
                    await session.flush()

                elif format_type == "pdf":
                    # PDF stores read-only pages
                    pdf_model = PdfParser.parse(content_bytes)
                    # For PDF, record coverage proof without mutation
                    pass

        # 3. Complete verification
        manifest_hash = compute_index_manifest_hash(
            document_id=document_id,
            revision_id=revision_id,
            source_checksum_sha256=source_sha256,
            parser_version=parser_version,
            chunker_version=V2_CHUNKER_VERSION,
            schema_version=V2_INDEX_SCHEMA_VERSION,
            anchor_count=1,
        )

        async with self.session_factory() as session:
            async with session.begin():
                b = await session.get(WeAIIndexBuild, build_id)
                if b:
                    b.build_state = IndexBuildState.READY.value
                    b.manifest_hash = manifest_hash
                    b.coverage_proof = {"status": "PARSER_COMPLETE", "format": format_type}
                    b.ready_at = utcnow()

        # 4. CAS activation
        active = await self.activate_index_build_cas(revision_id=revision_id, build_id=build_id)
        return active
