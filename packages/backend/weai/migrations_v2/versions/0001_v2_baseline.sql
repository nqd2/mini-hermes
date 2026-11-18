-- Frozen PostgreSQL DDL for the Agent Runtime V2 greenfield baseline.
-- Changes to the ORM after this snapshot require a new Alembic revision.
CREATE TABLE ar_conversation_engine_bindings (
	conversation_id UUID NOT NULL,
	engine_type VARCHAR(96) NOT NULL,
	engine_version VARCHAR(96) NOT NULL,
	bound_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_conversation_engine_bindings PRIMARY KEY (conversation_id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_environment_markers (
	key VARCHAR(64) NOT NULL,
	instance_id UUID NOT NULL,
	environment_name VARCHAR(64) NOT NULL,
	schema_generation VARCHAR(32) NOT NULL,
	safety_token VARCHAR(128) NOT NULL,
	object_namespace VARCHAR(128) NOT NULL,
	index_namespace VARCHAR(128) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	verified_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_ar_environment_markers PRIMARY KEY (key),
	CONSTRAINT uq_ar_env_markers_instance UNIQUE (instance_id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_idempotency_records (
	id UUID NOT NULL,
	principal_id VARCHAR(64) NOT NULL,
	operation_kind VARCHAR(64) NOT NULL,
	idempotency_key VARCHAR(255) NOT NULL,
	request_fingerprint CHAR(64) NOT NULL,
	response_status_code INTEGER NOT NULL,
	cached_response_json JSONB NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_idempotency_records PRIMARY KEY (id),
	CONSTRAINT uq_ar_idempotency_key UNIQUE (principal_id, operation_kind, idempotency_key)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_models (
	id VARCHAR(128) NOT NULL,
	name VARCHAR(255) NOT NULL,
	provider VARCHAR(64) NOT NULL,
	is_active BOOLEAN NOT NULL,
	capabilities_json JSONB,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_models PRIMARY KEY (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_object_registry (
	checksum_sha256 CHAR(64) NOT NULL,
	canonical_object_key VARCHAR(1024) NOT NULL,
	byte_size BIGINT NOT NULL,
	storage_tier VARCHAR(32) NOT NULL,
	ref_count INTEGER NOT NULL,
	gc_status VARCHAR(32) NOT NULL,
	gc_generation BIGINT NOT NULL,
	last_verified_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_object_registry PRIMARY KEY (checksum_sha256),
	CONSTRAINT uq_ar_object_registry_key UNIQUE (canonical_object_key)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_outbox (
	id UUID NOT NULL,
	event_key VARCHAR(255) NOT NULL,
	event_type VARCHAR(96) NOT NULL,
	aggregate_type VARCHAR(64),
	aggregate_id VARCHAR(64),
	payload_json JSONB NOT NULL,
	delivery_attempts INTEGER NOT NULL,
	max_attempts INTEGER NOT NULL,
	lease_worker_id VARCHAR(128),
	lease_generation BIGINT NOT NULL,
	lease_expires_at TIMESTAMP WITH TIME ZONE,
	last_error TEXT,
	published_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_outbox PRIMARY KEY (id),
	CONSTRAINT uq_ar_outbox_event_key UNIQUE (event_key)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_roles (
	id UUID NOT NULL,
	name VARCHAR(64) NOT NULL,
	description TEXT,
	permissions_json JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_roles PRIMARY KEY (id),
	CONSTRAINT uq_ar_roles_name UNIQUE (name)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_upload_receipts (
	id UUID NOT NULL,
	principal_id VARCHAR(64) NOT NULL,
	weai_file_id VARCHAR(255) NOT NULL,
	expected_sha256 CHAR(64) NOT NULL,
	expected_byte_size BIGINT NOT NULL,
	filename VARCHAR(512) NOT NULL,
	content_type VARCHAR(128) NOT NULL,
	v2_flow_version VARCHAR(32) NOT NULL,
	status VARCHAR(32) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_upload_receipts PRIMARY KEY (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_users (
	id UUID NOT NULL,
	email VARCHAR(255) NOT NULL,
	name VARCHAR(255) NOT NULL,
	role VARCHAR(32) NOT NULL,
	profile_image_url TEXT,
	is_active BOOLEAN NOT NULL,
	session_stamp VARCHAR(64),
	settings_json JSONB,
	password_hash VARCHAR(255),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_users PRIMARY KEY (id),
	CONSTRAINT uq_ar_users_email UNIQUE (email)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_access_grants (
	id UUID NOT NULL,
	resource_type VARCHAR(32) NOT NULL,
	resource_id UUID NOT NULL,
	grantee_type VARCHAR(16) NOT NULL,
	grantee_id UUID NOT NULL,
	permission VARCHAR(32) NOT NULL,
	granted_by UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_access_grants PRIMARY KEY (id),
	CONSTRAINT uq_ar_access_grants_grant UNIQUE (resource_type, resource_id, grantee_type, grantee_id, permission),
	CONSTRAINT fk_ar_access_grants_granted_by FOREIGN KEY(granted_by) REFERENCES ar_users (id) ON DELETE RESTRICT
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_auth_revoked_session_jtis (
	jti_digest CHAR(64) NOT NULL,
	user_id UUID NOT NULL,
	revoked_at TIMESTAMP WITH TIME ZONE NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	reason VARCHAR(64),
	CONSTRAINT pk_ar_auth_revoked_session_jtis PRIMARY KEY (jti_digest),
	CONSTRAINT fk_ar_revoked_jtis_user_id FOREIGN KEY(user_id) REFERENCES ar_users (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_auth_sessions (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	session_token_hash CHAR(64) NOT NULL,
	client_ip VARCHAR(45),
	user_agent TEXT,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_auth_sessions PRIMARY KEY (id),
	CONSTRAINT uq_ar_auth_sessions_token_hash UNIQUE (session_token_hash),
	CONSTRAINT fk_ar_auth_sessions_user_id FOREIGN KEY(user_id) REFERENCES ar_users (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_chat_folders (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	name VARCHAR(255) NOT NULL,
	is_expanded BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_chat_folders PRIMARY KEY (id),
	CONSTRAINT fk_ar_chat_folders_user_id FOREIGN KEY(user_id) REFERENCES ar_users (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_folder_nodes (
	id UUID NOT NULL,
	parent_folder_id UUID,
	owner_id UUID NOT NULL,
	name VARCHAR(255) NOT NULL,
	is_personal_root BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_folder_nodes PRIMARY KEY (id),
	CONSTRAINT fk_ar_folder_nodes_parent_id FOREIGN KEY(parent_folder_id) REFERENCES ar_folder_nodes (id) ON DELETE CASCADE,
	CONSTRAINT fk_ar_folder_nodes_owner_id FOREIGN KEY(owner_id) REFERENCES ar_users (id) ON DELETE RESTRICT
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_groups (
	id UUID NOT NULL,
	name VARCHAR(128) NOT NULL,
	description TEXT,
	permissions_json JSONB NOT NULL,
	created_by UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_groups PRIMARY KEY (id),
	CONSTRAINT fk_ar_groups_created_by FOREIGN KEY(created_by) REFERENCES ar_users (id) ON DELETE RESTRICT
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_model_params (
	id UUID NOT NULL,
	model_id VARCHAR(128) NOT NULL,
	system_prompt TEXT,
	temperature FLOAT,
	top_p FLOAT,
	max_tokens INTEGER,
	context_window_characters INTEGER NOT NULL,
	hyperparams_json JSONB,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_model_params PRIMARY KEY (id),
	CONSTRAINT uq_ar_model_params_model UNIQUE (model_id),
	CONSTRAINT fk_ar_model_params_model_id FOREIGN KEY(model_id) REFERENCES ar_models (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_run_bindings (
	run_id UUID NOT NULL,
	conversation_id UUID NOT NULL,
	engine_type VARCHAR(96) NOT NULL,
	engine_version VARCHAR(96) NOT NULL,
	tool_snapshot_hash VARCHAR(64),
	skill_snapshot_hash VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_run_bindings PRIMARY KEY (run_id),
	CONSTRAINT uq_ar_run_bindings_run_conv UNIQUE (run_id, conversation_id),
	CONSTRAINT fk_ar_run_bindings_conv_id FOREIGN KEY(conversation_id) REFERENCES ar_conversation_engine_bindings (conversation_id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_runs (
	id UUID NOT NULL,
	conversation_id UUID NOT NULL,
	owner_id VARCHAR(64) NOT NULL,
	status VARCHAR(32) NOT NULL,
	current_turn INTEGER NOT NULL,
	next_event_sequence INTEGER NOT NULL,
	request_payload JSONB NOT NULL,
	error_json JSONB,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_ar_runs PRIMARY KEY (id),
	CONSTRAINT fk_ar_runs_conv_id FOREIGN KEY(conversation_id) REFERENCES ar_conversation_engine_bindings (conversation_id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_staged_objects (
	id UUID NOT NULL,
	checksum_sha256 CHAR(64),
	staged_object_key VARCHAR(1024) NOT NULL,
	document_id UUID,
	principal_id VARCHAR(64) NOT NULL,
	byte_size BIGINT NOT NULL,
	stage_status VARCHAR(32) NOT NULL,
	verified_at TIMESTAMP WITH TIME ZONE,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_staged_objects PRIMARY KEY (id),
	CONSTRAINT fk_ar_staged_obj_sha256 FOREIGN KEY(checksum_sha256) REFERENCES ar_object_registry (checksum_sha256) ON DELETE RESTRICT
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_agent_artifacts (
	id UUID NOT NULL,
	run_id UUID NOT NULL,
	artifact_name VARCHAR(255) NOT NULL,
	content_type VARCHAR(128) NOT NULL,
	byte_size BIGINT NOT NULL,
	checksum_sha256 CHAR(64) NOT NULL,
	storage_key VARCHAR(1024) NOT NULL,
	bounded_excerpt TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_agent_artifacts PRIMARY KEY (id),
	CONSTRAINT fk_ar_agent_artifacts_run_id FOREIGN KEY(run_id) REFERENCES ar_runs (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_chats (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	folder_id UUID,
	title TEXT NOT NULL,
	meta_json JSONB,
	is_archived BOOLEAN NOT NULL,
	pinned BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_chats PRIMARY KEY (id),
	CONSTRAINT fk_ar_chats_user_id FOREIGN KEY(user_id) REFERENCES ar_users (id) ON DELETE CASCADE,
	CONSTRAINT fk_ar_chats_folder_id FOREIGN KEY(folder_id) REFERENCES ar_chat_folders (id) ON DELETE SET NULL
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_checkpoints (
	id UUID NOT NULL,
	run_id UUID NOT NULL,
	version INTEGER NOT NULL,
	state_graph_json JSONB NOT NULL,
	memory_frame_json JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_checkpoints PRIMARY KEY (id),
	CONSTRAINT uq_ar_checkpoints_run_ver UNIQUE (run_id, version),
	CONSTRAINT fk_ar_checkpoints_run_id FOREIGN KEY(run_id) REFERENCES ar_runs (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_documents (
	id UUID NOT NULL,
	tenant_id VARCHAR(64) NOT NULL,
	name VARCHAR(255) NOT NULL,
	owner_id VARCHAR(64) NOT NULL,
	folder_id UUID,
	mime_type VARCHAR(128) NOT NULL,
	format_type VARCHAR(32) NOT NULL,
	current_revision_id UUID,
	current_revision_no INTEGER NOT NULL,
	is_archived BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_documents PRIMARY KEY (id),
	CONSTRAINT fk_ar_documents_folder_id FOREIGN KEY(folder_id) REFERENCES ar_folder_nodes (id) ON DELETE SET NULL
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_execution_leases (
	run_id UUID NOT NULL,
	worker_id VARCHAR(128) NOT NULL,
	fencing_token BIGINT NOT NULL,
	heartbeat_at TIMESTAMP WITH TIME ZONE NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_execution_leases PRIMARY KEY (run_id),
	CONSTRAINT fk_ar_exec_leases_run_id FOREIGN KEY(run_id) REFERENCES ar_runs (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_group_memberships (
	group_id UUID NOT NULL,
	user_id UUID NOT NULL,
	role VARCHAR(32) NOT NULL,
	joined_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_group_memberships PRIMARY KEY (group_id, user_id),
	CONSTRAINT fk_ar_group_memberships_group_id FOREIGN KEY(group_id) REFERENCES ar_groups (id) ON DELETE CASCADE,
	CONSTRAINT fk_ar_group_memberships_user_id FOREIGN KEY(user_id) REFERENCES ar_users (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_run_events (
	id UUID NOT NULL,
	run_id UUID NOT NULL,
	sequence INTEGER NOT NULL,
	event_type VARCHAR(64) NOT NULL,
	payload_json JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_run_events PRIMARY KEY (id),
	CONSTRAINT uq_ar_run_events_run_seq UNIQUE (run_id, sequence),
	CONSTRAINT fk_ar_run_events_run_id FOREIGN KEY(run_id) REFERENCES ar_runs (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_tool_invocations (
	id UUID NOT NULL,
	run_id UUID NOT NULL,
	call_id VARCHAR(128) NOT NULL,
	tool_name VARCHAR(128) NOT NULL,
	idempotency_key VARCHAR(255) NOT NULL,
	parameters_hash CHAR(64) NOT NULL,
	arguments_json JSONB NOT NULL,
	status VARCHAR(32) NOT NULL,
	quarantined BOOLEAN NOT NULL,
	quarantine_reason TEXT,
	result_excerpt TEXT,
	artifact_id UUID,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_ar_tool_invocations PRIMARY KEY (id),
	CONSTRAINT uq_ar_tool_inv_run_key UNIQUE (run_id, idempotency_key),
	CONSTRAINT uq_ar_tool_inv_run_call UNIQUE (run_id, call_id),
	CONSTRAINT fk_ar_tool_inv_run_id FOREIGN KEY(run_id) REFERENCES ar_runs (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_chat_messages (
	id UUID NOT NULL,
	chat_id UUID NOT NULL,
	parent_id UUID,
	turn_id VARCHAR(64),
	role VARCHAR(32) NOT NULL,
	content TEXT NOT NULL,
	model_id VARCHAR(128),
	tool_calls_json JSONB,
	citations_json JSONB,
	annotations_json JSONB,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_chat_messages PRIMARY KEY (id),
	CONSTRAINT fk_ar_chat_messages_chat_id FOREIGN KEY(chat_id) REFERENCES ar_chats (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_document_revisions (
	id UUID NOT NULL,
	document_id UUID NOT NULL,
	revision_no INTEGER NOT NULL,
	object_key VARCHAR(1024) NOT NULL,
	checksum_sha256 CHAR(64) NOT NULL,
	byte_size BIGINT NOT NULL,
	source_metadata JSONB NOT NULL,
	commit_status VARCHAR(32) NOT NULL,
	committed_by VARCHAR(64) NOT NULL,
	committed_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_document_revisions PRIMARY KEY (id),
	CONSTRAINT uq_ar_doc_rev_doc_rev_no UNIQUE (document_id, revision_no),
	CONSTRAINT uq_ar_doc_rev_doc_id UNIQUE (document_id, id),
	CONSTRAINT fk_ar_doc_rev_doc_id FOREIGN KEY(document_id) REFERENCES ar_documents (id) ON DELETE RESTRICT
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_compensating_revisions (
	id UUID NOT NULL,
	document_id UUID NOT NULL,
	faulty_revision_id UUID NOT NULL,
	compensating_revision_id UUID NOT NULL,
	faulty_revision_no INTEGER NOT NULL,
	compensating_revision_no INTEGER NOT NULL,
	rollback_reason TEXT NOT NULL,
	initiated_by VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_compensating_revisions PRIMARY KEY (id),
	CONSTRAINT fk_ar_comp_rev_doc_id FOREIGN KEY(document_id) REFERENCES ar_documents (id) ON DELETE CASCADE,
	CONSTRAINT fk_ar_comp_rev_faulty_id FOREIGN KEY(faulty_revision_id) REFERENCES ar_document_revisions (id) ON DELETE RESTRICT,
	CONSTRAINT fk_ar_comp_rev_comp_id FOREIGN KEY(compensating_revision_id) REFERENCES ar_document_revisions (id) ON DELETE RESTRICT
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_document_tasks (
	id UUID NOT NULL,
	document_id UUID NOT NULL,
	revision_id UUID,
	task_type VARCHAR(32) NOT NULL,
	status VARCHAR(32) NOT NULL,
	progress_percent INTEGER NOT NULL,
	created_by VARCHAR(64) NOT NULL,
	error_message TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_ar_document_tasks PRIMARY KEY (id),
	CONSTRAINT fk_ar_doc_tasks_doc_id FOREIGN KEY(document_id) REFERENCES ar_documents (id) ON DELETE CASCADE,
	CONSTRAINT fk_ar_doc_tasks_rev_id FOREIGN KEY(revision_id) REFERENCES ar_document_revisions (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_edit_previews (
	id UUID NOT NULL,
	document_id UUID NOT NULL,
	base_revision_id UUID NOT NULL,
	base_revision_no INTEGER NOT NULL,
	base_sha256 CHAR(64) NOT NULL,
	artifact_sha256 CHAR(64) NOT NULL,
	staged_object_id UUID,
	canonical_patch_hash CHAR(64) NOT NULL,
	operations_json JSONB NOT NULL,
	diff_summary TEXT,
	status VARCHAR(32) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_edit_previews PRIMARY KEY (id),
	CONSTRAINT fk_ar_edit_previews_doc_id FOREIGN KEY(document_id) REFERENCES ar_documents (id) ON DELETE CASCADE,
	CONSTRAINT fk_ar_edit_previews_base_rev_id FOREIGN KEY(base_revision_id) REFERENCES ar_document_revisions (id) ON DELETE RESTRICT
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_export_jobs (
	id UUID NOT NULL,
	document_id UUID NOT NULL,
	revision_id UUID NOT NULL,
	target_format VARCHAR(32) NOT NULL,
	status VARCHAR(32) NOT NULL,
	exported_object_key VARCHAR(1024),
	checksum_sha256 CHAR(64),
	byte_size BIGINT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_ar_export_jobs PRIMARY KEY (id),
	CONSTRAINT fk_ar_export_jobs_doc_id FOREIGN KEY(document_id) REFERENCES ar_documents (id) ON DELETE CASCADE,
	CONSTRAINT fk_ar_export_jobs_rev_id FOREIGN KEY(revision_id) REFERENCES ar_document_revisions (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_index_builds (
	id UUID NOT NULL,
	document_id UUID NOT NULL,
	revision_id UUID NOT NULL,
	source_checksum_sha256 CHAR(64) NOT NULL,
	parser_version VARCHAR(64) NOT NULL,
	chunker_version VARCHAR(64) NOT NULL,
	schema_version VARCHAR(64) NOT NULL,
	manifest_hash CHAR(64) NOT NULL,
	build_state VARCHAR(32) NOT NULL,
	fencing_token BIGINT NOT NULL,
	lease_worker_id VARCHAR(128),
	lease_expires_at TIMESTAMP WITH TIME ZONE,
	coverage_proof JSONB,
	ready_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_index_builds PRIMARY KEY (id),
	CONSTRAINT uq_ar_index_builds_doc_rev_id UNIQUE (document_id, revision_id, id),
	CONSTRAINT fk_ar_index_builds_doc_id FOREIGN KEY(document_id) REFERENCES ar_documents (id) ON DELETE CASCADE,
	CONSTRAINT fk_ar_index_builds_rev_id FOREIGN KEY(revision_id) REFERENCES ar_document_revisions (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ingestion_jobs (
	id UUID NOT NULL,
	document_id UUID NOT NULL,
	revision_id UUID,
	principal_id VARCHAR(64) NOT NULL,
	idempotency_scope VARCHAR(64) NOT NULL,
	idempotency_key VARCHAR(255) NOT NULL,
	request_fingerprint CHAR(64) NOT NULL,
	source_state VARCHAR(32) NOT NULL,
	index_state VARCHAR(32) NOT NULL,
	fencing_token BIGINT NOT NULL,
	lease_worker_id VARCHAR(128),
	lease_expires_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_ingestion_jobs PRIMARY KEY (id),
	CONSTRAINT uq_ar_ingestion_jobs_idempotency UNIQUE (idempotency_scope, idempotency_key),
	CONSTRAINT fk_ar_ingestion_jobs_doc_id FOREIGN KEY(document_id) REFERENCES ar_documents (id) ON DELETE CASCADE,
	CONSTRAINT fk_ar_ingestion_jobs_rev_id FOREIGN KEY(revision_id) REFERENCES ar_document_revisions (id) ON DELETE SET NULL
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_active_indexes (
	revision_id UUID NOT NULL,
	document_id UUID NOT NULL,
	active_build_id UUID NOT NULL,
	generation BIGINT NOT NULL,
	activated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_active_indexes PRIMARY KEY (revision_id),
	CONSTRAINT uq_ar_active_indexes_rev_build UNIQUE (revision_id, active_build_id),
	CONSTRAINT fk_ar_active_indexes_rev_id FOREIGN KEY(revision_id) REFERENCES ar_document_revisions (id) ON DELETE CASCADE,
	CONSTRAINT fk_ar_active_indexes_doc_id FOREIGN KEY(document_id) REFERENCES ar_documents (id) ON DELETE CASCADE,
	CONSTRAINT fk_ar_active_indexes_build_id FOREIGN KEY(active_build_id) REFERENCES ar_index_builds (id) ON DELETE RESTRICT
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_analysis_jobs (
	id UUID NOT NULL,
	task_id UUID NOT NULL,
	document_id UUID NOT NULL,
	goal TEXT NOT NULL,
	specification_json JSONB NOT NULL,
	status VARCHAR(32) NOT NULL,
	evidence_proof_json JSONB,
	verified_outcome_json JSONB,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_ar_analysis_jobs PRIMARY KEY (id),
	CONSTRAINT uq_ar_analysis_jobs_task UNIQUE (task_id),
	CONSTRAINT fk_ar_analysis_jobs_task_id FOREIGN KEY(task_id) REFERENCES ar_document_tasks (id) ON DELETE CASCADE,
	CONSTRAINT fk_ar_analysis_jobs_doc_id FOREIGN KEY(document_id) REFERENCES ar_documents (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_document_approvals (
	id UUID NOT NULL,
	preview_id UUID NOT NULL,
	document_id UUID NOT NULL,
	base_revision_no INTEGER NOT NULL,
	canonical_patch_hash CHAR(64) NOT NULL,
	approver_user_id VARCHAR(64) NOT NULL,
	status VARCHAR(32) NOT NULL,
	consumed_at TIMESTAMP WITH TIME ZONE,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_document_approvals PRIMARY KEY (id),
	CONSTRAINT uq_ar_doc_approvals_preview UNIQUE (preview_id),
	CONSTRAINT fk_ar_doc_approvals_prev_id FOREIGN KEY(preview_id) REFERENCES ar_edit_previews (id) ON DELETE RESTRICT,
	CONSTRAINT fk_ar_doc_approvals_doc_id FOREIGN KEY(document_id) REFERENCES ar_documents (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_document_sections (
	id UUID NOT NULL,
	index_build_id UUID NOT NULL,
	section_order INTEGER NOT NULL,
	header_text TEXT,
	footer_text TEXT,
	properties_json JSONB,
	CONSTRAINT pk_ar_document_sections PRIMARY KEY (id),
	CONSTRAINT uq_ar_doc_sections_build_order UNIQUE (index_build_id, section_order),
	CONSTRAINT fk_ar_doc_sections_build_id FOREIGN KEY(index_build_id) REFERENCES ar_index_builds (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_presentation_slides (
	id UUID NOT NULL,
	index_build_id UUID NOT NULL,
	slide_order INTEGER NOT NULL,
	slide_number INTEGER NOT NULL,
	layout_name VARCHAR(128),
	title TEXT,
	speaker_notes TEXT,
	CONSTRAINT pk_ar_presentation_slides PRIMARY KEY (id),
	CONSTRAINT uq_ar_pres_slides_build_order UNIQUE (index_build_id, slide_order),
	CONSTRAINT fk_ar_pres_slides_build_id FOREIGN KEY(index_build_id) REFERENCES ar_index_builds (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_source_anchors (
	id UUID NOT NULL,
	index_build_id UUID NOT NULL,
	anchor_kind VARCHAR(32) NOT NULL,
	sheet_id UUID,
	sheet_name VARCHAR(255),
	page_no INTEGER,
	row_idx INTEGER,
	col_idx INTEGER,
	start_offset BIGINT,
	end_offset BIGINT,
	locator_json JSONB,
	CONSTRAINT pk_ar_source_anchors PRIMARY KEY (id),
	CONSTRAINT fk_ar_anchors_build_id FOREIGN KEY(index_build_id) REFERENCES ar_index_builds (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_summary_tasks (
	id UUID NOT NULL,
	task_id UUID NOT NULL,
	document_id UUID NOT NULL,
	scope_json JSONB NOT NULL,
	status VARCHAR(32) NOT NULL,
	structured_summary TEXT,
	citation_anchors_json JSONB,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_ar_summary_tasks PRIMARY KEY (id),
	CONSTRAINT uq_ar_summary_tasks_task UNIQUE (task_id),
	CONSTRAINT fk_ar_summary_tasks_task_id FOREIGN KEY(task_id) REFERENCES ar_document_tasks (id) ON DELETE CASCADE,
	CONSTRAINT fk_ar_summary_tasks_doc_id FOREIGN KEY(document_id) REFERENCES ar_documents (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_workbook_sheets (
	id UUID NOT NULL,
	index_build_id UUID NOT NULL,
	sheet_name VARCHAR(255) NOT NULL,
	sheet_order INTEGER NOT NULL,
	visibility VARCHAR(16) NOT NULL,
	max_row INTEGER,
	max_col INTEGER,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_workbook_sheets PRIMARY KEY (id),
	CONSTRAINT uq_ar_wb_sheets_build_order UNIQUE (index_build_id, sheet_order),
	CONSTRAINT uq_ar_wb_sheets_build_name UNIQUE (index_build_id, sheet_name),
	CONSTRAINT fk_ar_wb_sheets_build_id FOREIGN KEY(index_build_id) REFERENCES ar_index_builds (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_worker_leases (
	task_id UUID NOT NULL,
	worker_node_id VARCHAR(128) NOT NULL,
	fencing_token BIGINT NOT NULL,
	heartbeat_at TIMESTAMP WITH TIME ZONE NOT NULL,
	lease_expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_ar_worker_leases PRIMARY KEY (task_id),
	CONSTRAINT fk_ar_worker_leases_task_id FOREIGN KEY(task_id) REFERENCES ar_document_tasks (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_chunks (
	id UUID NOT NULL,
	index_build_id UUID NOT NULL,
	anchor_start_id UUID,
	anchor_end_id UUID,
	stable_ordinal INTEGER NOT NULL,
	content_sha256 CHAR(64) NOT NULL,
	token_count INTEGER NOT NULL,
	raw_text TEXT NOT NULL,
	metadata_json JSONB,
	CONSTRAINT pk_ar_chunks PRIMARY KEY (id),
	CONSTRAINT uq_ar_chunks_build_ordinal UNIQUE (index_build_id, stable_ordinal),
	CONSTRAINT fk_ar_chunks_build_id FOREIGN KEY(index_build_id) REFERENCES ar_index_builds (id) ON DELETE CASCADE,
	CONSTRAINT fk_ar_chunks_start_anchor_id FOREIGN KEY(anchor_start_id) REFERENCES ar_source_anchors (id) ON DELETE CASCADE,
	CONSTRAINT fk_ar_chunks_end_anchor_id FOREIGN KEY(anchor_end_id) REFERENCES ar_source_anchors (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_document_paragraphs (
	id UUID NOT NULL,
	section_id UUID NOT NULL,
	paragraph_order INTEGER NOT NULL,
	style_id VARCHAR(128),
	raw_text TEXT NOT NULL,
	runs_json JSONB,
	hyperlinks_json JSONB,
	CONSTRAINT pk_ar_document_paragraphs PRIMARY KEY (id),
	CONSTRAINT uq_ar_doc_paras_sec_order UNIQUE (section_id, paragraph_order),
	CONSTRAINT fk_ar_doc_paras_sec_id FOREIGN KEY(section_id) REFERENCES ar_document_sections (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_presentation_shapes (
	id UUID NOT NULL,
	slide_id UUID NOT NULL,
	parent_shape_id UUID,
	shape_name VARCHAR(255),
	shape_type VARCHAR(64) NOT NULL,
	geometry_json JSONB,
	text_frame TEXT,
	CONSTRAINT pk_ar_presentation_shapes PRIMARY KEY (id),
	CONSTRAINT fk_ar_pres_shapes_slide_id FOREIGN KEY(slide_id) REFERENCES ar_presentation_slides (id) ON DELETE CASCADE,
	CONSTRAINT fk_ar_pres_shapes_parent_id FOREIGN KEY(parent_shape_id) REFERENCES ar_presentation_shapes (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_workbook_cells (
	id UUID NOT NULL,
	index_build_id UUID NOT NULL,
	sheet_id UUID NOT NULL,
	row_idx INTEGER NOT NULL,
	col_idx INTEGER NOT NULL,
	cell_coordinate VARCHAR(16) NOT NULL,
	data_type VARCHAR(16) NOT NULL,
	raw_value TEXT,
	formula_expression TEXT,
	cached_value TEXT,
	cached_value_state VARCHAR(16) NOT NULL,
	number_format VARCHAR(128),
	CONSTRAINT pk_ar_workbook_cells PRIMARY KEY (id),
	CONSTRAINT uq_ar_wb_cells_sheet_row_col UNIQUE (sheet_id, row_idx, col_idx),
	CONSTRAINT fk_ar_wb_cells_build_id FOREIGN KEY(index_build_id) REFERENCES ar_index_builds (id) ON DELETE CASCADE,
	CONSTRAINT fk_ar_wb_cells_sheet_id FOREIGN KEY(sheet_id) REFERENCES ar_workbook_sheets (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_merged_ranges (
	id UUID NOT NULL,
	sheet_id UUID NOT NULL,
	anchor_cell_id UUID NOT NULL,
	range_coordinate VARCHAR(32) NOT NULL,
	top_row INTEGER NOT NULL,
	left_col INTEGER NOT NULL,
	bottom_row INTEGER NOT NULL,
	right_col INTEGER NOT NULL,
	CONSTRAINT pk_ar_merged_ranges PRIMARY KEY (id),
	CONSTRAINT fk_ar_merged_ranges_sheet_id FOREIGN KEY(sheet_id) REFERENCES ar_workbook_sheets (id) ON DELETE CASCADE,
	CONSTRAINT fk_ar_merged_ranges_anchor_id FOREIGN KEY(anchor_cell_id) REFERENCES ar_workbook_cells (id) ON DELETE RESTRICT
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_agent_file_content (
	file_id VARCHAR(255) NOT NULL,
	owner_id VARCHAR(255) NOT NULL,
	object_key VARCHAR(1024) NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	size_bytes BIGINT NOT NULL,
	char_count BIGINT NOT NULL,
	line_count BIGINT NOT NULL,
	line_index TEXT NOT NULL,
	char_index TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (file_id),
	UNIQUE (object_key)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_agent_session (
	id VARCHAR(36) NOT NULL,
	owner_id VARCHAR(255) NOT NULL,
	chat_id VARCHAR(255),
	parent_session_id VARCHAR(36),
	engine_type VARCHAR(96) NOT NULL,
	engine_version VARCHAR(96) NOT NULL,
	status VARCHAR(24) NOT NULL,
	next_turn_sequence INTEGER NOT NULL,
	task_state TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_agent_session_owner_chat UNIQUE (owner_id, chat_id),
	FOREIGN KEY(parent_session_id) REFERENCES ar_agent_session (id) ON DELETE SET NULL
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_drive_credential (
	user_id VARCHAR NOT NULL,
	encrypted_refresh_token BYTEA NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (user_id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_folder (
	id VARCHAR(36) NOT NULL,
	owner_id VARCHAR NOT NULL,
	parent_id VARCHAR(36),
	name VARCHAR(255) NOT NULL,
	is_personal_root BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(parent_id) REFERENCES ar_folder (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_access_grant (
	id TEXT NOT NULL,
	resource_type TEXT NOT NULL,
	resource_id TEXT NOT NULL,
	principal_type TEXT NOT NULL,
	principal_id TEXT NOT NULL,
	permission TEXT NOT NULL,
	created_at BIGINT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_access_grant_grant UNIQUE (resource_type, resource_id, principal_type, principal_id, permission)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_api_key (
	id TEXT NOT NULL,
	user_id TEXT NOT NULL,
	key TEXT NOT NULL,
	data JSON,
	expires_at BIGINT,
	last_used_at BIGINT,
	created_at BIGINT NOT NULL,
	updated_at BIGINT NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (id),
	UNIQUE (key)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_auth (
	id VARCHAR NOT NULL,
	email VARCHAR,
	password TEXT,
	active BOOLEAN,
	mfa JSON,
	session_stamp TEXT,
	PRIMARY KEY (id),
	UNIQUE (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_chat (
	id VARCHAR NOT NULL,
	user_id VARCHAR,
	title TEXT,
	chat JSON,
	created_at BIGINT,
	updated_at BIGINT,
	archived BOOLEAN,
	pinned BOOLEAN,
	meta JSON DEFAULT '{}',
	variables JSON,
	folder_id TEXT,
	tasks JSON,
	summary TEXT,
	current_message_id TEXT,
	last_read_at BIGINT,
	timer_at BIGINT,
	PRIMARY KEY (id),
	UNIQUE (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_config (
	key TEXT NOT NULL,
	value JSON NOT NULL,
	updated_at BIGINT,
	PRIMARY KEY (key)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_feedback (
	id TEXT NOT NULL,
	user_id TEXT,
	version BIGINT,
	type TEXT,
	data JSON,
	meta JSON,
	snapshot JSON,
	created_at BIGINT,
	updated_at BIGINT,
	PRIMARY KEY (id),
	UNIQUE (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_file (
	id VARCHAR NOT NULL,
	user_id VARCHAR,
	hash TEXT,
	filename TEXT,
	path TEXT,
	data JSON,
	meta JSON,
	created_at BIGINT,
	updated_at BIGINT,
	PRIMARY KEY (id),
	UNIQUE (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_folder (
	id TEXT NOT NULL,
	parent_id TEXT,
	user_id TEXT,
	name TEXT,
	items JSON,
	meta JSON,
	data JSON,
	is_expanded BOOLEAN,
	created_at BIGINT,
	updated_at BIGINT,
	PRIMARY KEY (id),
	UNIQUE (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_group (
	parent_group_id TEXT,
	id TEXT NOT NULL,
	user_id TEXT,
	name TEXT,
	description TEXT,
	data JSON,
	meta JSON,
	permissions JSON,
	created_at BIGINT,
	updated_at BIGINT,
	PRIMARY KEY (id),
	CONSTRAINT fk_group_parent FOREIGN KEY(parent_group_id) REFERENCES ar_ow_group (id) ON DELETE SET NULL,
	UNIQUE (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_knowledge (
	id TEXT NOT NULL,
	user_id TEXT,
	name TEXT,
	description TEXT,
	meta JSON,
	created_at BIGINT,
	updated_at BIGINT,
	PRIMARY KEY (id),
	UNIQUE (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_memory (
	id VARCHAR NOT NULL,
	user_id VARCHAR,
	type VARCHAR DEFAULT 'context',
	path TEXT,
	content TEXT,
	meta JSON,
	updated_at BIGINT,
	created_at BIGINT,
	PRIMARY KEY (id),
	UNIQUE (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_message (
	id TEXT NOT NULL,
	user_id TEXT,
	app_id TEXT,
	channel_id TEXT,
	reply_to_id TEXT,
	parent_id TEXT,
	is_pinned BOOLEAN NOT NULL,
	pinned_at BIGINT,
	pinned_by TEXT,
	content TEXT,
	data JSON,
	meta JSON,
	created_at BIGINT,
	updated_at BIGINT,
	PRIMARY KEY (id),
	UNIQUE (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_message_reaction (
	id TEXT NOT NULL,
	user_id TEXT,
	message_id TEXT,
	name TEXT,
	created_at BIGINT,
	PRIMARY KEY (id),
	UNIQUE (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_model (
	id TEXT NOT NULL,
	user_id TEXT,
	base_model_id TEXT,
	name TEXT,
	params TEXT,
	meta TEXT,
	version_id TEXT,
	is_active BOOLEAN,
	updated_at BIGINT,
	created_at BIGINT,
	PRIMARY KEY (id),
	UNIQUE (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_model_history (
	id TEXT NOT NULL,
	model_id TEXT NOT NULL,
	parent_id TEXT,
	snapshot JSON NOT NULL,
	user_id TEXT NOT NULL,
	commit_message TEXT,
	created_at BIGINT NOT NULL,
	PRIMARY KEY (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_notification (
	id TEXT NOT NULL,
	user_id TEXT NOT NULL,
	event_type TEXT NOT NULL,
	event_key TEXT NOT NULL,
	actor_type TEXT NOT NULL,
	actor_id TEXT,
	resource_type TEXT,
	resource_id TEXT,
	object_type TEXT,
	object_id TEXT,
	data JSON NOT NULL,
	seen_at BIGINT,
	read_at BIGINT,
	created_at BIGINT NOT NULL,
	updated_at BIGINT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_notification_occurrence UNIQUE (user_id, event_key),
	CONSTRAINT ck_notification_resource CHECK ((resource_type IS NULL) = (resource_id IS NULL)),
	CONSTRAINT ck_notification_object CHECK ((object_type IS NULL) = (object_id IS NULL)),
	CONSTRAINT ck_notification_actor CHECK (actor_type IN ('user', 'system'))
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_oauth_session (
	id TEXT NOT NULL,
	user_id TEXT NOT NULL,
	provider TEXT NOT NULL,
	token TEXT NOT NULL,
	expires_at BIGINT NOT NULL,
	created_at BIGINT NOT NULL,
	updated_at BIGINT NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_prompt (
	id TEXT NOT NULL,
	command VARCHAR,
	user_id VARCHAR,
	name TEXT,
	content TEXT,
	data JSON,
	meta JSON,
	tags JSON,
	is_active BOOLEAN,
	version_id TEXT,
	created_at BIGINT,
	updated_at BIGINT,
	PRIMARY KEY (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_prompt_history (
	id TEXT NOT NULL,
	prompt_id TEXT NOT NULL,
	parent_id TEXT,
	snapshot JSON NOT NULL,
	user_id TEXT NOT NULL,
	commit_message TEXT,
	created_at BIGINT NOT NULL,
	PRIMARY KEY (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_skill (
	id VARCHAR NOT NULL,
	user_id VARCHAR,
	name TEXT,
	description TEXT,
	content TEXT,
	data JSON,
	version_id TEXT,
	meta JSON,
	is_active BOOLEAN,
	updated_at BIGINT,
	created_at BIGINT,
	PRIMARY KEY (id),
	UNIQUE (id),
	UNIQUE (name)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_skill_history (
	id TEXT NOT NULL,
	skill_id TEXT NOT NULL,
	parent_id TEXT,
	snapshot JSON NOT NULL,
	user_id TEXT NOT NULL,
	commit_message TEXT,
	created_at BIGINT NOT NULL,
	PRIMARY KEY (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_tag (
	id VARCHAR NOT NULL,
	name VARCHAR,
	user_id VARCHAR NOT NULL,
	meta JSON,
	CONSTRAINT pk_id_user_id PRIMARY KEY (id, user_id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_tool (
	id VARCHAR NOT NULL,
	version_id TEXT,
	user_id VARCHAR,
	name TEXT,
	content TEXT,
	specs TEXT,
	meta TEXT,
	valves TEXT,
	is_active BOOLEAN DEFAULT true NOT NULL,
	updated_at BIGINT NOT NULL,
	created_at BIGINT,
	PRIMARY KEY (id),
	UNIQUE (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_tool_history (
	id TEXT NOT NULL,
	tool_id TEXT NOT NULL,
	parent_id TEXT,
	snapshot JSON NOT NULL,
	user_id TEXT NOT NULL,
	commit_message TEXT,
	created_at BIGINT NOT NULL,
	PRIMARY KEY (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_user (
	id VARCHAR NOT NULL,
	email VARCHAR,
	username VARCHAR(50),
	role VARCHAR,
	name VARCHAR NOT NULL,
	profile_image_url TEXT,
	profile_banner_image_url TEXT,
	bio TEXT,
	gender TEXT,
	date_of_birth DATE,
	timezone VARCHAR,
	presence_state VARCHAR,
	status_emoji VARCHAR,
	status_message TEXT,
	status_expires_at BIGINT,
	info JSON,
	variables JSON,
	settings JSON,
	oauth JSON,
	scim JSON,
	last_active_at BIGINT,
	updated_at BIGINT,
	created_at BIGINT,
	PRIMARY KEY (id),
	UNIQUE (id),
	UNIQUE (email)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_agent_turn (
	id VARCHAR(36) NOT NULL,
	session_id VARCHAR(36) NOT NULL,
	sequence INTEGER NOT NULL,
	status VARCHAR(24) NOT NULL,
	request_payload TEXT NOT NULL,
	idempotency_key VARCHAR(255),
	engine_type VARCHAR(96) NOT NULL,
	engine_version VARCHAR(96) NOT NULL,
	engine_run_id VARCHAR(255) NOT NULL,
	engine_thread_id VARCHAR(255) NOT NULL,
	tool_contract_version VARCHAR(255) NOT NULL,
	skill_snapshot_version VARCHAR(255) NOT NULL,
	model_id VARCHAR(255),
	chat_message_id VARCHAR(255),
	user_message_id VARCHAR(255),
	source_message_id VARCHAR(255),
	worker_id VARCHAR(255),
	lease_until TIMESTAMP WITH TIME ZONE,
	attempts INTEGER NOT NULL,
	cancel_requested BOOLEAN NOT NULL,
	next_event_sequence INTEGER NOT NULL,
	checkpoint_version INTEGER NOT NULL,
	error TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE,
	completed_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_agent_turn_session_sequence UNIQUE (session_id, sequence),
	CONSTRAINT uq_agent_turn_session_source_message UNIQUE (session_id, source_message_id),
	CONSTRAINT uq_agent_turn_session_idempotency UNIQUE (session_id, idempotency_key),
	CONSTRAINT ck_agent_turn_status CHECK (status IN ('queued', 'running', 'waiting_approval', 'waiting_user_input', 'paused', 'completed', 'failed', 'interrupted', 'cancelled')),
	FOREIGN KEY(session_id) REFERENCES ar_agent_session (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_document (
	id VARCHAR(36) NOT NULL,
	owner_id VARCHAR NOT NULL,
	filename VARCHAR(512) NOT NULL,
	mime_type VARCHAR(255) NOT NULL,
	folder_id VARCHAR(36),
	description TEXT,
	description_status VARCHAR(24) NOT NULL,
	description_error TEXT,
	description_attempts INTEGER NOT NULL,
	description_next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL,
	source_file_id VARCHAR(255),
	current_revision INTEGER NOT NULL,
	drive_file_id VARCHAR(255),
	drive_mime_type VARCHAR(255),
	drive_version VARCHAR(255),
	office_companion_id VARCHAR(255),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(folder_id) REFERENCES ar_folder (id),
	UNIQUE (source_file_id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_chat_file (
	id TEXT NOT NULL,
	user_id TEXT NOT NULL,
	chat_id TEXT NOT NULL,
	message_id TEXT,
	file_id TEXT NOT NULL,
	created_at BIGINT NOT NULL,
	updated_at BIGINT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_chat_file_chat_file UNIQUE (chat_id, file_id),
	UNIQUE (id),
	FOREIGN KEY(chat_id) REFERENCES ar_ow_chat (id) ON DELETE CASCADE,
	FOREIGN KEY(file_id) REFERENCES ar_ow_file (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_chat_message (
	id TEXT NOT NULL,
	chat_id TEXT NOT NULL,
	user_id TEXT,
	role TEXT NOT NULL,
	parent_id TEXT,
	content JSON,
	output JSON,
	model_id TEXT,
	files JSON,
	sources JSON,
	embeds JSON,
	meta JSON,
	done BOOLEAN,
	status_history JSON,
	error JSON,
	usage JSON,
	context_summary TEXT,
	created_at BIGINT,
	updated_at BIGINT,
	PRIMARY KEY (id),
	FOREIGN KEY(chat_id) REFERENCES ar_ow_chat (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_group_member (
	id TEXT NOT NULL,
	group_id TEXT NOT NULL,
	user_id TEXT NOT NULL,
	created_at BIGINT,
	updated_at BIGINT,
	PRIMARY KEY (id),
	UNIQUE (id),
	FOREIGN KEY(group_id) REFERENCES ar_ow_group (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_knowledge_directory (
	id TEXT NOT NULL,
	knowledge_id TEXT NOT NULL,
	parent_id TEXT,
	name TEXT NOT NULL,
	user_id TEXT NOT NULL,
	created_at BIGINT NOT NULL,
	updated_at BIGINT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_knowledge_directory_knowledge_parent_name UNIQUE (knowledge_id, parent_id, name),
	UNIQUE (id),
	FOREIGN KEY(knowledge_id) REFERENCES ar_ow_knowledge (id) ON DELETE CASCADE,
	FOREIGN KEY(parent_id) REFERENCES ar_ow_knowledge_directory (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_agent_artifact (
	id VARCHAR(36) NOT NULL,
	owner_id VARCHAR(255) NOT NULL,
	session_id VARCHAR(36),
	turn_id VARCHAR(36),
	object_key VARCHAR(1024) NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	size_bytes BIGINT NOT NULL,
	mime_type VARCHAR(255) NOT NULL,
	filename VARCHAR(512),
	metadata_json TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_agent_artifact_object_key UNIQUE (object_key),
	FOREIGN KEY(session_id) REFERENCES ar_agent_session (id) ON DELETE SET NULL,
	FOREIGN KEY(turn_id) REFERENCES ar_agent_turn (id) ON DELETE SET NULL
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_agent_checkpoint (
	id VARCHAR(36) NOT NULL,
	session_id VARCHAR(36) NOT NULL,
	turn_id VARCHAR(36) NOT NULL,
	version INTEGER NOT NULL,
	kind VARCHAR(32) NOT NULL,
	data TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_agent_checkpoint_turn_version UNIQUE (turn_id, version),
	FOREIGN KEY(session_id) REFERENCES ar_agent_session (id) ON DELETE CASCADE,
	FOREIGN KEY(turn_id) REFERENCES ar_agent_turn (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_agent_event (
	id VARCHAR(36) NOT NULL,
	session_id VARCHAR(36) NOT NULL,
	turn_id VARCHAR(36) NOT NULL,
	sequence INTEGER NOT NULL,
	event_type VARCHAR(96) NOT NULL,
	payload TEXT NOT NULL,
	published_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_agent_event_turn_sequence UNIQUE (turn_id, sequence),
	FOREIGN KEY(session_id) REFERENCES ar_agent_session (id) ON DELETE CASCADE,
	FOREIGN KEY(turn_id) REFERENCES ar_agent_turn (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_agent_tool_invocation (
	id VARCHAR(36) NOT NULL,
	session_id VARCHAR(36) NOT NULL,
	turn_id VARCHAR(36) NOT NULL,
	call_id VARCHAR(255) NOT NULL,
	tool_name VARCHAR(255) NOT NULL,
	arguments TEXT NOT NULL,
	status VARCHAR(24) NOT NULL,
	retry_safe BOOLEAN NOT NULL,
	idempotency_key VARCHAR(255),
	attempt_count INTEGER NOT NULL,
	result_data TEXT,
	result_artifact_id VARCHAR(36),
	error TEXT,
	approval_status VARCHAR(24),
	approved_by VARCHAR(255),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE,
	completed_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_agent_tool_turn_call UNIQUE (turn_id, call_id),
	CONSTRAINT ck_agent_tool_status CHECK (status IN ('pending', 'running', 'waiting_approval', 'succeeded', 'failed', 'outcome_unknown')),
	FOREIGN KEY(session_id) REFERENCES ar_agent_session (id) ON DELETE CASCADE,
	FOREIGN KEY(turn_id) REFERENCES ar_agent_turn (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_chat_resource (
	id VARCHAR(36) NOT NULL,
	chat_id VARCHAR(255) NOT NULL,
	document_id VARCHAR(36) NOT NULL,
	granted_by VARCHAR NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (chat_id, document_id),
	FOREIGN KEY(document_id) REFERENCES ar_document (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_document_chunk (
	id VARCHAR(64) NOT NULL,
	document_id VARCHAR(36) NOT NULL,
	revision INTEGER NOT NULL,
	ordinal INTEGER NOT NULL,
	source_kind VARCHAR(24) NOT NULL,
	section VARCHAR(512),
	page INTEGER,
	start_offset INTEGER NOT NULL,
	end_offset INTEGER NOT NULL,
	content TEXT NOT NULL,
	metadata_json TEXT,
	PRIMARY KEY (id),
	UNIQUE (document_id, revision, ordinal),
	FOREIGN KEY(document_id) REFERENCES ar_document (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_ow_knowledge_file (
	id TEXT NOT NULL,
	knowledge_id TEXT NOT NULL,
	file_id TEXT NOT NULL,
	directory_id TEXT,
	user_id TEXT NOT NULL,
	created_at BIGINT NOT NULL,
	updated_at BIGINT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_knowledge_file_knowledge_file UNIQUE (knowledge_id, file_id),
	UNIQUE (id),
	FOREIGN KEY(knowledge_id) REFERENCES ar_ow_knowledge (id) ON DELETE CASCADE,
	FOREIGN KEY(file_id) REFERENCES ar_ow_file (id) ON DELETE CASCADE,
	FOREIGN KEY(directory_id) REFERENCES ar_ow_knowledge_directory (id) ON DELETE SET NULL
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_preview (
	id VARCHAR(36) NOT NULL,
	document_id VARCHAR(36) NOT NULL,
	owner_id VARCHAR NOT NULL,
	base_revision INTEGER NOT NULL,
	base_sha256 VARCHAR(64),
	artifact_sha256 VARCHAR(64),
	patch_hash VARCHAR(64),
	approved_at TIMESTAMP WITH TIME ZONE,
	drive_version VARCHAR(255),
	operations TEXT NOT NULL,
	status VARCHAR(24) NOT NULL,
	preview_key VARCHAR(1024),
	diff TEXT,
	error TEXT,
	lease_until TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(document_id) REFERENCES ar_document (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_resource_index_job (
	id VARCHAR(36) NOT NULL,
	document_id VARCHAR(36) NOT NULL,
	revision INTEGER NOT NULL,
	status VARCHAR(24) NOT NULL,
	attempts INTEGER NOT NULL,
	error TEXT,
	next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL,
	lease_until TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_ar_resource_index_document_revision UNIQUE (document_id, revision),
	FOREIGN KEY(document_id) REFERENCES ar_document (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_resource_task (
	id VARCHAR(36) NOT NULL,
	document_id VARCHAR(36) NOT NULL,
	owner_id VARCHAR NOT NULL,
	chat_id VARCHAR(255),
	revision INTEGER NOT NULL,
	kind VARCHAR(32) NOT NULL,
	status VARCHAR(24) NOT NULL,
	phase VARCHAR(24) NOT NULL,
	next_ordinal INTEGER NOT NULL,
	next_group INTEGER NOT NULL,
	level INTEGER NOT NULL,
	completed_units INTEGER NOT NULL,
	total_units INTEGER NOT NULL,
	checkpoint TEXT,
	result TEXT,
	error TEXT,
	cancel_requested BOOLEAN NOT NULL,
	lease_until TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(document_id) REFERENCES ar_document (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_revision (
	id VARCHAR(36) NOT NULL,
	document_id VARCHAR(36) NOT NULL,
	number INTEGER NOT NULL,
	object_key VARCHAR(1024) NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	size INTEGER NOT NULL,
	preview_id VARCHAR(36),
	idempotency_key VARCHAR(255),
	operations TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (document_id, number),
	UNIQUE (preview_id),
	CONSTRAINT uq_ar_revision_idempotency_key UNIQUE (idempotency_key),
	FOREIGN KEY(document_id) REFERENCES ar_document (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_resource_analysis (
	task_id VARCHAR(36) NOT NULL,
	request_key VARCHAR(64) NOT NULL,
	message_id VARCHAR(255) NOT NULL,
	prompt TEXT NOT NULL,
	profile TEXT,
	gaps TEXT,
	PRIMARY KEY (task_id),
	FOREIGN KEY(task_id) REFERENCES ar_resource_task (id) ON DELETE CASCADE,
	UNIQUE (request_key)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_revision_approval (
	id VARCHAR(36) NOT NULL,
	idempotency_key VARCHAR(255) NOT NULL,
	preview_id VARCHAR(36),
	user_id VARCHAR(255) NOT NULL,
	resource_id VARCHAR(36) NOT NULL,
	base_revision INTEGER NOT NULL,
	patch_hash VARCHAR(64) NOT NULL,
	approver_id VARCHAR(255) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	consumed_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_ar_revision_approval_idempotency UNIQUE (idempotency_key),
	CONSTRAINT ck_ar_revision_approval_expiration CHECK (expires_at > created_at),
	FOREIGN KEY(preview_id) REFERENCES ar_preview (id) ON DELETE SET NULL,
	FOREIGN KEY(resource_id) REFERENCES ar_document (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_revision_outbox (
	id VARCHAR(36) NOT NULL,
	event_key VARCHAR(255) NOT NULL,
	document_id VARCHAR(36) NOT NULL,
	revision_id VARCHAR(36) NOT NULL,
	event_type VARCHAR(96) NOT NULL,
	payload TEXT NOT NULL,
	attempts INTEGER NOT NULL,
	lease_until TIMESTAMP WITH TIME ZONE,
	published_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_ar_revision_outbox_event_key UNIQUE (event_key),
	FOREIGN KEY(document_id) REFERENCES ar_document (id) ON DELETE CASCADE,
	FOREIGN KEY(revision_id) REFERENCES ar_revision (id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_sync_job (
	id VARCHAR(36) NOT NULL,
	document_id VARCHAR(36) NOT NULL,
	revision_id VARCHAR(36) NOT NULL,
	status VARCHAR(24) NOT NULL,
	attempts INTEGER NOT NULL,
	error TEXT,
	next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (revision_id),
	FOREIGN KEY(document_id) REFERENCES ar_document (id),
	FOREIGN KEY(revision_id) REFERENCES ar_revision (id)
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_resource_analysis_evidence (
	task_id VARCHAR(36) NOT NULL,
	ordinal INTEGER NOT NULL,
	unit TEXT NOT NULL,
	PRIMARY KEY (task_id, ordinal),
	FOREIGN KEY(task_id) REFERENCES ar_resource_analysis (task_id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE TABLE ar_resource_analysis_trace (
	task_id VARCHAR(36) NOT NULL,
	sequence INTEGER NOT NULL,
	step_id VARCHAR(36) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	data TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (task_id, sequence),
	FOREIGN KEY(task_id) REFERENCES ar_resource_analysis (task_id) ON DELETE CASCADE
)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_conv_engine_type_ver ON ar_conversation_engine_bindings (engine_type, engine_version)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_idempotency_exp ON ar_idempotency_records (expires_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_models_active ON ar_models (is_active)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_models_provider ON ar_models (provider)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_object_registry_gc ON ar_object_registry (gc_status, gc_generation)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_object_registry_ref_count ON ar_object_registry (ref_count)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_outbox_dispatch ON ar_outbox (published_at, lease_expires_at, created_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_outbox_type_pub ON ar_outbox (event_type, published_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_receipts_ow_file_id ON ar_upload_receipts (weai_file_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_receipts_principal_exp ON ar_upload_receipts (principal_id, expires_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_users_active ON ar_users (is_active)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_users_role ON ar_users (role)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_access_grants_grantee ON ar_access_grants (grantee_type, grantee_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_access_grants_resource ON ar_access_grants (resource_type, resource_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_revoked_jtis_exp ON ar_auth_revoked_session_jtis (expires_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_revoked_jtis_user ON ar_auth_revoked_session_jtis (user_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_auth_sessions_exp ON ar_auth_sessions (expires_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_auth_sessions_user ON ar_auth_sessions (user_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_chat_folders_user ON ar_chat_folders (user_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_chat_folders_user_name ON ar_chat_folders (user_id, name)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_folder_nodes_owner ON ar_folder_nodes (owner_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_folder_nodes_parent_name ON ar_folder_nodes (parent_folder_id, name)
-- agent-runtime-ddl-boundary --
CREATE UNIQUE INDEX uq_ar_folder_personal_root ON ar_folder_nodes (owner_id) WHERE is_personal_root = true
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_groups_created_by ON ar_groups (created_by)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_groups_name ON ar_groups (name)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_run_bindings_conv ON ar_run_bindings (conversation_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_runs_conv_status ON ar_runs (conversation_id, status)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_runs_created ON ar_runs (created_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_runs_owner_status ON ar_runs (owner_id, status)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_staged_objects_exp ON ar_staged_objects (expires_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_staged_objects_sha256 ON ar_staged_objects (checksum_sha256)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_staged_objects_status_exp ON ar_staged_objects (stage_status, expires_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_agent_artifacts_created ON ar_agent_artifacts (created_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_agent_artifacts_run ON ar_agent_artifacts (run_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_agent_artifacts_sha256 ON ar_agent_artifacts (checksum_sha256)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_chats_created ON ar_chats (created_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_chats_user ON ar_chats (user_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_chats_user_archived ON ar_chats (user_id, is_archived)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_documents_folder ON ar_documents (folder_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_documents_owner ON ar_documents (owner_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_documents_tenant_name ON ar_documents (tenant_id, name)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_exec_leases_exp ON ar_execution_leases (expires_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_exec_leases_worker_exp ON ar_execution_leases (worker_id, expires_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_group_memberships_group ON ar_group_memberships (group_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_group_memberships_user ON ar_group_memberships (user_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_run_events_run_seq ON ar_run_events (run_id, sequence)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_tool_inv_quarantine ON ar_tool_invocations (quarantined)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_tool_inv_status ON ar_tool_invocations (status)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_chat_messages_chat_created ON ar_chat_messages (chat_id, created_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_chat_messages_turn ON ar_chat_messages (turn_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_doc_rev_sha256 ON ar_document_revisions (checksum_sha256)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_comp_rev_doc_no ON ar_compensating_revisions (document_id, compensating_revision_no)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_comp_rev_faulty ON ar_compensating_revisions (faulty_revision_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_doc_tasks_created ON ar_document_tasks (created_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_doc_tasks_doc_rev ON ar_document_tasks (document_id, revision_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_doc_tasks_type_status ON ar_document_tasks (task_type, status)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_edit_previews_doc_base ON ar_edit_previews (document_id, base_revision_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_edit_previews_exp ON ar_edit_previews (expires_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_edit_previews_patch_hash ON ar_edit_previews (canonical_patch_hash)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_export_jobs_doc_format ON ar_export_jobs (document_id, target_format)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_export_jobs_status ON ar_export_jobs (status)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_index_builds_manifest ON ar_index_builds (manifest_hash)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_index_builds_rev_state ON ar_index_builds (revision_id, build_state)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ingestion_jobs_worker ON ar_ingestion_jobs (lease_worker_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_active_indexes_doc_rev ON ar_active_indexes (document_id, revision_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_analysis_jobs_doc_status ON ar_analysis_jobs (document_id, status)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_doc_approvals_doc_status ON ar_document_approvals (document_id, status)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_doc_approvals_user_exp ON ar_document_approvals (approver_user_id, expires_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_anchors_build_kind_sheet_page ON ar_source_anchors (index_build_id, anchor_kind, sheet_id, page_no)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_summary_tasks_doc_status ON ar_summary_tasks (document_id, status)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_worker_leases_exp ON ar_worker_leases (lease_expires_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_worker_leases_node_exp ON ar_worker_leases (worker_node_id, lease_expires_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_doc_paras_sec ON ar_document_paragraphs (section_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_pres_shapes_parent ON ar_presentation_shapes (parent_shape_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_pres_shapes_slide_type ON ar_presentation_shapes (slide_id, shape_type)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_wb_cells_sheet_coord ON ar_workbook_cells (sheet_id, cell_coordinate)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_merged_ranges_anchor ON ar_merged_ranges (anchor_cell_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_merged_ranges_sheet_coord ON ar_merged_ranges (sheet_id, range_coordinate)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_agent_file_content_owner_created ON ar_agent_file_content (owner_id, created_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_agent_file_content_owner_id ON ar_agent_file_content (owner_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_agent_session_owner_updated ON ar_agent_session (owner_id, updated_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_agent_session_chat_id ON ar_agent_session (chat_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_agent_session_owner_id ON ar_agent_session (owner_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_agent_session_parent_session_id ON ar_agent_session (parent_session_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_folder_owner_id ON ar_folder (owner_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_folder_parent_id ON ar_folder (parent_id)
-- agent-runtime-ddl-boundary --
CREATE UNIQUE INDEX uq_ar_doc_folder_personal_root ON ar_folder (owner_id) WHERE is_personal_root = true
-- agent-runtime-ddl-boundary --
CREATE INDEX folder_id_idx ON ar_ow_chat (folder_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX folder_id_user_id_idx ON ar_ow_chat (folder_id, user_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_chat_created_at ON ar_ow_chat (created_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_chat_updated_at ON ar_ow_chat (updated_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_chat_user_id ON ar_ow_chat (user_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX timer_at_idx ON ar_ow_chat (timer_at) WHERE timer_at IS NOT NULL
-- agent-runtime-ddl-boundary --
CREATE INDEX updated_at_user_id_idx ON ar_ow_chat (updated_at, user_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX user_id_archived_idx ON ar_ow_chat (user_id, archived)
-- agent-runtime-ddl-boundary --
CREATE INDEX user_id_folder_unread_idx ON ar_ow_chat (user_id, folder_id, archived, updated_at, last_read_at, id)
-- agent-runtime-ddl-boundary --
CREATE INDEX user_id_pinned_idx ON ar_ow_chat (user_id, pinned)
-- agent-runtime-ddl-boundary --
CREATE INDEX user_id_timer_at_idx ON ar_ow_chat (user_id, timer_at) WHERE timer_at IS NOT NULL
-- agent-runtime-ddl-boundary --
CREATE INDEX user_id_updated_at_id_idx ON ar_ow_chat (user_id, updated_at DESC, id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_file_created_at ON ar_ow_file (created_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_file_user_id ON ar_ow_file (user_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_group_parent_group_id ON ar_ow_group (parent_group_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_memory_id_user_id ON ar_ow_memory (id, user_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_memory_type ON ar_ow_memory (type)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_memory_user_id ON ar_ow_memory (user_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_model_history_model_id ON ar_ow_model_history (model_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_notification_object ON ar_ow_notification (object_type, object_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_notification_resource ON ar_ow_notification (resource_type, resource_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_notification_unseen ON ar_ow_notification (user_id, created_at, id) WHERE seen_at IS NULL
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_notification_user_created ON ar_ow_notification (user_id, created_at, id)
-- agent-runtime-ddl-boundary --
CREATE INDEX idx_oauth_session_expires_at ON ar_ow_oauth_session (expires_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX idx_oauth_session_user_id ON ar_ow_oauth_session (user_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX idx_oauth_session_user_provider ON ar_ow_oauth_session (user_id, provider)
-- agent-runtime-ddl-boundary --
CREATE UNIQUE INDEX ix_ar_ow_prompt_command ON ar_ow_prompt (command)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_prompt_user_id ON ar_ow_prompt (user_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_prompt_history_prompt_id ON ar_ow_prompt_history (prompt_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_skill_history_skill_id ON ar_ow_skill_history (skill_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_tag_name ON ar_ow_tag (name)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_tag_user_id ON ar_ow_tag (user_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_tool_created_at ON ar_ow_tool (created_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_tool_user_id ON ar_ow_tool (user_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_tool_history_tool_id ON ar_ow_tool_history (tool_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_agent_turn_claim ON ar_agent_turn (status, created_at, lease_until)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_agent_turn_session_chat_message ON ar_agent_turn (session_id, chat_message_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_agent_turn_chat_message_id ON ar_agent_turn (chat_message_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_agent_turn_session_id ON ar_agent_turn (session_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_agent_turn_status ON ar_agent_turn (status)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_document_folder_id ON ar_document (folder_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_document_owner_id ON ar_document (owner_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX chat_message_chat_parent_idx ON ar_ow_chat_message (chat_id, parent_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX chat_message_chat_role_done_idx ON ar_ow_chat_message (chat_id, role, done)
-- agent-runtime-ddl-boundary --
CREATE INDEX chat_message_model_created_idx ON ar_ow_chat_message (model_id, created_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX chat_message_user_created_idx ON ar_ow_chat_message (user_id, created_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_chat_message_chat_id ON ar_ow_chat_message (chat_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_chat_message_created_at ON ar_ow_chat_message (created_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_chat_message_model_id ON ar_ow_chat_message (model_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_ow_chat_message_user_id ON ar_ow_chat_message (user_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_group_member_user_id_group_id ON ar_ow_group_member (user_id, group_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_knowledge_directory_knowledge_id ON ar_ow_knowledge_directory (knowledge_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_knowledge_directory_parent_id ON ar_ow_knowledge_directory (parent_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_agent_artifact_owner_created ON ar_agent_artifact (owner_id, created_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_agent_artifact_turn ON ar_agent_artifact (turn_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_agent_artifact_owner_id ON ar_agent_artifact (owner_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_agent_checkpoint_session_created ON ar_agent_checkpoint (session_id, created_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_agent_checkpoint_session_id ON ar_agent_checkpoint (session_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_agent_checkpoint_turn_id ON ar_agent_checkpoint (turn_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_agent_event_outbox ON ar_agent_event (published_at, created_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_agent_event_session_id ON ar_agent_event (session_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_agent_event_turn_id ON ar_agent_event (turn_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_agent_tool_turn_status ON ar_agent_tool_invocation (turn_id, status)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_agent_tool_invocation_session_id ON ar_agent_tool_invocation (session_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_agent_tool_invocation_status ON ar_agent_tool_invocation (status)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_agent_tool_invocation_turn_id ON ar_agent_tool_invocation (turn_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_chat_resource_chat_id ON ar_chat_resource (chat_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_chat_resource_document_id ON ar_chat_resource (document_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_chat_resource_granted_by ON ar_chat_resource (granted_by)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_document_chunk_document_id ON ar_document_chunk (document_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_document_chunk_revision ON ar_document_chunk (revision)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_knowledge_file_directory_id ON ar_ow_knowledge_file (directory_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_preview_document_id ON ar_preview (document_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_resource_index_job_document_id ON ar_resource_index_job (document_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_resource_index_job_status ON ar_resource_index_job (status)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_resource_index_status_next_attempt ON ar_resource_index_job (status, next_attempt_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_resource_task_chat_id ON ar_resource_task (chat_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_resource_task_document_id ON ar_resource_task (document_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_resource_task_owner_id ON ar_resource_task (owner_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_resource_task_status ON ar_resource_task (status)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_revision_document_id ON ar_revision (document_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_revision_approval_approver_id ON ar_revision_approval (approver_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_revision_approval_preview_id ON ar_revision_approval (preview_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_revision_approval_resource_id ON ar_revision_approval (resource_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_revision_approval_user_id ON ar_revision_approval (user_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_revision_outbox_dispatch ON ar_revision_outbox (published_at, lease_until, created_at)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_revision_outbox_document_id ON ar_revision_outbox (document_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_revision_outbox_revision_id ON ar_revision_outbox (revision_id)
-- agent-runtime-ddl-boundary --
CREATE INDEX ix_ar_sync_job_document_id ON ar_sync_job (document_id)
