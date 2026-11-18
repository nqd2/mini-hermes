"""DOCX Canonical Parser and OpenXML RunSpanMatcher."""

from __future__ import annotations

import io
import re
from typing import Any

import docx
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from weai.persistence.v2.canonical.document import (
    CanonicalDocumentModel,
    CanonicalParagraph,
    CanonicalRun,
    CanonicalSection,
    CanonicalTable,
)


class DocxFormatError(Exception):
    def __init__(self, message: str, code: str = "docx_error", status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class UnsupportedContentError(DocxFormatError):
    """Raised when target paragraph contains tracked changes or complex fields."""

    def __init__(self, reason: str):
        super().__init__(
            f"DOCX edit rejected due to unsupported content: {reason}",
            code="docx_unsupported_content",
            status_code=422,
        )


class TextNotFoundError(DocxFormatError):
    def __init__(self, text: str):
        super().__init__(
            f"Target text '{text}' was not found in specified paragraph.",
            code="docx_text_not_found",
            status_code=404,
        )


class AmbiguousMatchError(DocxFormatError):
    def __init__(self, text: str, occurrences: int):
        super().__init__(
            f"Target text '{text}' occurs {occurrences} times in paragraph. Explicit occurrence index required.",
            code="docx_ambiguous_match",
            status_code=422,
        )


class RunSpanMatcher:
    """Executes search-and-replace across multi-run paragraph spans preserving <w:rPr>."""

    @classmethod
    def replace_in_paragraph(
        cls,
        paragraph: Paragraph,
        target_text: str,
        replacement_text: str,
        occurrence: int = 1,
    ) -> bool:
        """Finds target_text across runs and replaces with replacement_text preserving styles."""
        if not target_text:
            raise DocxFormatError("Target text cannot be empty.", status_code=422)

        # 1. Guardrail against tracked changes and complex fields
        p_elm = paragraph._p
        if p_elm.xpath(".//w:ins | .//w:del | .//w:moveFrom | .//w:moveTo"):
            raise UnsupportedContentError("Paragraph contains tracked changes (revisions).")
        if p_elm.xpath(".//w:fldSimple | .//w:fldChar"):
            raise UnsupportedContentError("Paragraph contains complex Word fields.")

        # 2. Extract paragraph full text and search
        full_text = paragraph.text
        matches = [m.start() for m in re.finditer(re.escape(target_text), full_text)]
        if not matches:
            raise TextNotFoundError(target_text)

        if len(matches) > 1 and occurrence is None:
            raise AmbiguousMatchError(target_text, len(matches))

        if occurrence < 1 or occurrence > len(matches):
            raise DocxFormatError(
                f"Invalid occurrence {occurrence}; total found: {len(matches)}.",
                status_code=422,
            )

        match_start = matches[occurrence - 1]
        match_end = match_start + len(target_text)

        # 3. Inventory all runs (including nested inside hyperlinks) and character intervals
        from docx.text.run import Run
        runs = [Run(r_elm, paragraph) for r_elm in paragraph._p.xpath(".//w:r")]
        run_intervals: list[tuple[int, int, Any]] = []
        curr = 0
        for r in runs:
            r_len = len(r.text)
            run_intervals.append((curr, curr + r_len, r))
            curr += r_len

        # 4. Find opening and closing runs
        p_idx = None
        q_idx = None
        for i, (r_start, r_end, r) in enumerate(run_intervals):
            if r_start <= match_start < r_end:
                p_idx = i
            if r_start < match_end <= r_end:
                q_idx = i

        if p_idx is None or q_idx is None:
            raise DocxFormatError("Boundary calculation failed.", status_code=500)

        # 5. Check hyperlink boundary intersection
        # Runs inside hyperlinks must not be partially severed
        for i in range(p_idx, q_idx + 1):
            run_elm = run_intervals[i][2]._r
            if run_elm.xpath("ancestor::w:hyperlink"):
                raise UnsupportedContentError("Matched text intersects a hyperlink.")

        # 6. Execute replacement preserving <w:rPr>
        if p_idx == q_idx:
            # Single-run match
            r_start, _, r = run_intervals[p_idx]
            prefix = r.text[: match_start - r_start]
            suffix = r.text[match_end - r_start :]
            r.text = prefix + replacement_text + suffix
        else:
            # Multi-run span match
            # Opening run: retains prefix + receives replacement (inheriting opening style)
            p_start, _, r_p = run_intervals[p_idx]
            prefix = r_p.text[: match_start - p_start]
            r_p.text = prefix + replacement_text

            # Intermediate runs: cleared
            for m in range(p_idx + 1, q_idx):
                run_intervals[m][2].text = ""

            # Closing run: retains suffix (preserving closing style)
            q_start, _, r_q = run_intervals[q_idx]
            suffix = r_q.text[match_end - q_start :]
            r_q.text = suffix

        return True


class DocxParser:
    """High-fidelity DOCX parser extracting sections, headers, footers, and runs."""

    @staticmethod
    def parse(content: bytes) -> CanonicalDocumentModel:
        doc = docx.Document(io.BytesIO(content))
        sections: list[CanonicalSection] = []

        global_para_order = 1
        global_table_order = 1

        for s_idx, sec in enumerate(doc.sections, start=1):
            # Header & Footer extraction
            header_text = None
            if sec.header and sec.header.paragraphs:
                header_parts = [p.text for p in sec.header.paragraphs if p.text]
                if header_parts:
                    header_text = "\n".join(header_parts)

            footer_text = None
            if sec.footer and sec.footer.paragraphs:
                footer_parts = [p.text for p in sec.footer.paragraphs if p.text]
                if footer_parts:
                    footer_text = "\n".join(footer_parts)

            props = {
                "page_width_pt": sec.page_width.pt if sec.page_width else None,
                "page_height_pt": sec.page_height.pt if sec.page_height else None,
                "orientation": str(sec.orientation),
            }

            c_section = CanonicalSection(
                section_order=s_idx,
                header_text=header_text,
                footer_text=footer_text,
                properties=props,
            )

            # In standard docx, doc.paragraphs represent body paragraphs
            if s_idx == 1:
                for p in doc.paragraphs:
                    runs: list[CanonicalRun] = []
                    all_p_runs = [Run(r_elm, p) for r_elm in p._p.xpath(".//w:r")]
                    for r in all_p_runs:
                        font = r.font
                        color_rgb = str(font.color.rgb) if font and font.color and font.color.rgb else None
                        size_pt = font.size.pt if font and font.size else None
                        is_hyperlink = bool(r._r.xpath("ancestor::w:hyperlink"))
                        runs.append(
                            CanonicalRun(
                                text=r.text,
                                bold=r.bold,
                                italic=r.italic,
                                underline=r.underline,
                                font_name=font.name if font else None,
                                font_size_pt=size_pt,
                                color_rgb=color_rgb,
                                is_hyperlink=is_hyperlink,
                            )
                        )

                    # Hyperlinks extraction
                    hyperlinks = []
                    for h_elm in p._p.xpath(".//w:hyperlink"):
                        r_id = h_elm.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
                        target = doc.part.rels[r_id].target_ref if (r_id and r_id in doc.part.rels) else None
                        h_text = "".join(h_elm.xpath(".//w:t/text()"))
                        hyperlinks.append({"r_id": r_id, "target": target, "text": h_text})

                    c_section.paragraphs.append(
                        CanonicalParagraph(
                            paragraph_order=global_para_order,
                            style_id=p.style.name if p.style else None,
                            raw_text=p.text,
                            runs=runs,
                            hyperlinks=hyperlinks,
                        )
                    )
                    global_para_order += 1

                for t in doc.tables:
                    matrix = [[cell.text for cell in row.cells] for row in t.rows]
                    c_section.tables.append(
                        CanonicalTable(
                            table_order=global_table_order,
                            num_rows=len(t.rows),
                            num_cols=len(t.columns) if t.rows else 0,
                            cells_matrix=matrix,
                        )
                    )
                    global_table_order += 1

            sections.append(c_section)

        return CanonicalDocumentModel(sections=sections)

    @staticmethod
    def apply_edit(
        content: bytes,
        paragraph_order: int,
        target_text: str,
        replacement_text: str,
        occurrence: int = 1,
    ) -> bytes:
        """Applies exact text replacement in a target paragraph using RunSpanMatcher."""
        doc = docx.Document(io.BytesIO(content))
        if paragraph_order < 1 or paragraph_order > len(doc.paragraphs):
            raise DocxFormatError(
                f"Paragraph order {paragraph_order} out of bounds (1..{len(doc.paragraphs)}).",
                status_code=404,
            )

        target_para = doc.paragraphs[paragraph_order - 1]
        RunSpanMatcher.replace_in_paragraph(target_para, target_text, replacement_text, occurrence)

        out = io.BytesIO()
        doc.save(out)
        return out.getvalue()
