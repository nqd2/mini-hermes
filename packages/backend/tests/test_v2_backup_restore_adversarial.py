"""Adversarial stress and edge-case tests for Backup, Restore, and 8-Point Parity Verification Engine."""

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.mini_hermes.v2.backup import dump_v2_database, restore_v2_database
from open_webui.mini_hermes.v2.bootstrap import BootstrapConfig, bootstrap_v2_database
from open_webui.mini_hermes.v2.models import (
    MhChat,
    MhChatMessage,
    MhDocument,
    MhDocumentRevision,
    MhIdempotencyRecord,
    MhObjectRegistry,
    MhOutbox,
    MhUser,
    utcnow,
)
from open_webui.mini_hermes.v2.verify_backup import verify_database_parity


@pytest.fixture(scope="module")
def event_loop_policy():
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


@pytest_asyncio.fixture
async def dual_pg_schemas():
    """Fixture providing two disposable schemas: source and target."""
    db_url = os.getenv("MH_TEST_POSTGRES_URL")
    if not db_url:
        pytest.skip("Set MH_TEST_POSTGRES_URL to run PostgreSQL integration tests")

    url = make_url(db_url)
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        pytest.fail("MH_TEST_POSTGRES_URL must use PostgreSQL")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")

    src_schema = f"mh_test_adv_src_{uuid4().hex}"
    tgt_schema = f"mh_test_adv_tgt_{uuid4().hex}"

    admin_engine = create_async_engine(url, pool_pre_ping=True)
    src_url = url.set(query={**dict(url.query), "options": f"-csearch_path={src_schema}"})
    tgt_url = url.set(query={**dict(url.query), "options": f"-csearch_path={tgt_schema}"})

    try:
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{src_schema}"'))
            await conn.execute(text(f'CREATE SCHEMA "{tgt_schema}"'))

        yield (
            src_url.render_as_string(hide_password=False),
            tgt_url.render_as_string(hide_password=False),
            src_schema,
            tgt_schema,
        )
    finally:
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{src_schema}" CASCADE'))
            await conn.execute(text(f'DROP SCHEMA "{tgt_schema}" CASCADE'))
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_adversarial_backup_restore_extreme_data_parity(dual_pg_schemas, tmp_path):
    """Stress test backup and restore parity with extreme and adversarial data:

    1. Deeply nested JSONB (5+ levels of nesting, empty objects/arrays, mixed data types, nulls).
    2. Non-ASCII, Unicode strings (Vietnamese diacritics, CJK, Arabic, emojis, special characters).
    3. Null values across multiple nullable columns.
    4. Extremely large text (30,000+ characters of content).
    5. BigInteger boundary values (near 64-bit maximum).
    6. Exact microsecond timezone-aware UTC timestamps.
    7. 100% match on all 8 parity checks in verify_database_parity().
    """
    src_url, tgt_url, src_schema, tgt_schema = dual_pg_schemas

    # 1. Bootstrap source schema
    config = BootstrapConfig(
        database_url=src_url,
        environment_id="development",
        schema_version="v2.0",
        admin_email="adv_admin@local.test",
        admin_password="AdminSecurePassword123!",
    )
    await bootstrap_v2_database(config)

    src_engine = create_async_engine(src_url)
    session_factory = async_sessionmaker(src_engine, expire_on_commit=False)

    large_text = "Dữ liệu tiếng Việt có dấu: " + ("Tôi yêu Mini-Hermes V2! 🚀 " * 2000)
    assert len(large_text) > 50000

    extreme_json = {
        "level_1": {
            "level_2": {
                "level_3": {
                    "level_4": {
                        "level_5": [
                            "sâu",
                            123456.789,
                            True,
                            False,
                            None,
                            {"khóa_tiếng_việt": "giá trị có dấu hỏi, ngã, nặng, sắc, huyền: ả ã ạ á à"},
                            {"emoji_key_🔑": "✨🔥🎉"},
                        ]
                    }
                }
            }
        },
        "empty_dict": {},
        "empty_list": [],
        "null_field": None,
        "special_chars": "Line1\nLine2\tTab\r\"Quotes\" and 'Single' \\Backslash /Slash <xml> & 'sql; --",
        "numeric_array": [0, -1, 1000000, 3.141592653589793],
    }

    user_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    rev_id = uuid.uuid4()
    chat_id = uuid.uuid4()
    obj_sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    try:
        async with session_factory() as session:
            async with session.begin():
                # 1. Extreme User
                user = MhUser(
                    id=user_id,
                    email="nguyen.van.kiemthu@example.com.vn",
                    name="Nguyễn Văn Kiểm Thử (Đặc nhiệm Persist 🚀)",
                    role="auditor",
                    profile_image_url=None,  # Null test
                    is_active=True,
                    session_stamp=uuid4().hex,
                    settings_json=extreme_json,
                    password_hash="$2b$12$eXtrEmEHaShFoRTeStInG1234567890",
                    created_at=datetime(2026, 11, 13, 14, 30, 45, 123456, tzinfo=timezone.utc),
                )
                session.add(user)
                await session.flush()

                # 2. Object Registry
                obj_reg = MhObjectRegistry(
                    checksum_sha256=obj_sha256,
                    canonical_object_key="v2/objects/extreme_test_object.dat",
                    byte_size=9_223_372_036_854_775_000,  # Near max 64-bit BigInteger
                    storage_tier="HOT",
                    ref_count=1,
                    gc_status="ACTIVE",
                    gc_generation=1,
                )
                session.add(obj_reg)
                await session.flush()

                # 3. Extreme Document
                doc = MhDocument(
                    id=doc_id,
                    tenant_id="tenant-vn-2026",
                    name="Báo cáo kiểm thử bảo mật & phục hồi dữ liệu 2026 🇻🇳 — 測試",
                    owner_id=str(user_id),
                    folder_id=None,  # Null test
                    mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    format_type="XLSX",
                    current_revision_id=None,
                    current_revision_no=1,
                    is_archived=False,
                    created_at=utcnow(),
                )
                session.add(doc)
                await session.flush()

                # 4. Extreme Revision
                rev = MhDocumentRevision(
                    id=rev_id,
                    document_id=doc_id,
                    revision_no=1,
                    object_key="v2/objects/extreme_test_object.dat",
                    checksum_sha256=obj_sha256,
                    byte_size=9_223_372_036_854_775_000,
                    source_metadata=extreme_json,
                    commit_status="COMMITTED",
                    committed_by=str(user_id),
                    committed_at=utcnow(),
                )
                session.add(rev)

                # 5. Extreme Chat & Chat Message
                chat = MhChat(
                    id=chat_id,
                    user_id=user_id,
                    folder_id=None,
                    title="Cuộc trò chuyện thử nghiệm khôi phục dữ liệu 📑 🔥",
                    meta_json=extreme_json,
                    is_archived=False,
                    pinned=True,
                    created_at=utcnow(),
                )
                session.add(chat)
                await session.flush()

                msg = MhChatMessage(
                    id=uuid.uuid4(),
                    chat_id=chat_id,
                    parent_id=None,
                    turn_id="turn_001_adv",
                    role="user",
                    content=large_text,
                    model_id="mini-hermes-v2",
                    tool_calls_json=[extreme_json],
                    citations_json=[{"source": "doc-1", "locator": "A1:B10", "unicode": "trích dẫn tiếng Việt"}],
                    annotations_json=extreme_json,
                    created_at=utcnow(),
                )
                session.add(msg)

                # 6. Extreme Outbox Event
                outbox_ev = MhOutbox(
                    id=uuid.uuid4(),
                    event_key=f"outbox_adv_{uuid4().hex}",
                    event_type="document.adversarial_test",
                    aggregate_type="document",
                    aggregate_id=str(doc_id),
                    payload_json=extreme_json,
                    delivery_attempts=3,
                    max_attempts=10,
                    lease_worker_id="worker-adv-01",
                    lease_generation=5,
                    lease_expires_at=utcnow(),
                    published_at=None,
                    last_error="Lỗi có chủ đích phục vụ kiểm thử tiếng Việt ⚠️",
                    created_at=utcnow(),
                )
                session.add(outbox_ev)

                # 7. Idempotency Record
                idem = MhIdempotencyRecord(
                    id=uuid.uuid4(),
                    principal_id=str(user_id),
                    operation_kind="commit_preview",
                    idempotency_key=f"idem_{uuid4().hex}",
                    request_fingerprint="abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
                    response_status_code=200,
                    cached_response_json=extreme_json,
                    expires_at=utcnow(),
                    created_at=utcnow(),
                )
                session.add(idem)

        # 2. Dump source database
        dump_file = tmp_path / "adversarial_backup.dump"
        dump_res = await dump_v2_database(source_url=src_url, output_path=dump_file)
        assert dump_res["success"] is True
        assert dump_file.exists()

        # 3. Restore dump into target schema
        restore_res = await restore_v2_database(target_url=tgt_url, dump_path=dump_file, force=True)
        assert restore_res["status"] == "restored"

        # 4. Run 8-Point Parity Verification Engine
        parity = await verify_database_parity(source_url=src_url, target_url=tgt_url)

        # 5. Assert 100% parity across all dimensions
        assert parity.passed is True, f"Parity discrepancies found: {parity.discrepancies}"
        assert len(parity.discrepancies) == 0
        assert parity.tables_verified == 49
        assert parity.total_rows_source == parity.total_rows_target
        assert parity.total_rows_source > 10  # Baseline + all adversarial entities

        for check_name, status in parity.check_details.items():
            assert status is True, f"Parity check '{check_name}' failed under extreme data!"

    finally:
        await src_engine.dispose()
