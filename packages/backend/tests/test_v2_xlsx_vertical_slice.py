"""Tests for XLSX Vertical Slice: Open WebUI -> Doc API V2 -> XLSX Engine -> Tool Result (M2.6)."""

import asyncio
import io
import json
import os
import socket
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

from open_webui.mini_hermes.v2.bootstrap import BootstrapConfig, bootstrap_v2_database
from open_webui.mini_hermes.v2.indexing import IndexLifecycleManager
from open_webui.mini_hermes.v2.ingestion import IngestionPipeline
from open_webui.mini_hermes.v2.models import (
    MhDocument,
    MhDocumentRevision,
    MhWorkbookCell,
    MhWorkbookSheet,
)
from open_webui.mini_hermes.v2.routes import (
    ReadSpreadsheetRangeRequest,
    inspect_document_endpoint,
    read_spreadsheet_range_endpoint,
    router,
    sign_cursor,
    verify_cursor,
)
from open_webui.mini_hermes.v2.storage import V2ObjectStore

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "v2"

if sys.platform == "win32":
    try:
        from open_webui.utils.windows_asyncio import install_windows_selector_policy

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
    db_url = os.getenv("MH_TEST_POSTGRES_URL")
    if not db_url:
        pytest.skip("Set MH_TEST_POSTGRES_URL to run PostgreSQL integration tests")

    url = make_url(db_url)
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        pytest.fail("MH_TEST_POSTGRES_URL must use PostgreSQL")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")

    host = url.host or "127.0.0.1"
    port = url.port or 5432
    try:
        sock = socket.create_connection((host, port), timeout=0.5)
        sock.close()
    except (OSError, socket.timeout):
        pytest.skip(f"PostgreSQL server at {host}:{port} is offline")

    schema_name = f"mh_test_vslice_{uuid.uuid4().hex}"
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


# --- Unit Tests: Wire Budget Pagination & Cursor Security (Offline) ---


def test_cursor_signing_and_verification_cycle():
    """Cursor is signed with HMAC-SHA256 and successfully verified."""
    doc_id = str(uuid.uuid4())
    rev_id = str(uuid.uuid4())
    build_id = str(uuid.uuid4())
    scope = "scope_sha256_hash"
    now_ts = int(datetime.now(timezone.utc).timestamp())

    token = sign_cursor(
        {
            "doc": doc_id,
            "rev": rev_id,
            "build": build_id,
            "scope": scope,
            "offset": 42,
            "exp": now_ts + 900,
        }
    )

    payload = verify_cursor(token, expected_scope=scope, expected_build_id=build_id)
    assert payload["offset"] == 42
    assert payload["doc"] == doc_id


def test_cursor_tampering_rejected():
    """Tampered token tag fails HMAC-SHA256 verification with HTTP 400 CURSOR_INVALID."""
    token = sign_cursor(
        {
            "doc": "d1",
            "rev": "r1",
            "build": "b1",
            "scope": "s1",
            "offset": 10,
            "exp": int(datetime.now(timezone.utc).timestamp()) + 900,
        }
    )
    tampered = token[:-4] + "ffff"

    with pytest.raises(HTTPException) as exc_info:
        verify_cursor(tampered, expected_scope="s1", expected_build_id="b1")
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "CURSOR_INVALID"


def test_cursor_expiration_rejected():
    """Expired cursor token fails verification with HTTP 410 CURSOR_EXPIRED."""
    past_ts = int(datetime.now(timezone.utc).timestamp()) - 10
    token = sign_cursor(
        {
            "doc": "d1",
            "rev": "r1",
            "build": "b1",
            "scope": "s1",
            "offset": 10,
            "exp": past_ts,
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        verify_cursor(token, expected_scope="s1", expected_build_id="b1")
    assert exc_info.value.status_code == 410
    assert exc_info.value.detail["code"] == "CURSOR_EXPIRED"


def test_cursor_scope_mismatch_rejected():
    """Cursor used on different sheet or range raises HTTP 400 CURSOR_SCOPE_MISMATCH."""
    now_ts = int(datetime.now(timezone.utc).timestamp()) + 900
    token = sign_cursor(
        {
            "doc": "d1",
            "rev": "r1",
            "build": "b1",
            "scope": "correct_scope",
            "offset": 10,
            "exp": now_ts,
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        verify_cursor(token, expected_scope="different_scope", expected_build_id="b1")
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "CURSOR_SCOPE_MISMATCH"


def test_cursor_retired_build_rejected():
    """Cursor pinned to a retired build ID raises HTTP 400 CURSOR_BUILD_RETIRED."""
    now_ts = int(datetime.now(timezone.utc).timestamp()) + 900
    token = sign_cursor(
        {
            "doc": "d1",
            "rev": "r1",
            "build": "old_build_id",
            "scope": "scope1",
            "offset": 10,
            "exp": now_ts,
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        verify_cursor(token, expected_scope="scope1", expected_build_id="active_build_id")
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "CURSOR_BUILD_RETIRED"


# --- Integration Tests: Vertical Slice Execution ---


@pytest.mark.asyncio
async def test_xlsx_vertical_slice_e2e_path(disposable_pg_schema, monkeypatch):
    """End-to-End Vertical Slice: Ingest -> Parse/Index -> Inspect -> Read Range with 5952 budget."""
    schema_url, _ = disposable_pg_schema
    await bootstrap_v2_database(BootstrapConfig(database_url=schema_url, environment_id="development"))

    engine = create_async_engine(schema_url)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    import open_webui.mini_hermes.v2.routes as v2_routes

    monkeypatch.setattr(v2_routes, "get_v2_session_factory", lambda: session_factory)

    with tempfile.TemporaryDirectory() as td:
        store = V2ObjectStore(base_dir=td, prefix="v2/")
        pipeline = IngestionPipeline(session_factory=session_factory, object_store=store)
        idx_mgr = IndexLifecycleManager(session_factory=session_factory)

        # 1. Ingest synthetic multi-sheet workbook
        xlsx_bytes = (FIXTURES_DIR / "xlsx-f05-synthetic.xlsx").read_bytes()
        ingest_res = await pipeline.start_ingestion(
            principal_id="test_principal",
            filename="xlsx-f05-synthetic.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            idempotency_key="slice_key_1",
            data=xlsx_bytes,
        )

        doc_id = uuid.UUID(ingest_res["document_id"])
        rev_id = uuid.UUID(ingest_res["revision_id"])

        # 2. Build and activate canonical index
        active = await idx_mgr.build_and_activate_canonical_index(
            document_id=doc_id,
            revision_id=rev_id,
            content_bytes=xlsx_bytes,
            format_type="xlsx",
        )
        assert active is not None

        # 3. Call Inspect endpoint
        async with session_factory() as session:
            inspect_res = await inspect_document_endpoint(
                document_id=str(doc_id),
                revision=None,
                db=session,
                principal="test_principal",
            )
            assert inspect_res["status"] == "complete"
            assert inspect_res["data"]["format_type"] == "xlsx"
            sheets = inspect_res["data"]["sheets"]
            assert len(sheets) >= 1

            target_sheet_name = sheets[0]["sheet_name"]

            # 4. Call Read Spreadsheet Range endpoint with default 5952 budget
            req_body = ReadSpreadsheetRangeRequest(
                sheet_name=target_sheet_name,
                range="A1:Z500",
                max_output_chars=5952,
            )
            page1 = await read_spreadsheet_range_endpoint(
                document_id=str(doc_id),
                body=req_body,
                db=session,
                principal="test_principal",
            )

            # Assert wire JSON pagination budget: len <= 5952
            wire_json = json.dumps(page1, ensure_ascii=False)
            assert len(wire_json) <= 5952
            assert page1["data"]["total_in_page"] > 0
            assert page1["status"] == "complete"

            # 5. Cursor continuation loop (lossless verification)
            all_collected_cells = list(page1["data"]["cells"])
            curr_cursor = page1["next_cursor"]
            seen_cursors = set()

            while curr_cursor is not None:
                assert curr_cursor not in seen_cursors, "Cursor pagination repeated a page"
                seen_cursors.add(curr_cursor)
                next_req = ReadSpreadsheetRangeRequest(
                    sheet_name=target_sheet_name,
                    range="A1:Z500",
                    cursor=curr_cursor,
                    max_output_chars=5952,
                )
                next_page = await read_spreadsheet_range_endpoint(
                    document_id=str(doc_id),
                    body=next_req,
                    db=session,
                    principal="test_principal",
                )
                assert len(json.dumps(next_page, ensure_ascii=False)) <= 5952
                assert next_page["data"]["total_in_page"] > 0
                all_collected_cells.extend(next_page["data"]["cells"])
                curr_cursor = next_page["next_cursor"]

            # Verify no duplicate cell coordinates across pages
            coords = [c["coordinate"] for c in all_collected_cells]
            assert len(coords) == len(set(coords)), "Duplicate coordinates across cursor pagination pages!"

            # 6. Verify Budget Too Small Error
            tiny_req = ReadSpreadsheetRangeRequest(
                sheet_name=target_sheet_name,
                range="A1:B10",
                max_output_chars=50,  # Far too small to fit minimal JSON envelope
            )
            with pytest.raises(HTTPException) as exc_info:
                await read_spreadsheet_range_endpoint(
                    document_id=str(doc_id),
                    body=tiny_req,
                    db=session,
                    principal="test_principal",
                )
            assert exc_info.value.status_code == 422
            assert exc_info.value.detail["code"] == "OUTPUT_BUDGET_TOO_SMALL"

    await engine.dispose()
