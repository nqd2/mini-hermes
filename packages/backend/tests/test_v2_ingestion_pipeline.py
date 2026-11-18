"""Tests for Agent Runtime V2 Checksummed Ingestion Pipeline and Resource Guards (M2.1)."""

import asyncio
import hashlib
import io
import os
import socket
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from weai.persistence.v2.bootstrap import BootstrapConfig, bootstrap_v2_database
from weai.persistence.v2.ingestion import (
    ChecksumMismatchError,
    CorruptFileError,
    FileSizeExceededError,
    FormatSignatureMismatchError,
    FormatSniffer,
    IdempotencyConflictError,
    IngestionPipeline,
    UnsupportedFormatError,
    ZipBombDetectedError,
)
from weai.persistence.v2.models import (
    FormatType,
    WeAIDocument,
    WeAIDocumentRevision,
    WeAIIngestionJob,
    WeAIOutbox,
)
from weai.persistence.v2.storage import V2ObjectStore

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "v2"

if sys.platform == "win32":
    try:
        from weai.utils.windows_asyncio import install_windows_selector_policy

        install_windows_selector_policy()
    except ImportError:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(scope="module")
def event_loop_policy():
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


@pytest_asyncio.fixture
async def disposable_pg_schema():
    """Fixture providing an isolated temporary PostgreSQL schema for integration tests."""
    db_url = os.getenv("AGENT_RUNTIME_TEST_POSTGRES_URL")
    if not db_url:
        pytest.skip("Set AGENT_RUNTIME_TEST_POSTGRES_URL to run PostgreSQL integration tests")

    url = make_url(db_url)
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        pytest.fail("AGENT_RUNTIME_TEST_POSTGRES_URL must use PostgreSQL")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")

    host = url.host or "127.0.0.1"
    port = url.port or 5432
    try:
        sock = socket.create_connection((host, port), timeout=0.5)
        sock.close()
    except (OSError, socket.timeout):
        pytest.skip(f"PostgreSQL server at {host}:{port} is offline")

    schema_name = f"ar_test_ingest_{uuid.uuid4().hex}"
    admin_engine = create_async_engine(url, pool_pre_ping=True)
    schema_url = url.set(query={**dict(url.query), "options": f"-csearch_path={schema_name}"})

    try:
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema_name}"'))

        yield schema_url.render_as_string(hide_password=False), schema_name
    finally:
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
        await admin_engine.dispose()


# --- Unit Tests: Sniffer, Size Limits, and Zip Bomb Defense (Offline) ---


def test_format_sniffer_identifies_valid_formats():
    """FormatSniffer detects XLSX, DOCX, PPTX, and PDF from bytes."""
    xlsx_bytes = (FIXTURES_DIR / "xlsx-f05-synthetic.xlsx").read_bytes()
    docx_bytes = (FIXTURES_DIR / "docx-f01-structured.docx").read_bytes()
    pptx_bytes = (FIXTURES_DIR / "pptx-f01-structured.pptx").read_bytes()
    pdf_bytes = (FIXTURES_DIR / "pdf-f01-text-and-image.pdf").read_bytes()

    assert FormatSniffer.sniff_format(xlsx_bytes, "doc.xlsx") == FormatType.XLSX
    assert FormatSniffer.sniff_format(docx_bytes, "doc.docx") == FormatType.DOCX
    assert FormatSniffer.sniff_format(pptx_bytes, "doc.pptx") == FormatType.PPTX
    assert FormatSniffer.sniff_format(pdf_bytes, "doc.pdf") == FormatType.PDF


def test_format_sniffer_rejects_extension_spoofing():
    """Rejects spoofed extensions and plain text files with FormatSignatureMismatchError."""
    # Plain text claiming to be xlsx
    with pytest.raises(FormatSignatureMismatchError):
        FormatSniffer.sniff_format(b"Hello world, I am text", "invoice.xlsx")

    # Plain text claiming to be pdf
    with pytest.raises(FormatSignatureMismatchError):
        FormatSniffer.sniff_format(b"Hello world, not a pdf", "paper.pdf")

    # Zip without [Content_Types].xml claiming to be docx
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("evil.exe", b"\x4d\x5a\x90")
    with pytest.raises(FormatSignatureMismatchError):
        FormatSniffer.sniff_format(buf.getvalue(), "doc.docx")

    # Unsupported extension
    with pytest.raises(UnsupportedFormatError):
        FormatSniffer.sniff_format(b"some content", "file.exe")


def test_format_sniffer_detects_zip_bomb():
    """Zip bomb with >100:1 compression ratio is rejected before decompression."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", b"<Types/>")
        z.writestr("xl/workbook.xml", b"<workbook/>")
        z.writestr("huge_zeroes.dat", b"\x00" * (5 * 1024 * 1024))

    bomb_bytes = buf.getvalue()
    with pytest.raises(ZipBombDetectedError):
        FormatSniffer.sniff_format(bomb_bytes, "bomb.xlsx")


def test_format_sniffer_detects_corrupt_zip():
    """Truncated or corrupted zip container raises CorruptFileError."""
    truncated_zip = b"PK\x03\x04\x14\x00\x00\x00truncated"
    with pytest.raises(CorruptFileError):
        FormatSniffer.sniff_format(truncated_zip, "corrupt.xlsx")


@pytest.mark.asyncio
async def test_ingest_enforces_file_size_limits():
    """Pipeline rejects files exceeding 50MB before parsing or writing."""
    dummy_factory = async_sessionmaker()
    pipeline = IngestionPipeline(session_factory=dummy_factory)

    oversized = b"X" * (50 * 1024 * 1024 + 1)
    with pytest.raises(FileSizeExceededError) as exc_info:
        await pipeline.start_ingestion(
            principal_id="test_principal",
            filename="huge.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            idempotency_key="key_oversized",
            data=oversized,
        )
    assert exc_info.value.status_code == 413


# --- Integration Tests with Disposable PostgreSQL ---


@pytest.mark.asyncio
async def test_ingest_computes_exact_sha256_and_publishes(disposable_pg_schema):
    """Verifies that ingestion computes exact SHA-256 and atomically publishes revision."""
    schema_url, _ = disposable_pg_schema
    await bootstrap_v2_database(BootstrapConfig(database_url=schema_url, environment_id="development"))

    engine = create_async_engine(schema_url)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    with tempfile.TemporaryDirectory() as td:
        store = V2ObjectStore(base_dir=td, prefix="v2/")
        pipeline = IngestionPipeline(session_factory=session_factory, object_store=store)

        xlsx_bytes = (FIXTURES_DIR / "xlsx-f05-synthetic.xlsx").read_bytes()
        expected_hash = "e85ad43cf67e3cb70576e6ad19e0f07452d00a1e9f1c7dc169dc1c9b248248be"

        result = await pipeline.start_ingestion(
            principal_id="test_principal",
            filename="xlsx-f05-synthetic.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            idempotency_key="key_001",
            data=xlsx_bytes,
        )

        assert result["checksum_sha256"] == expected_hash
        assert result["status"] == "AVAILABLE"
        assert result["revision_no"] == 1
        assert result["is_idempotent_replay"] is False

        # Verify DB records
        doc_id = uuid.UUID(result["document_id"])
        rev_id = uuid.UUID(result["revision_id"])

        async with session_factory() as session:
            doc = await session.get(WeAIDocument, doc_id)
            assert doc is not None
            assert doc.current_revision_id == rev_id
            assert doc.current_revision_no == 1
            assert doc.format_type == "xlsx"

            rev = await session.get(WeAIDocumentRevision, rev_id)
            assert rev is not None
            assert rev.checksum_sha256 == expected_hash
            assert rev.commit_status == "COMMITTED"

            # Check outbox event
            stmt_outbox = select(WeAIOutbox).where(WeAIOutbox.aggregate_id == str(doc_id))
            res_outbox = await session.execute(stmt_outbox)
            outbox_ev = res_outbox.scalar_one_or_none()
            assert outbox_ev is not None
            assert outbox_ev.event_type == "document.revision.committed"

    await engine.dispose()


@pytest.mark.asyncio
async def test_ingest_writes_immutable_bytes_to_object_store(disposable_pg_schema):
    """Verifies that bytes stored in V2ObjectStore match uploaded bytes exactly."""
    schema_url, _ = disposable_pg_schema
    await bootstrap_v2_database(BootstrapConfig(database_url=schema_url, environment_id="development"))

    engine = create_async_engine(schema_url)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    with tempfile.TemporaryDirectory() as td:
        store = V2ObjectStore(base_dir=td, prefix="v2/")
        pipeline = IngestionPipeline(session_factory=session_factory, object_store=store)

        docx_bytes = (FIXTURES_DIR / "docx-f01-structured.docx").read_bytes()
        res = await pipeline.start_ingestion(
            principal_id="test_principal",
            filename="docx-f01-structured.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            idempotency_key="key_002",
            data=docx_bytes,
        )

        object_key = res["object_key"]
        assert object_key.startswith("v2/blobs/")
        readback = store.get_bytes(object_key)
        assert readback == docx_bytes

    await engine.dispose()


@pytest.mark.asyncio
async def test_ingest_idempotency_and_conflict(disposable_pg_schema):
    """Same idempotency key with identical request replays; different content raises 409 Conflict."""
    schema_url, _ = disposable_pg_schema
    await bootstrap_v2_database(BootstrapConfig(database_url=schema_url, environment_id="development"))

    engine = create_async_engine(schema_url)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    with tempfile.TemporaryDirectory() as td:
        store = V2ObjectStore(base_dir=td, prefix="v2/")
        pipeline = IngestionPipeline(session_factory=session_factory, object_store=store)

        pptx_bytes = (FIXTURES_DIR / "pptx-f01-structured.pptx").read_bytes()
        key = "idemp_test_key"

        # 1. First execution
        res1 = await pipeline.start_ingestion(
            principal_id="test_principal",
            filename="pptx-f01-structured.pptx",
            content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            idempotency_key=key,
            data=pptx_bytes,
        )
        assert res1["is_idempotent_replay"] is False

        # 2. Replay with identical key & data
        res2 = await pipeline.start_ingestion(
            principal_id="test_principal",
            filename="pptx-f01-structured.pptx",
            content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            idempotency_key=key,
            data=pptx_bytes,
        )
        assert res2["is_idempotent_replay"] is True
        assert res2["job_id"] == res1["job_id"]
        assert res2["document_id"] == res1["document_id"]

        # 3. Conflict: same key with different data
        pdf_bytes = (FIXTURES_DIR / "pdf-f01-text-and-image.pdf").read_bytes()
        with pytest.raises(IdempotencyConflictError) as exc_info:
            await pipeline.start_ingestion(
                principal_id="test_principal",
                filename="pdf-f01-text-and-image.pdf",
                content_type="application/pdf",
                idempotency_key=key,
                data=pdf_bytes,
            )
        assert exc_info.value.status_code == 409

    await engine.dispose()


@pytest.mark.asyncio
async def test_ingest_checksum_mismatch(disposable_pg_schema):
    """Mismatched expected_sha256 raises ChecksumMismatchError with HTTP 422."""
    schema_url, _ = disposable_pg_schema
    await bootstrap_v2_database(BootstrapConfig(database_url=schema_url, environment_id="development"))

    engine = create_async_engine(schema_url)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    with tempfile.TemporaryDirectory() as td:
        store = V2ObjectStore(base_dir=td, prefix="v2/")
        pipeline = IngestionPipeline(session_factory=session_factory, object_store=store)

        pdf_bytes = (FIXTURES_DIR / "pdf-f01-text-and-image.pdf").read_bytes()
        bad_hash = "0" * 64

        with pytest.raises(ChecksumMismatchError) as exc_info:
            await pipeline.start_ingestion(
                principal_id="test_principal",
                filename="pdf-f01-text-and-image.pdf",
                content_type="application/pdf",
                idempotency_key="key_bad_hash",
                data=pdf_bytes,
                expected_sha256=bad_hash,
            )
        assert exc_info.value.status_code == 422

    await engine.dispose()
