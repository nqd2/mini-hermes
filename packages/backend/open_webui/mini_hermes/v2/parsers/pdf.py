"""PDF High-Fidelity Parser and Strict Read-Only Mutation Invariant."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from fastapi import HTTPException
from pypdf import PdfReader


class PageLayerType(StrEnum):
    TEXT_LAYER = "TEXT_LAYER"
    IMAGE_ONLY = "IMAGE_ONLY"
    BLANK = "BLANK"


@dataclass
class CanonicalPdfPage:
    page_number: int  # 1-indexed
    raw_text: str
    layer_type: PageLayerType
    num_images: int
    width_pt: float
    height_pt: float
    rotation: int = 0


@dataclass
class CanonicalPdfModel:
    num_pages: int
    pages: list[CanonicalPdfPage] = field(default_factory=list)

    @property
    def has_scanned_pages(self) -> bool:
        return any(p.layer_type == PageLayerType.IMAGE_ONLY for p in self.pages)

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.raw_text for p in self.pages if p.raw_text)


class FormatEditUnsupportedError(HTTPException):
    """Strictly raised when any edit/mutation operation is attempted on a PDF."""

    def __init__(
        self,
        message: str = "Mutations on PDF documents are strictly unsupported. Format is read-only.",
    ):
        self.code = "format_edit_unsupported"
        self.message = message
        super().__init__(
            status_code=422,
            detail={
                "code": "format_edit_unsupported",
                "message": message,
                "format": "pdf",
                "supported_operations": [],
            },
        )


class PdfParser:
    """PDF parser with text layer classification and unconditional mutation refusal."""

    @staticmethod
    def parse(content: bytes) -> CanonicalPdfModel:
        reader = PdfReader(io.BytesIO(content))
        pages: list[CanonicalPdfPage] = []

        for p_idx, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            try:
                num_images = len(page.images)
            except Exception:
                num_images = 0

            if len(text) > 0:
                layer_type = PageLayerType.TEXT_LAYER
            elif num_images > 0:
                layer_type = PageLayerType.IMAGE_ONLY
            else:
                layer_type = PageLayerType.BLANK

            box = page.mediabox
            width = float(box.width) if box else 0.0
            height = float(box.height) if box else 0.0

            pages.append(
                CanonicalPdfPage(
                    page_number=p_idx,
                    raw_text=text,
                    layer_type=layer_type,
                    num_images=num_images,
                    width_pt=width,
                    height_pt=height,
                    rotation=page.rotation or 0,
                )
            )

        return CanonicalPdfModel(num_pages=len(pages), pages=pages)

    @staticmethod
    def propose_edit(*args: Any, **kwargs: Any) -> None:
        """Unconditionally raises HTTP 422 format_edit_unsupported."""
        raise FormatEditUnsupportedError()

    @staticmethod
    def apply_mutation(*args: Any, **kwargs: Any) -> None:
        """Unconditionally raises HTTP 422 format_edit_unsupported."""
        raise FormatEditUnsupportedError()

    @staticmethod
    def apply_edit(*args: Any, **kwargs: Any) -> None:
        """Unconditionally raises HTTP 422 format_edit_unsupported."""
        raise FormatEditUnsupportedError()
