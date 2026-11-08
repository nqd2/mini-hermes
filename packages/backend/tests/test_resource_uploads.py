from types import SimpleNamespace

import pytest


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeDB:
    def __init__(self, existing=None):
        self.existing = existing

    async def execute(self, _statement):
        return FakeResult(self.existing)


@pytest.mark.asyncio
async def test_register_chat_upload_adopts_file_and_preserves_source_identity(tmp_path, monkeypatch):
    from open_webui.mini_hermes import resource_uploads
    from open_webui.models import files as file_models
    from open_webui.storage import provider

    source = tmp_path / 'uploaded.docx'
    source.write_bytes(b'office-document')
    uploaded = SimpleNamespace(meta={'name': 'M.docx'}, filename='uploaded.docx', path='source-key')
    folder = SimpleNamespace(id='personal-folder')
    document = SimpleNamespace(id='resource-1', source_file_id='file-1')
    created = {}

    async def get_uploaded(file_id, user_id, db=None):
        assert file_id == 'file-1'
        assert user_id == 'alice'
        return uploaded

    async def ensure_folder(db, user_id):
        assert user_id == 'alice'
        return folder

    async def create_document(db, store, user_id, filename, content, **kwargs):
        created.update(
            db=db,
            store=store,
            user_id=user_id,
            filename=filename,
            content=content,
            kwargs=kwargs,
        )
        return document

    monkeypatch.setattr(file_models.Files, 'get_file_by_id_and_user_id', get_uploaded)
    monkeypatch.setattr(provider.Storage, 'get_file', lambda _path: str(source))
    monkeypatch.setattr(resource_uploads, 'ensure_personal_folder', ensure_folder)
    monkeypatch.setattr(resource_uploads, 'create_document', create_document)

    store = object()
    db = FakeDB()
    result = await resource_uploads.register_chat_upload(db, 'alice', 'file-1', store)

    assert result is document
    assert created == {
        'db': db,
        'store': store,
        'user_id': 'alice',
        'filename': 'M.docx',
        'content': b'office-document',
        'kwargs': {'folder_id': 'personal-folder', 'source_file_id': 'file-1'},
    }


@pytest.mark.asyncio
async def test_register_chat_upload_is_idempotent_and_does_not_cross_owners():
    from open_webui.mini_hermes.resource_uploads import register_chat_upload

    owned = SimpleNamespace(owner_id='alice')
    foreign = SimpleNamespace(owner_id='bob')

    assert await register_chat_upload(FakeDB(owned), 'alice', 'file-1', object()) is owned
    assert await register_chat_upload(FakeDB(foreign), 'alice', 'file-1', object()) is None
