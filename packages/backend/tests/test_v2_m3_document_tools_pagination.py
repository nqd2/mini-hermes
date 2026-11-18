"""Milestone M3.1 Tests: 9 Canonical Document Tools, Wire JSON Budget Pagination, and HMAC-SHA256 Cursors."""

from __future__ import annotations

import hashlib
import json
import time
import uuid

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from weai.persistence.v2.models import V2Base
from weai.persistence.v2.models.auth import WeAIAccessGrant
from weai.persistence.v2.models.canonical import (
    WeAIDocumentParagraph,
    WeAIDocumentSection,
    WeAIWorkbookCell,
    WeAIWorkbookSheet,
)
from weai.persistence.v2.models.core import (
    WeAIActiveIndex,
    WeAIChunk,
    WeAIDocument,
    WeAIDocumentRevision,
    WeAIIndexBuild,
    WeAIIngestionJob,
)
from weai.persistence.v2.models.revisions import WeAIEditPreview
from weai.persistence.v2.models.workers import WeAIDocumentTask, WeAIExportJob
from weai.persistence.v2.tools import (
    CursorError,
    DEFAULT_WIRE_BUDGET_CHARS,
    ToolError,
    compute_scope_digest,
    execute_document_analysis,
    export_document,
    get_document_task,
    inspect_document,
    measure_wire_characters,
    propose_document_edit,
    read_document_range,
    read_spreadsheet_range,
    register_cursor_key,
    reset_cursor_key_registry,
    revoke_cursor_key,
    search_document,
    sign_cursor,
    start_document_summary,
    verify_cursor,
)


@pytest.fixture(autouse=True)
def _clean_cursor_keys():
    reset_cursor_key_registry()
    yield
    reset_cursor_key_registry()


async def make_v2_db(tmp_path) -> tuple[object, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'v2_m3_tools.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(V2Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


async def seed_ready_spreadsheet_and_document(
    session: AsyncSession,
    *,
    owner_id: str = "user-owner",
    tenant_id: str = "tenant-alpha",
    format_type: str = "xlsx",
    cells_data: list[tuple[int, int, str, str]] | None = None,
    paragraphs_data: list[str] | None = None,
) -> tuple[WeAIDocument, WeAIDocumentRevision, WeAIIndexBuild, WeAIWorkbookSheet]:
    doc_id = uuid.uuid4()
    rev_id = uuid.uuid4()
    build_id = uuid.uuid4()
    sheet_id = uuid.uuid4()
    section_id = uuid.uuid4()

    doc = WeAIDocument(
        id=doc_id,
        tenant_id=tenant_id,
        name=f"bao_cao_tai_chinh_2026.{format_type}",
        owner_id=owner_id,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        format_type=format_type,
        current_revision_id=rev_id,
        current_revision_no=1,
    )
    rev = WeAIDocumentRevision(
        id=rev_id,
        document_id=doc_id,
        revision_no=1,
        object_key=f"v2/objects/{doc_id}/rev1.{format_type}",
        checksum_sha256="a" * 64,
        byte_size=4096,
        source_metadata={"sheets": ["Revenue", "Summary"]},
        commit_status="COMMITTED",
        committed_by=owner_id,
    )
    build = WeAIIndexBuild(
        id=build_id,
        document_id=doc_id,
        revision_id=rev_id,
        source_checksum_sha256="a" * 64,
        parser_version="v2.0",
        chunker_version="v2.0",
        schema_version="v2.0",
        manifest_hash="b" * 64,
        build_state="READY",
        coverage_proof={"parser_complete": True, "index_complete": True},
    )
    active = WeAIActiveIndex(
        revision_id=rev_id,
        document_id=doc_id,
        active_build_id=build_id,
        generation=1,
    )
    sheet = WeAIWorkbookSheet(
        id=sheet_id,
        index_build_id=build_id,
        sheet_name="Revenue",
        sheet_order=0,
        visibility="visible",
        max_row=200,
        max_col=10,
    )
    section = WeAIDocumentSection(
        id=section_id,
        index_build_id=build_id,
        section_order=1,
        header_text="Executive Overview",
        footer_text="Page 1",
    )
    session.add_all([doc, rev, build, active, sheet, section])

    if cells_data is None:
        cells_data = [
            (r, 1, f"A{r}", f"Khoản mục doanh thu số {r}: {r * 1000} VND")
            for r in range(1, 31)
        ]
    for r_idx, c_idx, coord, text_val in cells_data:
        session.add(
            WeAIWorkbookCell(
                id=uuid.uuid4(),
                index_build_id=build_id,
                sheet_id=sheet_id,
                row_idx=r_idx,
                col_idx=c_idx,
                cell_coordinate=coord,
                data_type="s",
                raw_value=text_val,
                cached_value=text_val,
                cached_value_state="VALID",
            )
        )

    if paragraphs_data is None:
        paragraphs_data = [
            f"Đoạn văn bản phân tích chi tiết thứ {i}: Tăng trưởng ổn định trong quý {i}."
            for i in range(1, 16)
        ]
    for idx, p_text in enumerate(paragraphs_data, start=1):
        session.add(
            WeAIDocumentParagraph(
                id=uuid.uuid4(),
                section_id=section_id,
                paragraph_order=idx,
                raw_text=p_text,
                style_id="Normal",
            )
        )
        session.add(
            WeAIChunk(
                id=uuid.uuid4(),
                index_build_id=build_id,
                stable_ordinal=idx,
                content_sha256=hashlib.sha256(p_text.encode("utf-8")).hexdigest(),
                token_count=len(p_text.split()),
                raw_text=p_text,
            )
        )

    await session.commit()
    return doc, rev, build, sheet


@pytest.mark.asyncio
async def test_all_9_canonical_document_tools_end_to_end(tmp_path):
    engine, factory = await make_v2_db(tmp_path)
    try:
        async with factory() as session:
            doc, rev, build, _ = await seed_ready_spreadsheet_and_document(session)

            # 1. inspect_document
            info = await inspect_document(
                str(doc.id),
                session=session,
                principal_id="user-owner",
                tenant_id="tenant-alpha",
            )
            assert info["document_id"] == str(doc.id)
            assert info["revision_id"] == str(rev.id)
            assert info["index_build_id"] == str(build.id)
            assert info["data"]["index_status"] == "READY"
            assert len(info["data"]["sheets"]) == 1
            assert info["data"]["sheets"][0]["sheet_name"] == "Revenue"
            assert measure_wire_characters(info) <= DEFAULT_WIRE_BUDGET_CHARS

            # 2. read_document_range
            doc_page = await read_document_range(
                str(doc.id),
                start=1,
                end=10,
                session=session,
                principal_id="user-owner",
                tenant_id="tenant-alpha",
            )
            assert doc_page["status"] == "complete"
            assert len(doc_page["data"]["items"]) == 10
            assert doc_page["data"]["retrieval_coverage"]["coverage_type"] == "retrieval_coverage"
            assert doc_page["data"]["retrieval_coverage"]["processed_count"] == 10
            assert measure_wire_characters(doc_page) <= DEFAULT_WIRE_BUDGET_CHARS

            # 3. read_spreadsheet_range
            sheet_page = await read_spreadsheet_range(
                str(doc.id),
                range="A1:A15",
                sheet_name="Revenue",
                session=session,
                principal_id="user-owner",
                tenant_id="tenant-alpha",
            )
            assert sheet_page["status"] == "complete"
            assert len(sheet_page["data"]["cells"]) == 15
            assert sheet_page["data"]["retrieval_coverage"]["coverage_type"] == "retrieval_coverage"
            assert measure_wire_characters(sheet_page) <= DEFAULT_WIRE_BUDGET_CHARS

            # 4. search_document
            search_res = await search_document(
                str(doc.id),
                query="doanh thu số 12",
                session=session,
                principal_id="user-owner",
                tenant_id="tenant-alpha",
            )
            assert search_res["status"] == "complete"
            assert search_res["data"]["total_matches"] >= 1
            assert any("doanh thu số 12" in item["raw_value"] for item in search_res["data"]["matches"])
            assert measure_wire_characters(search_res) <= DEFAULT_WIRE_BUDGET_CHARS

            # 5. execute_document_analysis (idempotent job creation)
            analysis_1 = await execute_document_analysis(
                str(doc.id),
                goal="Tính tổng doanh thu toàn bảng",
                analysis_spec={"scope": "Revenue!A1:A30", "aggregation": "sum"},
                idempotency_key="idem-analysis-1",
                session=session,
                principal_id="user-owner",
                tenant_id="tenant-alpha",
            )
            analysis_2 = await execute_document_analysis(
                str(doc.id),
                goal="Tính tổng doanh thu toàn bảng",
                analysis_spec={"scope": "Revenue!A1:A30", "aggregation": "sum"},
                idempotency_key="idem-analysis-1",
                session=session,
                principal_id="user-owner",
                tenant_id="tenant-alpha",
            )
            assert analysis_1["data"]["task_id"] == analysis_2["data"]["task_id"]
            assert analysis_1["status"] == "pending"

            # 6. start_document_summary (idempotent job creation)
            summary_1 = await start_document_summary(
                str(doc.id),
                summary_spec={"scope": "executive"},
                idempotency_key="idem-summary-1",
                session=session,
                principal_id="user-owner",
                tenant_id="tenant-alpha",
            )
            assert summary_1["status"] == "pending"
            assert summary_1["data"]["task_id"] is not None

            # 7. get_document_task
            task_status = await get_document_task(
                analysis_1["data"]["task_id"],
                session=session,
                principal_id="user-owner",
                tenant_id="tenant-alpha",
            )
            assert task_status["data"]["task_id"] == analysis_1["data"]["task_id"]
            assert task_status["data"]["task_type"] == "analysis"
            assert task_status["data"]["status"] == "pending"

            # 8. export_document
            export_res = await export_document(
                str(doc.id),
                format="pdf",
                idempotency_key="idem-export-1",
                session=session,
                principal_id="user-owner",
                tenant_id="tenant-alpha",
            )
            assert export_res["data"]["format"] == "pdf"
            export_job_row = await session.get(WeAIExportJob, uuid.UUID(export_res["data"]["export_job_id"]))
            assert export_job_row is not None
            assert export_job_row.target_format == "pdf"

            # 9. propose_document_edit (creates preview, never commits revision directly)
            edit_res = await propose_document_edit(
                str(doc.id),
                base_revision=1,
                operations=[
                    {
                        "op": "set_cell_value",
                        "sheet_name": "Revenue",
                        "coordinate": "A1",
                        "value": "Khoản mục đã cập nhật",
                    }
                ],
                idempotency_key="idem-edit-1",
                session=session,
                principal_id="user-owner",
                tenant_id="tenant-alpha",
            )
            assert edit_res["status"] == "ready"
            assert len(edit_res["data"]["patch_hash"]) == 64
            preview_row = await session.get(WeAIEditPreview, uuid.UUID(edit_res["data"]["preview_id"]))
            assert preview_row is not None
            assert preview_row.canonical_patch_hash == edit_res["data"]["patch_hash"]
            # Verify document revision was NOT mutated
            await session.refresh(doc)
            assert doc.current_revision_no == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_index_not_ready_returns_existing_job_without_creating_or_retrying_jobs(tmp_path):
    engine, factory = await make_v2_db(tmp_path)
    try:
        async with factory() as session:
            doc_id = uuid.uuid4()
            rev_id = uuid.uuid4()
            job_id = uuid.uuid4()
            build_id = uuid.uuid4()

            doc = WeAIDocument(
                id=doc_id,
                tenant_id="tenant-alpha",
                name="pending_doc.xlsx",
                owner_id="user-1",
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                format_type="xlsx",
                current_revision_id=rev_id,
                current_revision_no=1,
            )
            rev = WeAIDocumentRevision(
                id=rev_id,
                document_id=doc_id,
                revision_no=1,
                object_key="v2/objects/pending.xlsx",
                checksum_sha256="c" * 64,
                byte_size=1024,
                commit_status="COMMITTED",
                committed_by="user-1",
            )
            job = WeAIIngestionJob(
                id=job_id,
                document_id=doc_id,
                revision_id=rev_id,
                principal_id="user-1",
                idempotency_scope="upload",
                idempotency_key="upload-1",
                request_fingerprint="d" * 64,
                source_state="COMMITTED",
                index_state="INDEXING",
            )
            build = WeAIIndexBuild(
                id=build_id,
                document_id=doc_id,
                revision_id=rev_id,
                source_checksum_sha256="c" * 64,
                parser_version="v2.0",
                chunker_version="v2.0",
                schema_version="v2.0",
                manifest_hash="e" * 64,
                build_state="BUILDING",
            )
            session.add_all([doc, rev, job, build])
            await session.commit()

            jobs_before = await session.scalar(select(func.count()).select_from(WeAIIngestionJob))
            builds_before = await session.scalar(select(func.count()).select_from(WeAIIndexBuild))

            for coro in (
                read_document_range(
                    str(doc_id),
                    session=session,
                    principal_id="user-1",
                    tenant_id="tenant-alpha",
                ),
                read_spreadsheet_range(
                    str(doc_id),
                    range="A1:B10",
                    sheet_name="Sheet1",
                    session=session,
                    principal_id="user-1",
                    tenant_id="tenant-alpha",
                ),
                search_document(
                    str(doc_id),
                    query="test",
                    session=session,
                    principal_id="user-1",
                    tenant_id="tenant-alpha",
                ),
            ):
                with pytest.raises(ToolError) as exc_info:
                    await coro
                assert exc_info.value.code == "INDEX_NOT_READY"
                assert exc_info.value.details["job_id"] == str(job_id)
                assert exc_info.value.details["build_id"] == str(build_id)

            jobs_after = await session.scalar(select(func.count()).select_from(WeAIIngestionJob))
            builds_after = await session.scalar(select(func.count()).select_from(WeAIIndexBuild))
            assert jobs_after == jobs_before == 1
            assert builds_after == builds_before == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_wire_json_budget_pagination_whole_cell_and_oversized_single_cell_slicing(tmp_path):
    engine, factory = await make_v2_db(tmp_path)
    try:
        async with factory() as session:
            # Create 1 oversized cell (11,500 chars with Vietnamese diacritics and multi-byte emoji)
            # plus normal cells before and after
            giant_text = ("Báo cáo kiểm toán độc lập 📊🔍 năm 2026 — " * 300)[:11500]
            cells = [
                (1, 1, "A1", "Tiêu đề báo cáo tài chính hợp nhất"),
                (2, 1, "A2", giant_text),
                (3, 1, "A3", "Kết luận kiểm toán: Chấp nhận toàn phần"),
            ]
            doc, _, _, _ = await seed_ready_spreadsheet_and_document(session, cells_data=cells)

            reconstructed_a2 = []
            seen_a1 = False
            seen_a3 = False
            cursor = None
            page_count = 0

            while True:
                page = await read_spreadsheet_range(
                    str(doc.id),
                    range="A1:A3",
                    sheet_name="Revenue",
                    cursor=cursor,
                    session=session,
                    principal_id="user-owner",
                    tenant_id="tenant-alpha",
                    max_output_chars=DEFAULT_WIRE_BUDGET_CHARS,
                )
                page_count += 1
                wire_chars = len(json.dumps(page, ensure_ascii=False))
                assert wire_chars <= DEFAULT_WIRE_BUDGET_CHARS, (
                    f"Page {page_count} exceeded wire budget: {wire_chars} > {DEFAULT_WIRE_BUDGET_CHARS}"
                )

                for item in page["data"]["cells"]:
                    if item["coordinate"] == "A1":
                        assert "partial_fragment" not in page["data"]
                        seen_a1 = True
                    elif item["coordinate"] == "A2":
                        reconstructed_a2.append(item["raw_value"])
                    elif item["coordinate"] == "A3":
                        if "partial_fragment" in page["data"]:
                            assert page["data"]["partial_fragment"]["item_ordinal"] != 2
                        seen_a3 = True

                cursor = page["next_cursor"]
                if page["status"] == "complete":
                    assert cursor is None
                    break
                assert page["status"] == "partial"
                assert cursor is not None and cursor.startswith("mhv2c1.")

            assert page_count >= 4
            assert seen_a1 is True
            assert seen_a3 is True
            assert "".join(reconstructed_a2) == giant_text

            # Verify minimal envelope budget error when max_output_chars is impossibly small
            with pytest.raises(ToolError) as exc_info:
                await read_spreadsheet_range(
                    str(doc.id),
                    range="A1:A3",
                    sheet_name="Revenue",
                    session=session,
                    principal_id="user-owner",
                    tenant_id="tenant-alpha",
                    max_output_chars=120,
                )
            assert exc_info.value.code == "OUTPUT_BUDGET_TOO_SMALL"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cursor_hmac_security_key_rotation_and_permission_reverification_on_continuation(tmp_path):
    engine, factory = await make_v2_db(tmp_path)
    try:
        async with factory() as session:
            cells = [(r, 1, f"A{r}", "Dữ liệu dòng " + ("X" * 900) + f" #{r}") for r in range(1, 15)]
            doc, rev, build, _ = await seed_ready_spreadsheet_and_document(
                session,
                owner_id="user-owner",
                tenant_id="tenant-alpha",
                cells_data=cells,
            )

            reader_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, "agent-runtime.v2.principal:user-reader")
            owner_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, "agent-runtime.v2.principal:user-owner")

            # Grant READ access to user-reader
            grant = WeAIAccessGrant(
                id=uuid.uuid4(),
                resource_type="document",
                resource_id=doc.id,
                grantee_type="user",
                grantee_id=reader_uuid,
                permission="read",
                granted_by=owner_uuid,
            )
            session.add(grant)
            await session.commit()

            # Page 1 fetched by user-reader succeeds and issues a signed cursor
            page1 = await read_spreadsheet_range(
                str(doc.id),
                range="A1:A14",
                sheet_name="Revenue",
                session=session,
                principal_id="user-reader",
                tenant_id="tenant-alpha",
                max_output_chars=3000,
            )
            assert page1["status"] == "partial"
            cursor = page1["next_cursor"]
            assert cursor is not None

            # Another user attempting to replay user-reader's cursor is rejected (CURSOR_SCOPE_MISMATCH)
            with pytest.raises(ToolError) as scope_exc:
                await read_spreadsheet_range(
                    str(doc.id),
                    range="A1:A14",
                    sheet_name="Revenue",
                    cursor=cursor,
                    session=session,
                    principal_id="user-owner",
                    tenant_id="tenant-alpha",
                    max_output_chars=3000,
                )
            assert scope_exc.value.code == "CURSOR_SCOPE_MISMATCH"

            # Revoke user-reader's grant before page 2 -> must re-verify permission and fail with FORBIDDEN!
            await session.execute(delete(WeAIAccessGrant).where(WeAIAccessGrant.id == grant.id))
            await session.commit()

            with pytest.raises(ToolError) as perm_exc:
                await read_spreadsheet_range(
                    str(doc.id),
                    range="A1:A14",
                    sheet_name="Revenue",
                    cursor=cursor,
                    session=session,
                    principal_id="user-reader",
                    tenant_id="tenant-alpha",
                    max_output_chars=3000,
                )
            assert perm_exc.value.code == "FORBIDDEN"

            # Test key rotation and key revocation
            register_cursor_key("k2026_q4", b"new-rotated-secret-key-32-bytes!", state="active")
            now_epoch = int(time.time())
            scope_sha = compute_scope_digest(
                operation="read_spreadsheet_range",
                document_id=str(doc.id),
                revision=rev.revision_no,
                source_sha256=rev.checksum_sha256,
                build_id=str(build.id),
                scope={"sheet_name": "Revenue", "range": "A1:A14"},
            )
            rotated_token = sign_cursor(
                principal_id="user-owner",
                tenant_id="tenant-alpha",
                operation="read_spreadsheet_range",
                document_id=str(doc.id),
                revision=rev.revision_no,
                source_sha256=rev.checksum_sha256,
                build_id=str(build.id),
                scope_sha256=scope_sha,
                item_ordinal=2,
                fragment=None,
                kid="k2026_q4",
                now=now_epoch,
            )
            verified = verify_cursor(
                rotated_token,
                expected_principal_id="user-owner",
                expected_tenant_id="tenant-alpha",
                expected_operation="read_spreadsheet_range",
                expected_document_id=str(doc.id),
                expected_revision=rev.revision_no,
                expected_source_sha256=rev.checksum_sha256,
                expected_build_id=str(build.id),
                expected_scope_sha256=scope_sha,
                now=now_epoch + 10,
            )
            assert verified["position"]["item_ordinal"] == 2

            # Expired cursor check
            with pytest.raises(CursorError) as exp_exc:
                verify_cursor(
                    rotated_token,
                    expected_principal_id="user-owner",
                    expected_tenant_id="tenant-alpha",
                    expected_operation="read_spreadsheet_range",
                    expected_document_id=str(doc.id),
                    expected_revision=rev.revision_no,
                    expected_source_sha256=rev.checksum_sha256,
                    expected_build_id=str(build.id),
                    expected_scope_sha256=scope_sha,
                    now=now_epoch + 901,
                )
            assert exp_exc.value.code == "CURSOR_EXPIRED"

            # Retired build check
            with pytest.raises(CursorError) as build_exc:
                verify_cursor(
                    rotated_token,
                    expected_principal_id="user-owner",
                    expected_tenant_id="tenant-alpha",
                    expected_operation="read_spreadsheet_range",
                    expected_document_id=str(doc.id),
                    expected_revision=rev.revision_no,
                    expected_source_sha256=rev.checksum_sha256,
                    expected_build_id=str(uuid.uuid4()),
                    expected_scope_sha256=scope_sha,
                    now=now_epoch + 10,
                )
            assert build_exc.value.code == "CURSOR_BUILD_RETIRED"

            # Revoke key k2026_q4 -> immediately rejected with CURSOR_INVALID
            revoke_cursor_key("k2026_q4")
            with pytest.raises(CursorError) as rev_exc:
                verify_cursor(
                    rotated_token,
                    expected_principal_id="user-owner",
                    expected_tenant_id="tenant-alpha",
                    expected_operation="read_spreadsheet_range",
                    expected_document_id=str(doc.id),
                    expected_revision=rev.revision_no,
                    expected_source_sha256=rev.checksum_sha256,
                    expected_build_id=str(build.id),
                    expected_scope_sha256=scope_sha,
                    now=now_epoch + 10,
                )
            assert rev_exc.value.code == "CURSOR_INVALID"
    finally:
        await engine.dispose()
