"""Agent Runtime V2 Canonical Document Tools and Pagination.

Exports the 9 canonical document tools, cursor codec, and wire pagination engine.
"""

from weai.persistence.v2.tools.cursor import (
    CursorBuildRetiredError,
    CursorError,
    CursorExpiredError,
    CursorInvalidError,
    CursorScopeMismatchError,
    CursorValidationError,
    compute_scope_digest,
    register_cursor_key,
    reset_cursor_key_registry,
    revoke_cursor_key,
    sign_cursor,
    verify_cursor,
)
from weai.persistence.v2.tools.document_tools import (
    CONTRACT_VERSION,
    DocumentToolError,
    ToolError,
    execute_document_analysis,
    export_document,
    get_document_task,
    inspect_document,
    propose_document_edit,
    read_document_range,
    read_spreadsheet_range,
    search_document,
    start_document_summary,
)
from weai.persistence.v2.tools.pagination import (
    DEFAULT_WIRE_BUDGET_CHARS,
    OutputBudgetTooSmallError,
    measure_wire_characters,
    measure_wire_metrics,
    paginate_records,
)

__all__ = [
    # 9 Tools
    "inspect_document",
    "read_document_range",
    "read_spreadsheet_range",
    "search_document",
    "execute_document_analysis",
    "start_document_summary",
    "get_document_task",
    "export_document",
    "propose_document_edit",
    # Pagination & Wire Budget
    "DEFAULT_WIRE_BUDGET_CHARS",
    "paginate_records",
    "measure_wire_characters",
    "measure_wire_metrics",
    "OutputBudgetTooSmallError",
    # Cursor Codec
    "sign_cursor",
    "verify_cursor",
    "compute_scope_digest",
    "register_cursor_key",
    "revoke_cursor_key",
    "reset_cursor_key_registry",
    "CursorError",
    "CursorInvalidError",
    "CursorExpiredError",
    "CursorScopeMismatchError",
    "CursorBuildRetiredError",
    # Envelopes
    "ToolError",
    "CONTRACT_VERSION",
]
