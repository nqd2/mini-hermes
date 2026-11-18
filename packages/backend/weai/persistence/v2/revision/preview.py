"""In-Memory Preview Generation, Format Allowlist Enforcement & Canonical Patch Hash (M4.2).

Enforces:
1. Canonical Patch Hash v1 (`SHA-256("mhv2:patch:v1\\n" + canonical_json)`) and
   Idempotency Request Fingerprint v1 (`SHA-256("mhv2:request:v1\\n" + canonical_json)`)
   per `docs/v2/phase0/03b-idempotency-hash-contract.md` and `hash-vectors-v1.json`.
2. Strict Format Capability Matrix allowlist (`docs/v2/phase0/06-format-capability-matrix.md`):
   - XLSX: `xlsx.set_cell_value` (`set_cell_value`, `set_cell`), `xlsx.clear_cell_value` (`clear_cell_value`, `clear_cell`)
     with Formula Protection Guardrail, non-anchor merged cell rejection, protected sheet rejection,
     and `fullCalcOnLoad = True`.
   - DOCX: `docx.replace_exact_text_in_paragraph` (`replace_exact_text_in_paragraph`, `replace_text`)
     via `RunSpanMatcher` preserving `<w:rPr>` formatting and failing closed on tracked changes, fields, hyperlinks.
   - PPTX: `pptx.replace_shape_text` (`replace_shape_text`) failing closed on `GroupShape` container edits.
   - PDF: Unconditional rejection with HTTP 422 `format_edit_unsupported` (`FormatEditUnsupportedError`).
   - Any operation outside the allowlist is explicitly rejected with HTTP 422 `UNSUPPORTED_EDIT_OPERATION`.
3. In-memory preview & structural diff generation without mutating the base document.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from weai.persistence.v2.parsers.docx import DocxFormatError, DocxParser
from weai.persistence.v2.parsers.pdf import FormatEditUnsupportedError, PdfParser
from weai.persistence.v2.parsers.pptx import PptxFormatError, PptxParser
from weai.persistence.v2.parsers.xlsx import (
    FormulaProtectionViolationError,
    MergedNonAnchorCellError,
    ProtectedSheetError,
    XlsxFormatError,
    XlsxParser,
)
from weai.persistence.v2.revision.state_machine import RevisionError

PATCH_HASH_DOMAIN = b"mhv2:patch:v1\n"
REQUEST_HASH_DOMAIN = b"mhv2:request:v1\n"

ALLOWED_XLSX_OPS = {
    "xlsx.set_cell_value",
    "set_cell_value",
    "set_cell",
    "xlsx.clear_cell_value",
    "clear_cell_value",
    "clear_cell",
}

ALLOWED_DOCX_OPS = {
    "docx.replace_exact_text_in_paragraph",
    "replace_exact_text_in_paragraph",
    "replace_text",
}

ALLOWED_PPTX_OPS = {
    "pptx.replace_shape_text",
    "replace_shape_text",
}


class UnsupportedEditOperationError(RevisionError):
    """Raised when an operation is outside the format capability allowlist."""

    def __init__(self, format_type: str, operation: str, reason: str | None = None) -> None:
        msg = f"Operation '{operation}' is not permitted on format '{format_type}'."
        if reason:
            msg = f"{msg} {reason}"
        super().__init__(
            msg,
            code="UNSUPPORTED_EDIT_OPERATION",
            status_code=422,
            retryable=False,
            details={
                "format": format_type,
                "operation": operation,
                "legacy_code": "format_edit_unsupported" if format_type == "pdf" else "unsupported_edit_operation",
            },
        )


class InvalidPatchPayloadError(RevisionError):
    """Raised when canonical JSON input contains non-finite numbers, invalid keys, or surrogates."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            code="INVALID_ARGUMENT",
            status_code=422,
            retryable=False,
        )


def _validate_canonical_value(val: Any) -> None:
    """Recursively validates JSON values for canonical serialization (no NaN/Inf, no non-string keys, no surrogates)."""
    if val is None or isinstance(val, (bool, int)):
        return
    if isinstance(val, float):
        if not math.isfinite(val):
            raise InvalidPatchPayloadError(f"Non-finite float value '{val}' is forbidden in canonical JSON.")
        return
    if isinstance(val, str):
        try:
            val.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise InvalidPatchPayloadError(f"Unpaired Unicode surrogate in string is forbidden: {exc}") from exc
        return
    if isinstance(val, list):
        for item in val:
            _validate_canonical_value(item)
        return
    if isinstance(val, dict):
        for k, v in val.items():
            if not isinstance(k, str):
                raise InvalidPatchPayloadError(f"Non-string dictionary key '{k!r}' is forbidden in canonical JSON.")
            _validate_canonical_value(k)
            _validate_canonical_value(v)
        return
    raise InvalidPatchPayloadError(f"Unsupported type '{type(val).__name__}' in canonical JSON payload.")


def serialize_canonical_json(payload: dict[str, Any]) -> bytes:
    """Serializes a validated dictionary into canonical UTF-8 JSON bytes per D03b."""
    if not isinstance(payload, dict):
        raise InvalidPatchPayloadError("Canonical payload root must be a JSON object.")
    _validate_canonical_value(payload)
    try:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return text.encode("utf-8", errors="strict")
    except (ValueError, TypeError, UnicodeEncodeError) as exc:
        raise InvalidPatchPayloadError(f"Canonical JSON serialization failed: {exc}") from exc


def parse_json_rejecting_duplicate_keys(raw_json: str | bytes) -> Any:
    """Parses JSON while strictly rejecting duplicate object keys per D03b."""
    def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for k, v in pairs:
            if not isinstance(k, str):
                raise InvalidPatchPayloadError(f"Object key must be string, got {type(k).__name__}.")
            if k in result:
                raise InvalidPatchPayloadError(f"Duplicate JSON object key '{k}' is forbidden.")
            result[k] = v
        return result

    text = raw_json.decode("utf-8", errors="strict") if isinstance(raw_json, bytes) else raw_json
    try:
        parsed = json.loads(text, object_pairs_hook=_no_duplicates)
    except InvalidPatchPayloadError:
        raise
    except Exception as exc:
        raise InvalidPatchPayloadError(f"Invalid JSON input: {exc}") from exc
    _validate_canonical_value(parsed)
    return parsed


def compute_canonical_patch_hash(
    *,
    document_id: str,
    base_revision: int,
    base_sha256: str,
    operations: list[dict[str, Any]],
    artifact_sha256: str,
) -> str:
    """Computes Patch Hash v1 per D03b (`SHA-256('mhv2:patch:v1\\n' || canonical_bytes)`).

    Matches the exact reference vector in `docs/v2/phase0/fixtures/hash-vectors-v1.json`.
    """
    if not isinstance(base_revision, int) or isinstance(base_revision, bool) or base_revision < 1:
        raise InvalidPatchPayloadError("base_revision must be a positive integer.")
    if not isinstance(operations, list) or len(operations) == 0:
        raise InvalidPatchPayloadError("operations must be a non-empty list.")

    canonical_envelope = {
        "artifact_sha256": str(artifact_sha256).lower(),
        "base_revision": int(base_revision),
        "base_sha256": str(base_sha256).lower(),
        "document_id": str(document_id),
        "operations": operations,
        "schema": "mhv2.patch.v1",
    }
    canonical_bytes = serialize_canonical_json(canonical_envelope)
    return hashlib.sha256(PATCH_HASH_DOMAIN + canonical_bytes).hexdigest().lower()


def compute_request_fingerprint(
    *,
    principal_id: str,
    operation: str,
    request: dict[str, Any],
) -> str:
    """Computes Idempotency Request Fingerprint v1 per D03b (`SHA-256('mhv2:request:v1\\n' || canonical_bytes)`).

    Matches the exact reference vector in `docs/v2/phase0/fixtures/hash-vectors-v1.json`.
    """
    canonical_envelope = {
        "operation": str(operation),
        "principal_id": str(principal_id),
        "request": request,
        "schema": "mhv2.request.v1",
    }
    canonical_bytes = serialize_canonical_json(canonical_envelope)
    return hashlib.sha256(REQUEST_HASH_DOMAIN + canonical_bytes).hexdigest().lower()


def apply_operations_in_memory(
    *,
    format_type: str,
    base_bytes: bytes,
    operations: list[dict[str, Any]],
) -> tuple[bytes, str, list[dict[str, Any]]]:
    """Validates operations against the format capability matrix and applies them in memory.

    Never mutates the base document on disk or in object storage.
    Returns `(staged_bytes, diff_summary, structured_diffs)`.
    """
    fmt = format_type.lower().strip()

    # 1. PDF Read-Only Invariant: Unconditional HTTP 422 format_edit_unsupported
    if fmt == "pdf":
        PdfParser.propose_edit(operations=operations)

    if not isinstance(operations, list) or len(operations) == 0:
        raise InvalidPatchPayloadError("Operations list cannot be empty.")

    current_bytes = base_bytes
    diff_lines: list[str] = []
    structured_diffs: list[dict[str, Any]] = []

    if fmt == "xlsx":
        for idx, op_dict in enumerate(operations):
            if not isinstance(op_dict, dict):
                raise InvalidPatchPayloadError(f"Operation at index {idx} must be a JSON object.")
            _validate_canonical_value(op_dict)

            op_name = str(op_dict.get("op") or op_dict.get("operation") or "").strip()
            if op_name not in ALLOWED_XLSX_OPS:
                raise UnsupportedEditOperationError(
                    "xlsx",
                    op_name or "<missing>",
                    "Allowed XLSX operations: xlsx.set_cell_value, xlsx.clear_cell_value.",
                )

            sheet_name = str(op_dict.get("sheet") or op_dict.get("sheet_name") or "").strip()
            coordinate = str(op_dict.get("cell") or op_dict.get("coordinate") or "").strip().upper()
            if not sheet_name or not coordinate:
                raise InvalidPatchPayloadError(
                    f"XLSX operation '{op_name}' requires 'sheet' (or 'sheet_name') and 'cell' (or 'coordinate')."
                )

            # Inspect before value for accurate diff
            before_model = XlsxParser.parse(current_bytes)
            before_sheet = before_model.get_sheet(sheet_name)
            if before_sheet is None:
                raise RevisionError(
                    f"Worksheet '{sheet_name}' not found in workbook.",
                    code="INVALID_ARGUMENT",
                    status_code=404,
                )
            before_cell = before_sheet.get_cell(coordinate)
            before_val = (
                before_cell.formula_expression
                if (before_cell and before_cell.has_formula)
                else (before_cell.raw_value if before_cell else None)
            )

            canonical_op = (
                "xlsx.clear_cell_value"
                if op_name in ("xlsx.clear_cell_value", "clear_cell_value", "clear_cell")
                else "xlsx.set_cell_value"
            )
            new_val = None if canonical_op == "xlsx.clear_cell_value" else op_dict.get("value")

            try:
                current_bytes = XlsxParser.apply_edit(
                    content=current_bytes,
                    sheet_name=sheet_name,
                    coordinate=coordinate,
                    operation=canonical_op,
                    value=new_val,
                )
            except FormulaProtectionViolationError as exc:
                raise RevisionError(
                    exc.message,
                    code=exc.code,
                    status_code=exc.status_code,
                    details={
                        "coordinate": exc.coordinate,
                        "formula": exc.formula,
                        "attempted_value": exc.attempted_value,
                    },
                ) from exc
            except MergedNonAnchorCellError as exc:
                raise RevisionError(
                    exc.message,
                    code=exc.code,
                    status_code=exc.status_code,
                    details={
                        "coordinate": exc.coordinate,
                        "anchor": exc.anchor,
                        "range": exc.range_coord,
                    },
                ) from exc
            except ProtectedSheetError as exc:
                raise RevisionError(
                    exc.message,
                    code=exc.code,
                    status_code=exc.status_code,
                ) from exc
            except XlsxFormatError as exc:
                raise RevisionError(
                    exc.message,
                    code=exc.code,
                    status_code=exc.status_code,
                ) from exc

            diff_lines.append(
                f"--- {sheet_name}!{coordinate}\n"
                f"- {before_val!r}\n"
                f"+ {new_val!r}"
            )
            structured_diffs.append(
                {
                    "format": "xlsx",
                    "op": canonical_op,
                    "sheet": sheet_name,
                    "coordinate": coordinate,
                    "before": before_val,
                    "after": new_val,
                }
            )

    elif fmt == "docx":
        for idx, op_dict in enumerate(operations):
            if not isinstance(op_dict, dict):
                raise InvalidPatchPayloadError(f"Operation at index {idx} must be a JSON object.")
            _validate_canonical_value(op_dict)

            op_name = str(op_dict.get("op") or op_dict.get("operation") or "").strip()
            if op_name not in ALLOWED_DOCX_OPS:
                raise UnsupportedEditOperationError(
                    "docx",
                    op_name or "<missing>",
                    "Allowed DOCX operations: docx.replace_exact_text_in_paragraph.",
                )

            para_order = op_dict.get("paragraph_order") or op_dict.get("paragraph") or 1
            if not isinstance(para_order, int) or isinstance(para_order, bool) or para_order < 1:
                raise InvalidPatchPayloadError("DOCX operation requires positive integer 'paragraph_order'.")

            target_text = op_dict.get("target_text") if "target_text" in op_dict else op_dict.get("find")
            replacement_text = (
                op_dict.get("replacement_text")
                if "replacement_text" in op_dict
                else op_dict.get("replace")
            )
            occurrence = op_dict.get("occurrence", 1)

            if not isinstance(target_text, str) or not target_text:
                raise InvalidPatchPayloadError("DOCX operation requires non-empty 'target_text' (or 'find').")
            if not isinstance(replacement_text, str):
                raise InvalidPatchPayloadError("DOCX operation requires string 'replacement_text' (or 'replace').")

            try:
                current_bytes = DocxParser.apply_edit(
                    content=current_bytes,
                    paragraph_order=para_order,
                    target_text=target_text,
                    replacement_text=replacement_text,
                    occurrence=occurrence,
                )
            except DocxFormatError as exc:
                raise RevisionError(
                    exc.message,
                    code=exc.code,
                    status_code=exc.status_code,
                ) from exc

            diff_lines.append(
                f"--- paragraph:{para_order} (occurrence {occurrence})\n"
                f"- {target_text}\n"
                f"+ {replacement_text}"
            )
            structured_diffs.append(
                {
                    "format": "docx",
                    "op": "docx.replace_exact_text_in_paragraph",
                    "paragraph_order": para_order,
                    "occurrence": occurrence,
                    "before": target_text,
                    "after": replacement_text,
                }
            )

    elif fmt == "pptx":
        for idx, op_dict in enumerate(operations):
            if not isinstance(op_dict, dict):
                raise InvalidPatchPayloadError(f"Operation at index {idx} must be a JSON object.")
            _validate_canonical_value(op_dict)

            op_name = str(op_dict.get("op") or op_dict.get("operation") or "").strip()
            if op_name not in ALLOWED_PPTX_OPS:
                raise UnsupportedEditOperationError(
                    "pptx",
                    op_name or "<missing>",
                    "Allowed PPTX operations: pptx.replace_shape_text.",
                )

            slide_order = op_dict.get("slide_order") or op_dict.get("slide") or 1
            shape_id = op_dict.get("shape_id")
            target_text = op_dict.get("target_text") if "target_text" in op_dict else op_dict.get("find")
            replacement_text = (
                op_dict.get("replacement_text")
                if "replacement_text" in op_dict
                else op_dict.get("replace")
            )

            if not isinstance(slide_order, int) or isinstance(slide_order, bool) or slide_order < 1:
                raise InvalidPatchPayloadError("PPTX operation requires positive integer 'slide_order'.")
            if not isinstance(shape_id, int) or isinstance(shape_id, bool):
                raise InvalidPatchPayloadError("PPTX operation requires integer 'shape_id'.")
            if not isinstance(target_text, str) or not target_text:
                raise InvalidPatchPayloadError("PPTX operation requires non-empty 'target_text' (or 'find').")
            if not isinstance(replacement_text, str):
                raise InvalidPatchPayloadError("PPTX operation requires string 'replacement_text' (or 'replace').")

            try:
                current_bytes = PptxParser.apply_edit(
                    content=current_bytes,
                    slide_order=slide_order,
                    shape_id=shape_id,
                    target_text=target_text,
                    replacement_text=replacement_text,
                )
            except PptxFormatError as exc:
                raise RevisionError(
                    exc.message,
                    code=exc.code,
                    status_code=exc.status_code,
                ) from exc

            diff_lines.append(
                f"--- slide:{slide_order}/shape:{shape_id}\n"
                f"- {target_text}\n"
                f"+ {replacement_text}"
            )
            structured_diffs.append(
                {
                    "format": "pptx",
                    "op": "pptx.replace_shape_text",
                    "slide_order": slide_order,
                    "shape_id": shape_id,
                    "before": target_text,
                    "after": replacement_text,
                }
            )

    else:
        raise UnsupportedEditOperationError(fmt, "any", f"Unsupported document format '{fmt}'.")

    diff_summary = "\n".join(diff_lines)
    return current_bytes, diff_summary, structured_diffs
