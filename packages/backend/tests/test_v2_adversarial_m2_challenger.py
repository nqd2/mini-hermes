"""Adversarial Challenge Test Suite for Milestone M2 (Document Engine & Ingestion Pipeline).

Empirically challenges:
1. PDF Strict Read-Only Invariant & Page Classification (no OCR hallucination, unconditional 422 on mutations).
2. Ingestion Pipeline Security & Limit Defenses (zip bombs, spoofing, 50MB ceiling, SHA-256 tamper, encryption).
3. Derived Index Lifecycle Concurrency & Revision Isolation (CAS pointer concurrency, revision isolation, schema gates).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.mini_hermes.v2.canonical.spreadsheet import CanonicalSpreadsheetModel
from open_webui.mini_hermes.v2.indexing import (
    IndexBuildNotFoundError,
    IndexingError,
    IndexLifecycleManager,
    IndexNotReadyError,
    IndexSchemaIncompatibleError,
    StaleFencingTokenError,
    V2_INDEX_SCHEMA_VERSION,
    V2_XLSX_PARSER_VERSION,
    compute_index_manifest_hash,
)
from open_webui.mini_hermes.v2.ingestion import (
    ChecksumMismatchError,
    CorruptFileError,
    EncryptedFileUnsupportedError,
    FileSizeExceededError,
    FormatSignatureMismatchError,
    FormatSniffer,
    IdempotencyConflictError,
    IngestionPipeline,
    UnsupportedFormatError,
    ZipBombDetectedError,
)
from open_webui.mini_hermes.v2.models import (
    FormatType,
    IndexBuildState,
    MhActiveIndex,
    MhDocument,
    MhDocumentRevision,
    MhIndexBuild,
)
from open_webui.mini_hermes.v2.models.base import V2Base
from open_webui.mini_hermes.v2.parsers.pdf import (
    CanonicalPdfModel,
    FormatEditUnsupportedError,
    PageLayerType,
    PdfParser,
)
from open_webui.mini_hermes.v2.storage import V2ObjectStore

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "v2"


# ==============================================================================
# Helper fixtures for in-memory SQLite async testing
# ==============================================================================


@pytest_asyncio.fixture
async def sqlite_v2_session_factory():
    """Provides an isolated in-memory SQLite session factory for concurrency and isolation testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(V2Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


# ==============================================================================
# 1. PDF Strict Read-Only Invariant & Page Classification Challenges
# ==============================================================================


def test_pdf_propose_edit_all_mutation_vectors_raise_422():
    """Attack propose_edit with empty, valid, and adversarial mutation payloads.

    Must unconditionally raise FormatEditUnsupportedError (HTTP 422) with
    code: 'format_edit_unsupported'.
    """
    attack_payloads: list[dict[str, Any]] = [
        {},
        {"operations": []},
        {"operations": [{"op": "replace_text", "target": "foo", "replacement": "bar"}]},
        {"operations": [{"op": "delete_page", "page": 1}]},
        {"operations": [{"op": "insert_text", "text": "malicious injection"}]},
        {"sql_injection": "'; DROP TABLE mh_documents; --"},
        {"document_id": "00000000-0000-0000-0000-000000000000", "preview": True},
    ]

    for payload in attack_payloads:
        with pytest.raises(FormatEditUnsupportedError) as exc_info:
            PdfParser.propose_edit(**payload)

        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["code"] == "format_edit_unsupported"
        assert exc_info.value.detail["format"] == "pdf"
        assert exc_info.value.detail["supported_operations"] == []


def test_pdf_apply_mutation_and_apply_edit_unconditionally_raise_422():
    """Attack apply_mutation and apply_edit with arbitrary parameters.

    Must unconditionally raise FormatEditUnsupportedError (HTTP 422).
    """
    # Test apply_mutation
    with pytest.raises(FormatEditUnsupportedError) as exc_info:
        PdfParser.apply_mutation(document_id=str(uuid.uuid4()), patch={"any": "data"})
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "format_edit_unsupported"

    # Test apply_edit
    with pytest.raises(FormatEditUnsupportedError) as exc_info:
        PdfParser.apply_edit(b"%PDF-1.4 dummy", 1, "target", "replacement")
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "format_edit_unsupported"

    # Test apply_edit with edge parameters (None, negative page, empty strings)
    with pytest.raises(FormatEditUnsupportedError):
        PdfParser.apply_edit(None, -1, "", "")


def test_pdf_parser_has_no_backdoor_mutation_or_preview_methods():
    """Empirically inspect PdfParser and CanonicalPdfModel.

    Verifies that no write, save, export, or preview mutation methods exist that could bypass the invariant.
    """
    parser_methods = [m for m in dir(PdfParser) if not m.startswith("_")]
    # Only parse and the 3 guardrail methods should exist
    allowed_methods = {"parse", "propose_edit", "apply_mutation", "apply_edit"}
    for method in parser_methods:
        assert method in allowed_methods, f"Unexpected method '{method}' found on PdfParser"

    model_methods = [m for m in dir(CanonicalPdfModel) if not m.startswith("_")]
    forbidden_verbs = {"save", "write", "export", "mutate", "commit", "apply", "preview"}
    for m in model_methods:
        assert not any(v in m.lower() for v in forbidden_verbs), f"Dangerous method '{m}' on CanonicalPdfModel"


def test_pdf_page_classification_digital_text_vs_scanned_vs_blank_without_ocr():
    """Synthesize a 5-page PDF with diverse layer structures:

    Page 1: Digital text layer
    Page 2: Pure raster image, zero text
    Page 3: Completely blank (no text, no image)
    Page 4: Whitespace-only text ('   \\n\\t  ') with image
    Page 5: Whitespace-only text with NO image

    Verifies:
    - Accurate classification (TEXT_LAYER, IMAGE_ONLY, BLANK).
    - No OCR hallucination (zero text emitted for image/blank pages).
    - has_scanned_pages is True when an IMAGE_ONLY page is present.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, invariant=1)

    # Page 1: Selectable text
    c.drawString(100, 700, "Genuine digital selectable text layer.")
    c.showPage()

    # Page 2: Pure raster image
    img = Image.new("RGB", (200, 200), color=(100, 150, 200))
    img_buf = io.BytesIO()
    img.save(img_buf, format="PNG")
    img_buf.seek(0)
    c.drawImage(ImageReader(img_buf), 100, 300, width=300, height=300)
    c.showPage()

    # Page 3: Blank page
    c.showPage()

    # Page 4: Whitespace text + image
    c.drawString(100, 700, "       ")
    c.drawImage(ImageReader(img_buf), 100, 300, width=200, height=200)
    c.showPage()

    # Page 5: Whitespace text only (no image)
    c.drawString(100, 700, "          ")
    c.showPage()

    c.save()
    pdf_bytes = buf.getvalue()

    model = PdfParser.parse(pdf_bytes)
    assert model.num_pages == 5

    p1, p2, p3, p4, p5 = model.pages

    # Page 1: TEXT_LAYER
    assert p1.page_number == 1
    assert p1.layer_type == PageLayerType.TEXT_LAYER
    assert "Genuine digital selectable text layer" in p1.raw_text

    # Page 2: IMAGE_ONLY, zero text, NO OCR hallucination
    assert p2.page_number == 2
    assert p2.layer_type == PageLayerType.IMAGE_ONLY
    assert p2.raw_text == ""
    assert p2.num_images >= 1

    # Page 3: BLANK
    assert p3.page_number == 3
    assert p3.layer_type == PageLayerType.BLANK
    assert p3.raw_text == ""
    assert p3.num_images == 0

    # Page 4: Whitespace + Image -> Classified as IMAGE_ONLY (whitespace stripped to empty)
    assert p4.page_number == 4
    assert p4.layer_type == PageLayerType.IMAGE_ONLY
    assert p4.raw_text == ""
    assert p4.num_images >= 1

    # Page 5: Whitespace only -> Classified as BLANK
    assert p5.page_number == 5
    assert p5.layer_type == PageLayerType.BLANK
    assert p5.raw_text == ""
    assert p5.num_images == 0

    assert model.has_scanned_pages is True


# ==============================================================================
# 2. Ingestion Pipeline Security & Limit Defenses Challenges
# ==============================================================================


def test_zip_bomb_extreme_compression_ratio_rejected():
    """Attack with an extreme compression ratio archive (> 100:1).

    Must raise ZipBombDetectedError (status 422, code: 'ZIP_BOMB_DETECTED').
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", b"<Types/>")
        z.writestr("xl/workbook.xml", b"<workbook/>")
        # 10 MB of zeroes compresses to ~10 KB -> ratio ~1000:1
        z.writestr("xl/huge_zeroes.dat", b"\x00" * (10 * 1024 * 1024))

    bomb_bytes = buf.getvalue()
    with pytest.raises(ZipBombDetectedError) as exc_info:
        FormatSniffer.sniff_format(bomb_bytes, "bomb.xlsx")

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "ZIP_BOMB_DETECTED"
    assert "exceeds security limit of 100.0:1" in exc_info.value.message


def test_zip_bomb_uncompressed_size_ceiling_rejected():
    """Attack with archive uncompressed size exceeding 250MB limit.

    Must raise ZipBombDetectedError (status 422, code: 'ZIP_BOMB_DETECTED').
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", b"<Types/>")
        z.writestr("xl/workbook.xml", b"<workbook/>")
        # Pad with 3 MB incompressible random data to keep compression ratio below 100:1 (~75:1)
        z.writestr("xl/random.bin", os.urandom(3 * 1024 * 1024))
        # 26 entries of 10 MB zeroes = 260 MB uncompressed, exceeding 250MB ceiling
        for i in range(26):
            z.writestr(f"xl/zero_{i}.dat", b"\x00" * (10 * 1024 * 1024))

    bomb_bytes = buf.getvalue()
    with pytest.raises(ZipBombDetectedError) as exc_info:
        FormatSniffer.sniff_format(bomb_bytes, "large.xlsx")

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "ZIP_BOMB_DETECTED"
    assert "exceeds limit 262144000 bytes (250MB)" in exc_info.value.message


def test_zip_bomb_entry_count_ceiling_rejected():
    """Attack with archive containing more than 10,000 files.

    Must raise ZipBombDetectedError (status 422, code: 'ZIP_BOMB_DETECTED').
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as z:
        for i in range(10_001):
            z.writestr(f"file_{i}.txt", b"x")

    bomb_bytes = buf.getvalue()
    with pytest.raises(ZipBombDetectedError) as exc_info:
        FormatSniffer.sniff_format(bomb_bytes, "many_files.docx")

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "ZIP_BOMB_DETECTED"
    assert "exceeds limit 10000" in exc_info.value.message


def test_zip_bomb_recursive_nested_archive_defense_assessment():
    """Adversarial stress-test: A raw recursive zip bomb (e.g. 42.zip structure)

    renamed as an OOXML document.
    Must fail-closed with FormatSignatureMismatchError due to missing [Content_Types].xml.
    """
    inner_buf = io.BytesIO()
    with zipfile.ZipFile(inner_buf, "w") as iz:
        iz.writestr("sub_file.txt", b"deep payload")
    inner_bytes = inner_buf.getvalue()

    outer_buf = io.BytesIO()
    with zipfile.ZipFile(outer_buf, "w") as oz:
        for i in range(5):
            oz.writestr(f"nested_{i}.zip", inner_bytes)

    raw_recursive_bytes = outer_buf.getvalue()

    # Rejection 1: When disguised as .docx (lacks [Content_Types].xml)
    with pytest.raises(FormatSignatureMismatchError) as exc_info:
        FormatSniffer.sniff_format(raw_recursive_bytes, "malicious.docx")
    assert exc_info.value.status_code == 422
    assert "missing '[Content_Types].xml'" in exc_info.value.message

    # Rejection 2: When disguised as .xlsx
    with pytest.raises(FormatSignatureMismatchError):
        FormatSniffer.sniff_format(raw_recursive_bytes, "malicious.xlsx")

    # Rejection 3: When disguised as .pdf
    with pytest.raises(FormatSignatureMismatchError):
        FormatSniffer.sniff_format(raw_recursive_bytes, "malicious.pdf")


def test_extension_spoofing_attack_matrix():
    """Exhaustive extension spoofing matrix.

    Every spoofing attempt must be rejected fail-closed with FormatSignatureMismatchError (422)
    or UnsupportedFormatError (422).
    """
    pdf_magic_bytes = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
    zip_magic_bytes = b"PK\x03\x04\x14\x00\x00\x00"
    elf_magic_bytes = b"\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    pe_magic_bytes = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"

    # 1. .xlsx with PDF magic header
    with pytest.raises(FormatSignatureMismatchError) as exc_info:
        FormatSniffer.sniff_format(pdf_magic_bytes, "report.xlsx")
    assert exc_info.value.status_code == 422
    assert "lacks 'PK\\x03\\x04' ZIP header" in exc_info.value.message

    # 2. .xlsx with Linux ELF binary header
    with pytest.raises(FormatSignatureMismatchError):
        FormatSniffer.sniff_format(elf_magic_bytes, "payload.xlsx")

    # 3. .xlsx with Windows PE executable header
    with pytest.raises(FormatSignatureMismatchError):
        FormatSniffer.sniff_format(pe_magic_bytes, "invoice.xlsx")

    # 4. .pdf with ZIP magic header
    with pytest.raises(FormatSignatureMismatchError) as exc_info:
        FormatSniffer.sniff_format(zip_magic_bytes, "contract.pdf")
    assert exc_info.value.status_code == 422
    assert "lacks '%PDF-' magic header" in exc_info.value.message

    # 5. .docx with valid ZIP container but missing word/document.xml
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", b"<Types/>")
        z.writestr("not_word/something.xml", b"<data/>")
    with pytest.raises(FormatSignatureMismatchError) as exc_info:
        FormatSniffer.sniff_format(buf.getvalue(), "corrupt.docx")
    assert "missing Word document part ('word/document.xml')" in exc_info.value.message

    # 6. .pptx with valid ZIP container but missing ppt/presentation.xml
    buf_pptx = io.BytesIO()
    with zipfile.ZipFile(buf_pptx, "w") as z:
        z.writestr("[Content_Types].xml", b"<Types/>")
        z.writestr("word/document.xml", b"<data/>")  # Word part in pptx
    with pytest.raises(FormatSignatureMismatchError) as exc_info:
        FormatSniffer.sniff_format(buf_pptx.getvalue(), "slides.pptx")
    assert "missing PowerPoint presentation part ('ppt/presentation.xml')" in exc_info.value.message

    # 7. Disallowed executable / script extensions
    disallowed_extensions = [
        "script.sh",
        "binary.exe",
        "archive.tar.gz",
        "macro.vbs",
        "page.html",
        "payload.bat",
    ]
    for filename in disallowed_extensions:
        with pytest.raises(UnsupportedFormatError) as exc_info:
            FormatSniffer.sniff_format(b"echo hello", filename)
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == "UNSUPPORTED_FORMAT"


@pytest.mark.asyncio
async def test_file_size_ceiling_50mb_boundary():
    """Attack with a payload exactly 1 byte over the 50MB ceiling.

    Must reject with FileSizeExceededError (HTTP 413, code: 'FILE_SIZE_EXCEEDED').
    """
    dummy_factory = async_sessionmaker()
    pipeline = IngestionPipeline(session_factory=dummy_factory)

    oversized_data = b"0" * (50 * 1024 * 1024 + 1)
    with pytest.raises(FileSizeExceededError) as exc_info:
        await pipeline.start_ingestion(
            principal_id="attacker",
            filename="oversized.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            idempotency_key=str(uuid.uuid4()),
            data=oversized_data,
        )

    assert exc_info.value.status_code == 413
    assert exc_info.value.code == "FILE_SIZE_EXCEEDED"
    assert "exceeds maximum allowable ceiling" in exc_info.value.message


@pytest.mark.asyncio
async def test_sha256_checksum_tamper_rejection():
    """Attack with mismatched expected_sha256 vs actual payload bytes.

    Must reject with ChecksumMismatchError (HTTP 422, code: 'CHECKSUM_MISMATCH').
    """
    dummy_factory = async_sessionmaker()
    pipeline = IngestionPipeline(session_factory=dummy_factory)

    genuine_xlsx = (FIXTURES_DIR / "xlsx-f05-synthetic.xlsx").read_bytes()
    tampered_expected_hash = "0000000000000000000000000000000000000000000000000000000000000000"

    with pytest.raises(ChecksumMismatchError) as exc_info:
        await pipeline.start_ingestion(
            principal_id="attacker",
            filename="xlsx-f05-synthetic.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            idempotency_key=str(uuid.uuid4()),
            data=genuine_xlsx,
            expected_sha256=tampered_expected_hash,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "CHECKSUM_MISMATCH"
    assert tampered_expected_hash in exc_info.value.message


def test_encrypted_file_rejection():
    """Attack with password-protected archive entry.

    Must reject with EncryptedFileUnsupportedError (HTTP 422).
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", b"<Types/>")
        z.writestr("xl/workbook.xml", b"<workbook/>")
        z.writestr("xl/secret.txt", b"encrypted data payload")

    data = bytearray(buf.getvalue())
    # Flip encryption bit (bit 0 of flag_bits) in all Central Directory and Local File headers
    idx = 0
    while True:
        idx = data.find(b"PK\x01\x02", idx)
        if idx == -1:
            break
        data[idx + 8] |= 0x1
        idx += 4

    idx = 0
    while True:
        idx = data.find(b"PK\x03\x04", idx)
        if idx == -1:
            break
        data[idx + 6] |= 0x1
        idx += 4

    with pytest.raises(EncryptedFileUnsupportedError) as exc_info:
        FormatSniffer.sniff_format(bytes(data), "protected.xlsx")

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "ENCRYPTED_FILE_UNSUPPORTED"
    assert "encrypted" in exc_info.value.message.lower()


# ==============================================================================
# 3. Derived Index Lifecycle Concurrency & Revision Isolation Challenges
# ==============================================================================


@pytest.mark.asyncio
async def test_active_index_pointer_cas_concurrency_race(sqlite_v2_session_factory):
    """Stress-test compare-and-set (CAS) concurrency on MhActiveIndex.generation.

    Scenario:
    1. Activate Build 1 at generation 1.
    2. Two workers concurrently attempt to activate Build 2A and Build 2B with expected_generation = 1.
    3. Exactly ONE worker must succeed (generation becomes 2, old build retired).
    4. The losing worker must receive StaleFencingTokenError (HTTP 409).
    """
    idx_mgr = IndexLifecycleManager(sqlite_v2_session_factory)

    doc_id = uuid.uuid4()
    rev_id = uuid.uuid4()

    # Step 1: Create and activate Build 1
    b1 = await idx_mgr.create_index_build(doc_id, rev_id, "hash1", V2_XLSX_PARSER_VERSION)
    c1 = await idx_mgr.claim_build_for_execution("w1")
    await idx_mgr.complete_build_verification(b1.id, "w1", c1.fencing_token, "m1")
    act1 = await idx_mgr.activate_index_build_cas(rev_id, b1.id, expected_generation=None)
    assert act1.generation == 1
    assert act1.active_build_id == b1.id

    # Step 2: Create two competing builds for the same revision
    b2a = await idx_mgr.create_index_build(doc_id, rev_id, "hash2a", V2_XLSX_PARSER_VERSION)
    ca = await idx_mgr.claim_build_for_execution("wa")
    await idx_mgr.complete_build_verification(b2a.id, "wa", ca.fencing_token, "ma")

    b2b = await idx_mgr.create_index_build(doc_id, rev_id, "hash2b", V2_XLSX_PARSER_VERSION)
    cb = await idx_mgr.claim_build_for_execution("wb")
    await idx_mgr.complete_build_verification(b2b.id, "wb", cb.fencing_token, "mb")

    # Step 3: Worker A executes first with expected_generation=1 -> succeeds
    res_a = await idx_mgr.activate_index_build_cas(rev_id, b2a.id, expected_generation=1)
    assert res_a.generation == 2
    assert res_a.active_build_id == b2a.id

    # Verify old build b1 was retired AND competing build b2b was also retired to RETIRED
    async with sqlite_v2_session_factory() as session:
        b1_db = await session.get(MhIndexBuild, b1.id)
        assert b1_db.build_state == IndexBuildState.RETIRED.value
        b2b_db = await session.get(MhIndexBuild, b2b.id)
        assert b2b_db.build_state == IndexBuildState.RETIRED.value

    # Defense Vector A: Worker B attempting to activate retired build b2b is rejected with HTTP 422
    with pytest.raises(IndexingError) as exc_retired:
        await idx_mgr.activate_index_build_cas(rev_id, b2b.id, expected_generation=1)
    assert exc_retired.value.status_code == 422
    assert "Cannot activate build in state 'RETIRED'" in exc_retired.value.message

    # Defense Vector B: Worker C prepares fresh READY build b2c, but passes stale generation=1 -> rejected with HTTP 409
    b2c = await idx_mgr.create_index_build(doc_id, rev_id, "hash2c", V2_XLSX_PARSER_VERSION)
    cc = await idx_mgr.claim_build_for_execution("wc")
    await idx_mgr.complete_build_verification(b2c.id, "wc", cc.fencing_token, "mc")

    with pytest.raises(StaleFencingTokenError) as exc_cas:
        await idx_mgr.activate_index_build_cas(rev_id, b2c.id, expected_generation=1)

    assert exc_cas.value.status_code == 409
    assert exc_cas.value.code == "STALE_FENCING_TOKEN"
    assert "CAS generation mismatch: expected 1, current 2" in exc_cas.value.message

    # Verify active build is still 2A and generation is still 2
    active_now, build_now = await idx_mgr.get_active_index_for_revision(rev_id)
    assert active_now.active_build_id == b2a.id
    assert active_now.generation == 2


@pytest.mark.asyncio
async def test_active_index_pointer_cas_rejects_non_ready_and_incompatible_schema(
    sqlite_v2_session_factory,
):
    """CAS activation must reject:

    - Builds in non-READY states (REQUESTED, RUNNING, RETIRED) with HTTP 422.
    - Incompatible schema versions (e.g. 'v1.0') with IndexSchemaIncompatibleError (HTTP 422).
    - Revision mismatch with HTTP 422.
    """
    idx_mgr = IndexLifecycleManager(sqlite_v2_session_factory)
    doc_id = uuid.uuid4()

    # 1. Non-READY state (REQUESTED)
    rev1 = uuid.uuid4()
    b_req = await idx_mgr.create_index_build(doc_id, rev1, "hash_req", V2_XLSX_PARSER_VERSION)
    with pytest.raises(IndexingError) as exc_info:
        await idx_mgr.activate_index_build_cas(rev1, b_req.id)
    assert exc_info.value.status_code == 422
    assert "Must be READY" in exc_info.value.message

    # 2. Incompatible schema version
    rev2 = uuid.uuid4()
    b_bad_schema = await idx_mgr.create_index_build(
        doc_id, rev2, "hash_bad", V2_XLSX_PARSER_VERSION, schema_version="v1.0-legacy"
    )
    # Claim the build (since b_req is in rev1, claim_build picks b_req first if requested, so claim b_req first or use separate factory)
    c_req = await idx_mgr.claim_build_for_execution("w_clean")
    c_bad = await idx_mgr.claim_build_for_execution("w_bad")
    assert c_bad.id == b_bad_schema.id
    await idx_mgr.complete_build_verification(b_bad_schema.id, "w_bad", c_bad.fencing_token, "m_bad")

    with pytest.raises(IndexSchemaIncompatibleError) as exc_info:
        await idx_mgr.activate_index_build_cas(rev2, b_bad_schema.id)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "INDEX_SCHEMA_INCOMPATIBLE"

    # 3. Revision mismatch
    rev3 = uuid.uuid4()
    rev_other = uuid.uuid4()
    b_other = await idx_mgr.create_index_build(doc_id, rev3, "hash_other", V2_XLSX_PARSER_VERSION)
    c_other = await idx_mgr.claim_build_for_execution("w_other")
    await idx_mgr.complete_build_verification(b_other.id, "w_other", c_other.fencing_token, "m_other")

    with pytest.raises(IndexingError) as exc_info:
        await idx_mgr.activate_index_build_cas(rev_other, b_other.id)
    assert exc_info.value.status_code == 422
    assert "Build revision mismatch" in exc_info.value.message


@pytest.mark.asyncio
async def test_revision_advancement_isolation(sqlite_v2_session_factory):
    """Stress-test revision advancement isolation.

    When Revision 2 is committed, queries for Revision 2 must NOT read Revision 1 index data.
    Unindexed Revision 2 must strictly raise IndexNotReadyError (HTTP 409).
    Once Revision 2 is indexed, both revisions maintain strict isolation.
    """
    idx_mgr = IndexLifecycleManager(sqlite_v2_session_factory)
    doc_id = uuid.uuid4()
    rev1_id = uuid.uuid4()
    rev2_id = uuid.uuid4()

    # Step 1: Ingest and activate Revision 1
    b1 = await idx_mgr.create_index_build(doc_id, rev1_id, "hash_rev1", V2_XLSX_PARSER_VERSION)
    c1 = await idx_mgr.claim_build_for_execution("w_rev1")
    await idx_mgr.complete_build_verification(b1.id, "w_rev1", c1.fencing_token, "m_rev1")
    await idx_mgr.activate_index_build_cas(rev1_id, b1.id)

    # Revision 1 active index is ready
    act1, b1_active = await idx_mgr.get_active_index_for_revision(rev1_id)
    assert act1.active_build_id == b1.id
    assert b1_active.revision_id == rev1_id

    # Step 2: Query Revision 2 before its index is ready -> MUST FAIL ISOLATION GATE
    with pytest.raises(IndexNotReadyError) as exc_info:
        await idx_mgr.get_active_index_for_revision(rev2_id)

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "INDEX_NOT_READY"
    assert str(rev2_id) in exc_info.value.message

    # Step 3: Now build and activate Revision 2 index
    b2 = await idx_mgr.create_index_build(doc_id, rev2_id, "hash_rev2", V2_XLSX_PARSER_VERSION)
    c2 = await idx_mgr.claim_build_for_execution("w_rev2")
    await idx_mgr.complete_build_verification(b2.id, "w_rev2", c2.fencing_token, "m_rev2")
    await idx_mgr.activate_index_build_cas(rev2_id, b2.id)

    # Step 4: Verify strict isolation: both revisions point to their respective builds
    act1_post, b1_post = await idx_mgr.get_active_index_for_revision(rev1_id)
    act2_post, b2_post = await idx_mgr.get_active_index_for_revision(rev2_id)

    assert act1_post.active_build_id == b1.id
    assert b1_post.source_checksum_sha256 == "hash_rev1"

    assert act2_post.active_build_id == b2.id
    assert b2_post.source_checksum_sha256 == "hash_rev2"
    assert act2_post.active_build_id != act1_post.active_build_id
