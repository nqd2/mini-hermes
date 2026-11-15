# V2-R1 Format Capability & Edit Allowlist

**Status:** Locked / Accepted. Mandatory format capabilities and explicit mutation allowlists for XLSX, DOCX, PPTX, and read-only PDF.

## Required format capability

| Capability | XLSX | DOCX | PPTX | PDF |
|---|---|---|---|---|
| Upload/validate/store immutable original | Required | Required | Required | Required |
| Inspect structured metadata | Sheets/visibility/dimensions | Paragraphs/tables/sections | Slides/shapes | Pages/text layer presence |
| Bounded read + stable anchors | Cell, row, range | Paragraph/table cell | Slide, shape/text | Page/text span |
| Search + source provenance | Required | Required | Required | Required |
| Summary / analysis via sandbox | Required | Required | Required | Text-layer only; no OCR |
| Mutation and revision | Allowlist only | Allowlist only | Allowlist only | **Denied** |
| Export | XLSX | DOCX | PPTX | Original/download/read-only artifact |

## Current parser baseline on synthetic fixtures

The checkout's V1 `extract_sections` does not meet these V2 capabilities in all cases: on the Phase 0 fixtures it omitted DOCX header/footer text and PPTX grouped-shape text, and exposed a PDF image-only page as empty text without image-coverage metadata. See the [test plan](08-test-plan.md) and `packages/backend/tests/test_v2_phase0_parser_characterization.py`. This baseline is limited to those fixtures; it neither establishes V2 behavior nor authorizes treating omitted content as complete.

## Proposed minimal edit allowlists for R1

### XLSX (`xlsx.set_cell_value`, `xlsx.clear_cell_value`)
- Explicit workbook/sheet ID/cell coordinate; user-provided scalar value types only. Preserve all untouched cells/styles/formulas/merges; refuse modification of merged non-anchor, protected sheets, unsupported data validations, external-link/formula-invalidating paths, and ambiguous structured references.
- Formula authoring, fill ranges, row/column insertion/deletion, chart/pivot modification are **out of initial allowlist** pending separate tested approval. Formula cells may be read but not changed by default.

### DOCX (`docx.replace_exact_text_in_paragraph`)
- Target stable paragraph anchor + exact text occurrence and base revision; replacement of text runs allowed only if output can preserve paragraph structure and formatting under the acceptance fixtures. Reject tracked-change edge cases, fields, hyperlinks, unsupported content or ambiguous replacements. Table-cell replacement requires a separately approved operation if not proven.

### PPTX (`pptx.replace_shape_text`)
- Target stable slide ID + shape ID and exact original text/hash; replace text only on supported editable text shapes. Reject grouped/placeholder/unhandled shapes or content where layout integrity cannot be shown. No slide creation, media/chart manipulation in initial allowlist.

### PDF
- `propose_document_edit` yields `UNSUPPORTED_EDIT_OPERATION` for all mutations. Image-only/scanned PDFs should be detected and reported `TEXT_LAYER_UNAVAILABLE` or analogous typed status; OCR is explicitly out of scope.

## Universal edit requirements

No silent destructive coercion; operation-specific schema validation and deterministic canonical patch hash; preview shows actual diff; explicit approval expires 15 minutes after preview creation; current authorization rechecked; optimistic revision precondition; atomic DB publication; deterministic retry; rollback creates compensating revision. Export round-trip must preserve untouched Office structures to the degree declared in acceptance matrix; do not promise full pixel fidelity without actual tests.

## Acceptance owner checks

For each operation: positive + negative fixture; format parser/library path; reversible preview; file opens in target application; original non-target content preserved; macro/encrypted/corrupt inputs rejected or handled explicitly; checksum of published bytes verified. Owner may narrow but not silently expand the allowlist.
