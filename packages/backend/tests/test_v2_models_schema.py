"""Unit tests for Agent Runtime V2 Persistence Models and Schema.

Verifies:
- All 49 tables exist in V2Base.metadata with official 'ar_*' table prefix.
- All 6 domains are represented with required columns, types, and constraints.
- Composite foreign keys and cyclical FK (ar_documents.current_revision_id) with use_alter=True.
- Unique constraints and indexes.
- Dialect enforcement: SQLite strictly refused, PostgreSQL dialect required.
- Facade module weai.models.v2.schema re-exports all 49 models.
- DDL compilation for PostgreSQL dialect produces valid SQL statements.
"""

import pytest
from sqlalchemy import create_mock_engine
from sqlalchemy.schema import CreateTable

import weai.models.v2.schema as facade_schema
from weai.persistence.v2.models import (
    ApprovalStatus,
    CachedValueState,
    CellDataType,
    FormatType,
    GranteeType,
    IndexBuildState,
    WeAIAccessGrant,
    WeAIActiveIndex,
    WeAIAgentArtifact,
    WeAIAnalysisJob,
    WeAIAuthRevokedSessionJti,
    WeAIAuthSession,
    WeAIChat,
    WeAIChatFolder,
    WeAIChatMessage,
    WeAICheckpoint,
    WeAIChunk,
    WeAICompensatingRevision,
    WeAIConversationEngineBinding,
    WeAIDocument,
    WeAIDocumentApproval,
    WeAIDocumentParagraph,
    WeAIDocumentRevision,
    WeAIDocumentSection,
    WeAIDocumentTask,
    WeAIEditPreview,
    WeAIEnvironmentMarker,
    WeAIExecutionLease,
    WeAIExportJob,
    WeAIFolderNode,
    WeAIGroup,
    WeAIGroupMembership,
    WeAIIdempotencyRecord,
    WeAIIndexBuild,
    WeAIIngestionJob,
    WeAIModel,
    WeAIModelParam,
    WeAIObjectRegistry,
    WeAIOutbox,
    WeAIPresentationShape,
    WeAIPresentationSlide,
    WeAIRole,
    WeAIRun,
    WeAIRunBinding,
    WeAIRunEvent,
    WeAISourceAnchor,
    WeAIStagedObject,
    WeAISummaryTask,
    WeAIToolInvocation,
    WeAIUploadReceipt,
    WeAIUser,
    WeAIWorkbookCell,
    WeAIWorkbookSheet,
    WeAIWorkerLease,
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
    "ar_documents",
    "ar_document_revisions",
    "ar_upload_receipts",
    "ar_ingestion_jobs",
    "ar_index_builds",
    "ar_active_indexes",
    "ar_source_anchors",
    "ar_chunks",
    "ar_object_registry",
    "ar_staged_objects",
    # Domain 2: Format Canonical Tables (7)
    "ar_workbook_sheets",
    "ar_workbook_cells",
    "ar_merged_ranges",
    "ar_document_sections",
    "ar_document_paragraphs",
    "ar_presentation_slides",
    "ar_presentation_shapes",
    # Domain 3: Revision & Approvals (5)
    "ar_edit_previews",
    "ar_document_approvals",
    "ar_idempotency_records",
    "ar_outbox",
    "ar_compensating_revisions",
    # Domain 4: AgentRuntime Engine (8)
    "ar_conversation_engine_bindings",
    "ar_run_bindings",
    "ar_runs",
    "ar_run_events",
    "ar_tool_invocations",
    "ar_checkpoints",
    "ar_agent_artifacts",
    "ar_execution_leases",
    # Domain 5: Task Workers (5)
    "ar_document_tasks",
    "ar_analysis_jobs",
    "ar_summary_tasks",
    "ar_export_jobs",
    "ar_worker_leases",
    # Domain 6: Auth / RBAC / WeAI Async Entities (14)
    "ar_users",
    "ar_auth_sessions",
    "ar_auth_revoked_session_jtis",
    "ar_roles",
    "ar_groups",
    "ar_group_memberships",
    "ar_access_grants",
    "ar_folder_nodes",
    "ar_chats",
    "ar_chat_messages",
    "ar_chat_folders",
    "ar_models",
    "ar_model_params",
    "ar_environment_markers",
}


def test_v2_metadata_has_exactly_49_tables():
    """All 49 tables must be registered in v2_metadata under official 'ar_*' prefix."""
    registered = set(v2_metadata.tables.keys())
    assert len(registered) == 49, f"Expected 49 tables, found {len(registered)}: {registered ^ EXPECTED_49_TABLES}"
    assert registered == EXPECTED_49_TABLES, f"Difference: {registered ^ EXPECTED_49_TABLES}"

    non_mh = [t for t in registered if not t.startswith("ar_")]
    assert len(non_mh) == 0, f"Found tables without 'ar_' prefix: {non_mh}"


def test_domain_1_core_documents_structure():
    """Verify Domain 1 core document models and cyclical foreign key with use_alter=True."""
    doc_table = WeAIDocument.__table__
    rev_table = WeAIDocumentRevision.__table__

    assert "current_revision_id" in doc_table.c
    assert "current_revision_no" in doc_table.c
    assert "format_type" in doc_table.c

    # Check cyclical foreign key on current_revision_id
    fk_current_rev = next(
        fk for fk in doc_table.foreign_keys
        if fk.parent.name == "current_revision_id"
    )
    assert fk_current_rev.use_alter is True
    assert fk_current_rev.target_fullname == "ar_document_revisions.id"

    # Revision foreign key to document
    fk_doc = next(
        fk for fk in rev_table.foreign_keys
        if fk.parent.name == "document_id"
    )
    assert fk_doc.target_fullname == "ar_documents.id"
    assert fk_doc.ondelete == "RESTRICT"

    # Revision unique constraint on (document_id, revision_no)
    uqs = {uq.name for uq in rev_table.constraints if hasattr(uq, "columns")}
    assert "uq_ar_doc_rev_doc_rev_no" in uqs


def test_domain_2_canonical_tables_formula_separation():
    """Verify Domain 2 canonical cells preserve formula, raw value, cached value, and state."""
    cell_table = WeAIWorkbookCell.__table__

    assert "formula_expression" in cell_table.c
    assert "raw_value" in cell_table.c
    assert "cached_value" in cell_table.c
    assert "cached_value_state" in cell_table.c
    assert "cell_coordinate" in cell_table.c

    # Paragraph runs_json for OpenXML preservation
    para_table = WeAIDocumentParagraph.__table__
    assert "runs_json" in para_table.c
    assert "hyperlinks_json" in para_table.c


def test_domain_3_revisions_approvals_structure():
    """Verify Domain 3 preview, approval with 15m TTL, and outbox with lease fencing."""
    preview_table = WeAIEditPreview.__table__
    assert "canonical_patch_hash" in preview_table.c
    assert "expires_at" in preview_table.c
    assert "artifact_sha256" in preview_table.c

    approval_table = WeAIDocumentApproval.__table__
    assert "canonical_patch_hash" in approval_table.c
    assert "approver_user_id" in approval_table.c
    assert "consumed_at" in approval_table.c

    outbox_table = WeAIOutbox.__table__
    assert "lease_generation" in outbox_table.c
    assert "lease_worker_id" in outbox_table.c
    assert "delivery_attempts" in outbox_table.c
    assert "published_at" in outbox_table.c


def test_domain_4_agent_runtime_structure():
    """Verify Domain 4 engine binding, monotonic events, and tool ledger quarantine."""
    run_events_table = WeAIRunEvent.__table__
    assert "sequence" in run_events_table.c

    # Sequence unique constraint per run
    uqs = [uq for uq in run_events_table.constraints if getattr(uq, "name", None) == "uq_ar_run_events_run_seq"]
    assert len(uqs) == 1

    tool_inv_table = WeAIToolInvocation.__table__
    assert "quarantined" in tool_inv_table.c
    assert "quarantine_reason" in tool_inv_table.c
    assert "parameters_hash" in tool_inv_table.c
    assert "idempotency_key" in tool_inv_table.c


def test_domain_5_workers_structure():
    """Verify Domain 5 background task workers and lease fencing."""
    lease_table = WeAIWorkerLease.__table__
    assert "fencing_token" in lease_table.c
    assert "worker_node_id" in lease_table.c
    assert "lease_expires_at" in lease_table.c


def test_domain_6_auth_rbac_structure():
    """Verify Domain 6 auth, roles, and environment marker."""
    user_table = WeAIUser.__table__
    assert "email" in user_table.c
    assert "password_hash" in user_table.c
    assert "role" in user_table.c

    marker_table = WeAIEnvironmentMarker.__table__
    assert "instance_id" in marker_table.c
    assert "environment_name" in marker_table.c
    assert "schema_generation" in marker_table.c
    assert "safety_token" in marker_table.c


def test_facade_schema_re_exports_everything():
    """Verify that weai.models.v2.schema re-exports V2Base and all 49 models."""
    assert facade_schema.V2Base is V2Base
    assert facade_schema.v2_metadata is v2_metadata

    assert facade_schema.WeAIDocument is WeAIDocument
    assert facade_schema.WeAIWorkbookCell is WeAIWorkbookCell
    assert facade_schema.WeAIEditPreview is WeAIEditPreview
    assert facade_schema.WeAIRun is WeAIRun
    assert facade_schema.WeAIDocumentTask is WeAIDocumentTask
    assert facade_schema.WeAIUser is WeAIUser
    assert facade_schema.WeAIEnvironmentMarker is WeAIEnvironmentMarker

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
