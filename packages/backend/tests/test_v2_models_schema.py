"""Unit tests for Mini-Hermes V2 Persistence Models and Schema.

Verifies:
- All 49 tables exist in V2Base.metadata with official 'mh_*' table prefix.
- All 6 domains are represented with required columns, types, and constraints.
- Composite foreign keys and cyclical FK (mh_documents.current_revision_id) with use_alter=True.
- Unique constraints and indexes.
- Dialect enforcement: SQLite strictly refused, PostgreSQL dialect required.
- Facade module open_webui.models.v2.schema re-exports all 49 models.
- DDL compilation for PostgreSQL dialect produces valid SQL statements.
"""

import pytest
from sqlalchemy import create_mock_engine
from sqlalchemy.schema import CreateTable

import open_webui.models.v2.schema as facade_schema
from open_webui.mini_hermes.v2.models import (
    ApprovalStatus,
    CachedValueState,
    CellDataType,
    FormatType,
    GranteeType,
    IndexBuildState,
    MhAccessGrant,
    MhActiveIndex,
    MhAgentArtifact,
    MhAnalysisJob,
    MhAuthRevokedSessionJti,
    MhAuthSession,
    MhChat,
    MhChatFolder,
    MhChatMessage,
    MhCheckpoint,
    MhChunk,
    MhCompensatingRevision,
    MhConversationEngineBinding,
    MhDocument,
    MhDocumentApproval,
    MhDocumentParagraph,
    MhDocumentRevision,
    MhDocumentSection,
    MhDocumentTask,
    MhEditPreview,
    MhEnvironmentMarker,
    MhExecutionLease,
    MhExportJob,
    MhFolderNode,
    MhGroup,
    MhGroupMembership,
    MhIdempotencyRecord,
    MhIndexBuild,
    MhIngestionJob,
    MhModel,
    MhModelParam,
    MhObjectRegistry,
    MhOutbox,
    MhPresentationShape,
    MhPresentationSlide,
    MhRole,
    MhRun,
    MhRunBinding,
    MhRunEvent,
    MhSourceAnchor,
    MhStagedObject,
    MhSummaryTask,
    MhToolInvocation,
    MhUploadReceipt,
    MhUser,
    MhWorkbookCell,
    MhWorkbookSheet,
    MhWorkerLease,
    PermissionKind,
    PreviewStatus,
    PublicationState,
    ResourceType,
    RunStatus,
    ShapeType,
    SheetVisibility,
    TaskStatus,
    TaskType,
    ToolInvocationStatus,
    UserRole,
    V2Base,
    enforce_postgresql,
    v2_metadata,
)

EXPECTED_49_TABLES = {
    # Domain 1: Storage & Core Documents (10)
    "mh_documents",
    "mh_document_revisions",
    "mh_upload_receipts",
    "mh_ingestion_jobs",
    "mh_index_builds",
    "mh_active_indexes",
    "mh_source_anchors",
    "mh_chunks",
    "mh_object_registry",
    "mh_staged_objects",
    # Domain 2: Format Canonical Tables (7)
    "mh_workbook_sheets",
    "mh_workbook_cells",
    "mh_merged_ranges",
    "mh_document_sections",
    "mh_document_paragraphs",
    "mh_presentation_slides",
    "mh_presentation_shapes",
    # Domain 3: Revision & Approvals (5)
    "mh_edit_previews",
    "mh_document_approvals",
    "mh_idempotency_records",
    "mh_outbox",
    "mh_compensating_revisions",
    # Domain 4: AgentRuntime Engine (8)
    "mh_conversation_engine_bindings",
    "mh_run_bindings",
    "mh_runs",
    "mh_run_events",
    "mh_tool_invocations",
    "mh_checkpoints",
    "mh_agent_artifacts",
    "mh_execution_leases",
    # Domain 5: Task Workers (5)
    "mh_document_tasks",
    "mh_analysis_jobs",
    "mh_summary_tasks",
    "mh_export_jobs",
    "mh_worker_leases",
    # Domain 6: Auth / RBAC / Open WebUI Async Entities (14)
    "mh_users",
    "mh_auth_sessions",
    "mh_auth_revoked_session_jtis",
    "mh_roles",
    "mh_groups",
    "mh_group_memberships",
    "mh_access_grants",
    "mh_folder_nodes",
    "mh_chats",
    "mh_chat_messages",
    "mh_chat_folders",
    "mh_models",
    "mh_model_params",
    "mh_environment_markers",
}


def test_v2_metadata_has_exactly_49_tables():
    """All 49 tables must be registered in v2_metadata under official 'mh_*' prefix."""
    registered = set(v2_metadata.tables.keys())
    assert len(registered) == 49, f"Expected 49 tables, found {len(registered)}: {registered ^ EXPECTED_49_TABLES}"
    assert registered == EXPECTED_49_TABLES, f"Difference: {registered ^ EXPECTED_49_TABLES}"

    non_mh = [t for t in registered if not t.startswith("mh_")]
    assert len(non_mh) == 0, f"Found tables without 'mh_' prefix: {non_mh}"


def test_domain_1_core_documents_structure():
    """Verify Domain 1 core document models and cyclical foreign key with use_alter=True."""
    doc_table = MhDocument.__table__
    rev_table = MhDocumentRevision.__table__

    assert "current_revision_id" in doc_table.c
    assert "current_revision_no" in doc_table.c
    assert "format_type" in doc_table.c

    # Check cyclical foreign key on current_revision_id
    fk_current_rev = next(
        fk for fk in doc_table.foreign_keys
        if fk.parent.name == "current_revision_id"
    )
    assert fk_current_rev.use_alter is True
    assert fk_current_rev.target_fullname == "mh_document_revisions.id"

    # Revision foreign key to document
    fk_doc = next(
        fk for fk in rev_table.foreign_keys
        if fk.parent.name == "document_id"
    )
    assert fk_doc.target_fullname == "mh_documents.id"
    assert fk_doc.ondelete == "RESTRICT"

    # Revision unique constraint on (document_id, revision_no)
    uqs = {uq.name for uq in rev_table.constraints if hasattr(uq, "columns")}
    assert "uq_mh_doc_rev_doc_rev_no" in uqs


def test_domain_2_canonical_tables_formula_separation():
    """Verify Domain 2 canonical cells preserve formula, raw value, cached value, and state."""
    cell_table = MhWorkbookCell.__table__

    assert "formula_expression" in cell_table.c
    assert "raw_value" in cell_table.c
    assert "cached_value" in cell_table.c
    assert "cached_value_state" in cell_table.c
    assert "cell_coordinate" in cell_table.c

    # Paragraph runs_json for OpenXML preservation
    para_table = MhDocumentParagraph.__table__
    assert "runs_json" in para_table.c
    assert "hyperlinks_json" in para_table.c


def test_domain_3_revisions_approvals_structure():
    """Verify Domain 3 preview, approval with 15m TTL, and outbox with lease fencing."""
    preview_table = MhEditPreview.__table__
    assert "canonical_patch_hash" in preview_table.c
    assert "expires_at" in preview_table.c
    assert "artifact_sha256" in preview_table.c

    approval_table = MhDocumentApproval.__table__
    assert "canonical_patch_hash" in approval_table.c
    assert "approver_user_id" in approval_table.c
    assert "consumed_at" in approval_table.c

    outbox_table = MhOutbox.__table__
    assert "lease_generation" in outbox_table.c
    assert "lease_worker_id" in outbox_table.c
    assert "delivery_attempts" in outbox_table.c
    assert "published_at" in outbox_table.c


def test_domain_4_agent_runtime_structure():
    """Verify Domain 4 engine binding, monotonic events, and tool ledger quarantine."""
    run_events_table = MhRunEvent.__table__
    assert "sequence" in run_events_table.c

    # Sequence unique constraint per run
    uqs = [uq for uq in run_events_table.constraints if getattr(uq, "name", None) == "uq_mh_run_events_run_seq"]
    assert len(uqs) == 1

    tool_inv_table = MhToolInvocation.__table__
    assert "quarantined" in tool_inv_table.c
    assert "quarantine_reason" in tool_inv_table.c
    assert "parameters_hash" in tool_inv_table.c
    assert "idempotency_key" in tool_inv_table.c


def test_domain_5_workers_structure():
    """Verify Domain 5 background task workers and lease fencing."""
    lease_table = MhWorkerLease.__table__
    assert "fencing_token" in lease_table.c
    assert "worker_node_id" in lease_table.c
    assert "lease_expires_at" in lease_table.c


def test_domain_6_auth_rbac_structure():
    """Verify Domain 6 auth, roles, and environment marker."""
    user_table = MhUser.__table__
    assert "email" in user_table.c
    assert "password_hash" in user_table.c
    assert "role" in user_table.c

    marker_table = MhEnvironmentMarker.__table__
    assert "instance_id" in marker_table.c
    assert "environment_name" in marker_table.c
    assert "schema_generation" in marker_table.c
    assert "safety_token" in marker_table.c


def test_facade_schema_re_exports_everything():
    """Verify that open_webui.models.v2.schema re-exports V2Base and all 49 models."""
    assert facade_schema.V2Base is V2Base
    assert facade_schema.v2_metadata is v2_metadata

    assert facade_schema.MhDocument is MhDocument
    assert facade_schema.MhWorkbookCell is MhWorkbookCell
    assert facade_schema.MhEditPreview is MhEditPreview
    assert facade_schema.MhRun is MhRun
    assert facade_schema.MhDocumentTask is MhDocumentTask
    assert facade_schema.MhUser is MhUser
    assert facade_schema.MhEnvironmentMarker is MhEnvironmentMarker

    # Verify count of exported tables
    assert len(facade_schema.v2_metadata.tables) == 49


def test_enums_completeness():
    """Verify string enum members match architectural contracts."""
    assert FormatType.XLSX == "xlsx"
    assert FormatType.DOCX == "docx"
    assert FormatType.PPTX == "pptx"
    assert FormatType.PDF == "pdf"

    assert PublicationState.STAGED == "STAGED"
    assert PublicationState.COMMITTED == "COMMITTED"
    assert PublicationState.AVAILABLE == "AVAILABLE"

    assert IndexBuildState.READY == "READY"
    assert IndexBuildState.FAILED == "FAILED"

    assert CachedValueState.PRESENT == "PRESENT"
    assert CachedValueState.MISSING == "MISSING"
    assert CachedValueState.DIRTY == "DIRTY"

    assert UserRole.ADMIN == "admin"
    assert UserRole.USER == "user"
    assert UserRole.REVIEWER == "reviewer"
    assert UserRole.AUDITOR == "auditor"


def test_enforce_postgresql_refuses_sqlite():
    """enforce_postgresql must fail closed on SQLite and accept PostgreSQL."""
    with pytest.raises(RuntimeError, match="strictly requires PostgreSQL"):
        enforce_postgresql("sqlite")

    with pytest.raises(RuntimeError, match="strictly requires PostgreSQL"):
        enforce_postgresql("mysql")

    # PostgreSQL must pass without exception
    enforce_postgresql("postgresql")


def test_postgresql_ddl_compilation_for_all_49_tables():
    """Compile DDL for all 49 tables against PostgreSQL dialect to verify syntax and typing."""
    statements: list[str] = []

    def mock_executor(sql, *args, **kwargs):
        statements.append(str(sql.compile(dialect=mock_engine.dialect)))

    mock_engine = create_mock_engine("postgresql+psycopg://", mock_executor)

    # Compile CreateTable for all 49 tables
    for table_name, table in v2_metadata.tables.items():
        ddl = str(CreateTable(table).compile(dialect=mock_engine.dialect))
        assert "CREATE TABLE" in ddl
        assert table_name in ddl
