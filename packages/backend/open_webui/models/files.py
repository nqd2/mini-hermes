"""File upload models, forms, and database operations."""

from __future__ import annotations

import logging
import time

# local imports
from open_webui.internal.db import Base, JSONField, get_async_db_context
from open_webui.utils.misc import sanitize_metadata
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import JSON, BigInteger, Column, String, Text, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


class File(Base):  # uploaded file record
    __tablename__ = 'file'
    id = Column(String, primary_key=True, unique=True)
    user_id = Column(String, index=True)  # owner user id
    hash = Column(Text, nullable=True)

    filename = Column(Text)  # original upload filename
    path = Column(Text, nullable=True)

    data = Column(JSON, nullable=True)
    meta = Column(JSON, nullable=True)

    created_at = Column(BigInteger, index=True)  # upload timestamp
    updated_at = Column(BigInteger)


class FileModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    hash: str | None = None

    filename: str
    path: str | None = None

    data: dict | None = None
    meta: dict | None = None

    created_at: int | None  # timestamp in epoch
    updated_at: int | None  # timestamp in epoch


# Metadata structures
class FileMeta(BaseModel):
    name: str | None = None
    content_type: str | None = None
    size: int | None = None

    model_config = ConfigDict(extra='allow')

    @model_validator(mode='before')
    @classmethod
    def sanitize_meta(cls, data):
        """Sanitize metadata fields to handle malformed legacy data."""
        if not isinstance(data, dict):
            return data

        # Handle content_type that may be a list like ['application/pdf', None]
        content_type = data.get('content_type')
        if isinstance(content_type, list):
            # Extract first non-None string value
            data['content_type'] = next((item for item in content_type if isinstance(item, str)), None)
        elif content_type is not None and not isinstance(content_type, str):
            data['content_type'] = None

        return data


class FileModelResponse(BaseModel):
    id: str
    user_id: str
    hash: str | None = None

    filename: str
    data: dict | None = None
    meta: FileMeta | None = None

    created_at: int  # timestamp in epoch
    updated_at: int | None = None  # timestamp in epoch, optional for legacy files

    model_config = ConfigDict(extra='allow')


class FileMetadataResponse(BaseModel):
    id: str
    hash: str | None = None
    meta: dict | None = None
    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch


class FileListResponse(BaseModel):
    items: list[FileModelResponse]
    total: int


class FileForm(BaseModel):
    id: str
    hash: str | None = None
    filename: str
    path: str
    data: dict = {}
    meta: dict = {}


class FileUpdateForm(BaseModel):
    hash: str | None = None
    data: dict | None = None
    meta: dict | None = None


class FilesTable:
    def __init__(self):
        self._external_content_store = None

    def _get_external_content_store(self):
        if self._external_content_store is None:
            from open_webui.agent_runtime.files import AgentFileContentStore

            self._external_content_store = AgentFileContentStore.configured()
        return self._external_content_store

    async def hydrate_content_models(
        self, models: list[FileModel], db: AsyncSession | None = None
    ) -> list[FileModel]:
        if not models:
            return models
        async with get_async_db_context(db) as db:
            return [await self._hydrate_external_content(model, db) for model in models]

    async def _hydrate_external_content(self, model: FileModel, db: AsyncSession) -> FileModel:
        if isinstance(model.data, dict) and isinstance(model.data.get('content'), str):
            return model
        try:
            from open_webui.agent_runtime.models import AgentFileContent

            store = self._get_external_content_store()
            reference = await db.get(AgentFileContent, model.id)
            if reference is not None and reference.owner_id == model.user_id:
                model.data = {
                    **(model.data or {}),
                    'content': await store.read_all(store._reference(reference)),
                }
        except Exception:
            # Keep legacy file APIs available while the additive migration is not installed.
            log.debug('No external extracted content for file %s', model.id, exc_info=True)
        return model

    async def get_file_by_id_without_content(
        self, id: str, db: AsyncSession | None = None
    ) -> FileModel | None:
        """Read file metadata without selecting the potentially large extracted-text JSON column."""
        async with get_async_db_context(db) as db:
            row = (
                await db.execute(
                    select(
                        File.id,
                        File.user_id,
                        File.hash,
                        File.filename,
                        File.path,
                        File.meta,
                        File.created_at,
                        File.updated_at,
                    ).where(File.id == id)
                )
            ).first()
            return FileModel.model_validate({**row._mapping, 'data': {}}) if row else None

    async def insert_new_file(
        self, user_id: str, form_data: FileForm, db: AsyncSession | None = None
    ) -> FileModel | None:
        async with get_async_db_context(db) as db:
            file_data = form_data.model_dump()

            # Sanitize meta to remove non-JSON-serializable objects
            # (e.g. callable tool functions, MCP client instances from middleware)
            if file_data.get('meta'):
                file_data['meta'] = sanitize_metadata(file_data['meta'])

            file = FileModel(
                **{
                    **file_data,
                    'user_id': user_id,
                    'created_at': int(time.time()),
                    'updated_at': int(time.time()),
                }
            )

            try:
                result = File(**file.model_dump())
                db.add(result)
                await db.commit()
                if result:
                    return FileModel.model_validate(result)
                else:
                    return None
            except Exception as e:
                log.exception(f'Error inserting a new file: {e}')
                return None  # insertion failed

    async def get_file_by_id(
        self,
        id: str,
        db: AsyncSession | None = None,
    ) -> FileModel | None:
        """Look up a file by its primary key."""
        try:
            async with get_async_db_context(db) as db:
                file = await db.get(File, id)
                if not file:
                    return None
                return await self._hydrate_external_content(FileModel.model_validate(file), db)
        except Exception:
            return None

    async def get_file_by_id_and_user_id(
        self, id: str, user_id: str, db: AsyncSession | None = None
    ) -> FileModel | None:
        async with get_async_db_context(db) as db:
            try:
                result = await db.execute(select(File).filter_by(id=id, user_id=user_id))
                file = result.scalars().first()
                if file:
                    return await self._hydrate_external_content(FileModel.model_validate(file), db)
                else:
                    return None
            except Exception:
                return None

    async def get_file_metadata_by_id(self, id: str, db: AsyncSession | None = None) -> FileMetadataResponse | None:
        async with get_async_db_context(db) as db:
            try:
                file = await db.get(File, id)
                if not file:
                    return None
                return FileMetadataResponse(
                    id=file.id,
                    hash=file.hash,
                    meta=file.meta,
                    created_at=file.created_at,
                    updated_at=file.updated_at,
                )
            except Exception:
                return None

    async def get_files(self, db: AsyncSession | None = None) -> list[FileModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(File))
            return [
                await self._hydrate_external_content(FileModel.model_validate(file), db)
                for file in result.scalars().all()
            ]

    async def count_files_by_user_id(
        self,
        user_id: str | None = None,
        db: AsyncSession | None = None,
    ) -> int:
        async with get_async_db_context(db) as db:
            stmt = select(func.count(File.id))
            if user_id:
                stmt = stmt.filter_by(user_id=user_id)
            result = await db.execute(stmt)
            return result.scalar() or 0

    async def check_access_by_user_id(self, id, user_id, permission='write', db: AsyncSession | None = None) -> bool:
        file = await self.get_file_by_id(id, db=db)
        if not file:
            return False
        if file.user_id == user_id:
            return True
        # Implement additional access control logic here as needed
        return False

    async def get_files_by_ids(self, ids: list[str], db: AsyncSession | None = None) -> list[FileModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(File).filter(File.id.in_(ids)).order_by(File.updated_at.desc()))
            return [
                await self._hydrate_external_content(FileModel.model_validate(file), db)
                for file in result.scalars().all()
            ]

    async def get_file_metadatas_by_ids(
        self, ids: list[str], db: AsyncSession | None = None
    ) -> list[FileMetadataResponse]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(File.id, File.hash, File.meta, File.created_at, File.updated_at)
                .filter(File.id.in_(ids))
                .order_by(File.updated_at.desc())
            )
            return [
                FileMetadataResponse(
                    id=row.id,
                    hash=row.hash,
                    meta=row.meta,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in result.all()
            ]

    async def get_files_by_user_id(self, user_id: str, db: AsyncSession | None = None) -> list[FileModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(File).filter_by(user_id=user_id))
            return [
                await self._hydrate_external_content(FileModel.model_validate(file), db)
                for file in result.scalars().all()
            ]

    async def get_file_list(
        self,
        user_id: str | None = None,
        skip: int = 0,
        limit: int = 50,
        include_content: bool = True,
        db: AsyncSession | None = None,
    ) -> 'FileListResponse':
        async with get_async_db_context(db) as db:
            stmt = select(File)
            if user_id:
                stmt = stmt.filter_by(user_id=user_id)

            count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
            total = count_result.scalar()

            result = await db.execute(stmt.order_by(File.updated_at.desc(), File.id.desc()).offset(skip).limit(limit))
            hydrated = [FileModel.model_validate(file) for file in result.scalars().all()]
            if include_content:
                hydrated = [await self._hydrate_external_content(file, db) for file in hydrated]
            items = [FileModelResponse.model_validate(file) for file in hydrated]

            return FileListResponse(items=items, total=total)

    @staticmethod
    def _glob_to_like_pattern(glob: str) -> str:
        """
        Convert a glob/fnmatch pattern to a SQL LIKE pattern.

        Escapes SQL special characters and converts glob wildcards:
        - `*` becomes `%` (match any sequence of characters)
        - `?` becomes `_` (match exactly one character)

        Args:
            glob: A glob pattern (e.g., "*.txt", "file?.doc")

        Returns:
            A SQL LIKE compatible pattern with proper escaping.
        """
        # Escape SQL special characters first, then convert glob wildcards
        pattern = glob.replace('\\', '\\\\')
        pattern = pattern.replace('%', '\\%')
        pattern = pattern.replace('_', '\\_')
        pattern = pattern.replace('*', '%')
        pattern = pattern.replace('?', '_')
        return pattern

    async def search_files(
        self,
        user_id: str | None = None,
        filename: str = '*',
        skip: int = 0,
        limit: int = 100,
        include_content: bool = True,
        db: AsyncSession | None = None,
    ) -> list[FileModel]:
        """
        Search files with glob pattern matching, optional user filter, and pagination.

        Args:
            user_id: Filter by user ID. If None, returns files for all users.
            filename: Glob pattern to match filenames (e.g., "*.txt"). Default "*" matches all.
            skip: Number of results to skip for pagination.
            limit: Maximum number of results to return.
            db: Optional database session.

        Returns:
            List of matching FileModel objects, ordered by created_at descending.
        """
        async with get_async_db_context(db) as db:
            stmt = select(File)

            if user_id:
                stmt = stmt.filter_by(user_id=user_id)

            pattern = self._glob_to_like_pattern(filename)
            if pattern != '%':
                stmt = stmt.filter(File.filename.ilike(pattern, escape='\\'))

            result = await db.execute(stmt.order_by(File.created_at.desc(), File.id.desc()).offset(skip).limit(limit))
            items = [FileModel.model_validate(file) for file in result.scalars().all()]
            if include_content:
                items = [await self._hydrate_external_content(file, db) for file in items]
            return items

    async def update_file_by_id(
        self, id: str, form_data: FileUpdateForm, db: AsyncSession | None = None
    ) -> FileModel | None:
        async with get_async_db_context(db) as db:
            try:
                result = await db.execute(select(File).filter_by(id=id))
                file = result.scalars().first()
                external_object_key = None

                if form_data.hash is not None:
                    file.hash = form_data.hash

                if form_data.data is not None:
                    file.data = {**(file.data if file.data else {}), **form_data.data}
                    if 'content' in form_data.data:
                        external_object_key = await self._remove_external_content_record(file.id, db)

                if form_data.meta is not None:
                    file.meta = {**(file.meta if file.meta else {}), **form_data.meta}

                file.updated_at = int(time.time())
                await db.commit()
                await self._delete_external_object(external_object_key)
                return FileModel.model_validate(file)
            except Exception as e:
                log.exception(f'Error updating file completely by id: {e}')
                return None

    async def update_file_hash_by_id(
        self, id: str, hash: str | None, db: AsyncSession | None = None
    ) -> FileModel | None:
        async with get_async_db_context(db) as db:
            try:
                result = await db.execute(select(File).filter_by(id=id))
                file = result.scalars().first()
                file.hash = hash
                file.updated_at = int(time.time())
                await db.commit()

                return FileModel.model_validate(file)
            except Exception:
                return None

    async def update_file_data_by_id(self, id: str, data: dict, db: AsyncSession | None = None) -> FileModel | None:
        async with get_async_db_context(db) as db:
            try:
                result = await db.execute(select(File).filter_by(id=id))
                file = result.scalars().first()
                external_object_key = None
                file.data = {**(file.data if file.data else {}), **data}
                if 'content' in data:
                    external_object_key = await self._remove_external_content_record(file.id, db)
                file.updated_at = int(time.time())
                await db.commit()
                await self._delete_external_object(external_object_key)
                return FileModel.model_validate(file)
            except Exception as e:
                return None

    async def update_file_metadata_by_id(self, id: str, meta: dict, db: AsyncSession | None = None) -> FileModel | None:
        async with get_async_db_context(db) as db:
            try:
                result = await db.execute(select(File).filter_by(id=id))
                file = result.scalars().first()
                file.meta = {**(file.meta if file.meta else {}), **meta}
                file.updated_at = int(time.time())
                await db.commit()
                return FileModel.model_validate(file)
            except Exception:
                return None

    async def update_file_name_by_id(self, id: str, name: str, db: AsyncSession | None = None) -> FileModel | None:
        async with get_async_db_context(db) as db:
            try:
                result = await db.execute(select(File).filter_by(id=id))
                file = result.scalars().first()
                file.filename = name
                file.meta = {**(file.meta if file.meta else {}), 'name': name}
                file.updated_at = int(time.time())
                await db.commit()
                return FileModel.model_validate(file)
            except Exception:
                return None

    async def get_pending_files_for_knowledge(
        self, knowledge_id: str, db: AsyncSession | None = None
    ) -> list[FileModelResponse]:
        """Return files still being processed for this knowledge base.

        These are files uploaded with ``meta.data.knowledge_id`` set, whose
        ``data.status`` is still ``pending`` or ``processing``, and which
        have not yet been added to the ``knowledge_file`` join table.

        The JSON subscript syntax (``Column['key']['subkey'].as_string()``)
        is supported by both SQLite (``json_extract``) and PostgreSQL
        (``->>``/``->``).
        """
        async with get_async_db_context(db) as db:
            try:
                # Lazy import to avoid circular dependency
                from open_webui.models.knowledge import KnowledgeFile

                # Subquery: file IDs already linked to this knowledge base
                linked_ids = (
                    select(KnowledgeFile.file_id).filter(KnowledgeFile.knowledge_id == knowledge_id).correlate(None)
                )

                stmt = (
                    select(File)
                    .filter(
                        File.meta['data']['knowledge_id'].as_string() == knowledge_id,
                        File.data['status'].as_string().in_(['pending', 'processing']),
                        File.id.notin_(linked_ids),
                    )
                    .order_by(File.created_at.desc())
                )
                result = await db.execute(stmt)
                return [FileModelResponse.model_validate(f, from_attributes=True) for f in result.scalars().all()]
            except Exception as e:
                log.warning(f'Error fetching pending files for knowledge {knowledge_id}: {e}')
                return []

    async def delete_file_by_id(self, id: str, db: AsyncSession | None = None) -> bool:
        async with get_async_db_context(db) as db:
            try:
                external_object_key = await self._remove_external_content_record(id, db)
                await db.execute(delete(File).filter_by(id=id))
                await db.commit()
                await self._delete_external_object(external_object_key)
                return True
            except Exception:
                return False

    async def delete_all_files(self, db: AsyncSession | None = None) -> bool:
        async with get_async_db_context(db) as db:
            try:
                try:
                    from open_webui.agent_runtime.models import AgentFileContent

                    references = list((await db.scalars(select(AgentFileContent))).all())
                except Exception:
                    references = []
                await db.execute(delete(File))
                for reference in references:
                    await db.delete(reference)
                await db.commit()
                for reference in references:
                    await self._delete_external_object(reference.object_key)

                return True
            except Exception:
                return False

    @staticmethod
    async def _remove_external_content_record(file_id: str, db: AsyncSession) -> str | None:
        try:
            from open_webui.agent_runtime.models import AgentFileContent

            reference = await db.get(AgentFileContent, file_id)
            if reference is None:
                return None
            key = reference.object_key
            await db.delete(reference)
            return key
        except Exception:
            return None

    @staticmethod
    async def _delete_external_object(object_key: str | None) -> None:
        if not object_key:
            return
        try:
            from open_webui.agent_runtime.files import AgentFileContentStore

            await AgentFileContentStore.configured().object_store.delete(object_key)
        except Exception:
            log.exception('Could not remove external text object %s', object_key)


Files = FilesTable()  # singleton files repository
