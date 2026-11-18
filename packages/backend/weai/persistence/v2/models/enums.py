"""V2 domain enumerations.

Strongly typed string enumerations for all Agent Runtime V2 lifecycle states,
format kinds, task types, and RBAC primitives.
"""

from enum import StrEnum


class FormatType(StrEnum):
    XLSX = "xlsx"
    DOCX = "docx"
    PPTX = "pptx"
    PDF = "pdf"


class PublicationState(StrEnum):
    STAGED = "STAGED"
    VERIFIED = "VERIFIED"
    COMMITTED = "COMMITTED"
    AVAILABLE = "AVAILABLE"


class IndexBuildState(StrEnum):
    REQUESTED = "REQUESTED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    READY = "READY"
    RETIRED = "RETIRED"
    FAILED = "FAILED"


class CellDataType(StrEnum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    FORMULA = "formula"
    EMPTY = "empty"
    ERROR = "error"


class CachedValueState(StrEnum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    DIRTY = "DIRTY"


class SheetVisibility(StrEnum):
    VISIBLE = "visible"
    HIDDEN = "hidden"
    VERY_HIDDEN = "very_hidden"


class ShapeType(StrEnum):
    TEXT_BOX = "text_box"
    AUTO_SHAPE = "auto_shape"
    GROUP = "group"
    TABLE = "table"
    PICTURE = "picture"
    GRAPHIC_FRAME = "graphic_frame"


class PreviewStatus(StrEnum):
    READY = "ready"
    APPLIED = "applied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ToolInvocationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class TaskType(StrEnum):
    INGEST = "ingest"
    PARSE = "parse"
    CHUNK = "chunk"
    INDEX = "index"
    SUMMARY = "summary"
    ANALYSIS = "analysis"
    EXPORT = "export"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UserRole(StrEnum):
    ADMIN = "admin"
    USER = "user"
    REVIEWER = "reviewer"
    AUDITOR = "auditor"


class GranteeType(StrEnum):
    USER = "user"
    GROUP = "group"


class ResourceType(StrEnum):
    DOCUMENT = "document"
    FOLDER = "folder"
    WORKSPACE = "workspace"


class PermissionKind(StrEnum):
    READ = "read"
    EDIT = "edit"
    ADMIN = "admin"
