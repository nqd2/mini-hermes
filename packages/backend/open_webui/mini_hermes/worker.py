"""Preview and Drive sync worker. Run as a separate process/container."""

import asyncio
import base64
import hashlib
import logging
from datetime import timedelta
from io import BytesIO

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import aliased

from open_webui.internal.db import AsyncSessionLocal

from .drive import DriveError, Gateway, NATIVE_TO_OFFICE
from .edits import extract_text
from .models import Document, Preview, Revision, SyncJob, now
from .oauth import access_token_for
from .service import build_preview
from .storage import ObjectStore


log = logging.getLogger(__name__)


def _same_native_content(remote: bytes, desired: bytes, filename: str) -> bool:
    suffix = filename.lower().rsplit('.', 1)[-1]
    if extract_text(remote, filename) != extract_text(desired, filename):
        return False
    if suffix == 'xlsx':
        from openpyxl import load_workbook

        remote_book = load_workbook(BytesIO(remote), read_only=True)
        desired_book = load_workbook(BytesIO(desired), read_only=True)
        return remote_book.sheetnames == desired_book.sheetnames
    if suffix == 'pptx':
        from pptx import Presentation

        return len(Presentation(BytesIO(remote)).slides) == len(Presentation(BytesIO(desired)).slides)
    return True


async def claim_preview():
    async with AsyncSessionLocal() as db:
        preview = (await db.execute(select(Preview).where(
            or_(Preview.status == 'pending',
                (Preview.status == 'processing') & (Preview.lease_until <= now())))
                                    .order_by(Preview.created_at).limit(1).with_for_update(skip_locked=True))).scalar_one_or_none()
        if preview:
            preview.status = 'processing'
            preview.lease_until = now() + timedelta(minutes=5)
            await db.commit()
            return preview.id
    return None


async def claim_sync():
    earlier_job = aliased(SyncJob)
    earlier_revision = aliased(Revision)
    current_revision = aliased(Revision)
    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(SyncJob).join(
            current_revision, SyncJob.revision_id == current_revision.id).where(
            or_(SyncJob.status == 'pending',
                (SyncJob.status == 'processing') & (SyncJob.next_attempt_at <= now()),
                (SyncJob.status == 'failed') & (SyncJob.attempts < 5) & (SyncJob.next_attempt_at <= now())),
            ~exists(select(1).select_from(earlier_job).join(
                earlier_revision, earlier_job.revision_id == earlier_revision.id).where(
                    earlier_job.document_id == SyncJob.document_id,
                    earlier_revision.number < current_revision.number,
                    earlier_job.status != 'synced')))
            .order_by(SyncJob.created_at).limit(1).with_for_update(skip_locked=True))).scalar_one_or_none()
        if job:
            job.status = 'processing'
            job.attempts += 1
            job.next_attempt_at = now() + timedelta(minutes=5)
            await db.commit()
            return job.id
    return None


async def process_preview(preview_id: str):
    async with AsyncSessionLocal() as db:
        preview = (await db.execute(select(Preview).where(Preview.id == preview_id))).scalar_one()
        await build_preview(db, ObjectStore(), preview)


async def process_sync(job_id: str):
    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one()
        doc = (await db.execute(select(Document).where(Document.id == job.document_id))).scalar_one()
        revision = (await db.execute(select(Revision).where(Revision.id == job.revision_id))).scalar_one()
        gateway = Gateway()
        try:
            token = await access_token_for(db, doc.owner_id)
            content = await ObjectStore().get(revision.object_key)
            if doc.drive_file_id:
                metadata = await gateway.call('metadata', access_token=token, file_id=doc.drive_file_id)
                if not metadata.get('capabilities', {}).get('canEdit'):
                    raise DriveError('User no longer has edit access to this Drive file')
                if str(metadata['version']) != doc.drive_version:
                    remote = base64.b64decode((await gateway.call(
                        'download', access_token=token, file_id=doc.drive_file_id,
                        mime_type=doc.drive_mime_type))['content_b64'])
                    same = (_same_native_content(remote, content, doc.filename)
                            if doc.drive_mime_type in NATIVE_TO_OFFICE
                            else hashlib.sha256(remote).digest() == hashlib.sha256(content).digest())
                    if not same:
                        raise DriveError('Drive conflict: file changed since the last internal sync; reimport before retry')
                    if doc.drive_mime_type in NATIVE_TO_OFFICE:
                        found = await gateway.call('find-created', access_token=token,
                                                   document_id=doc.id, role='office_companion')
                        companion = await gateway.call('upload', access_token=token, name=doc.filename,
                                                       mime_type=doc.mime_type, content_b64=base64.b64encode(content).decode(),
                                                       file_id=found['file']['id'] if found['file'] else None,
                                                       parents=metadata.get('parents'),
                                                       app_properties={'mh_document_id': doc.id, 'mh_role': 'office_companion'})
                        doc.office_companion_id = companion['id']
                    doc.drive_version = str(metadata['version'])
                    job.status = 'synced'
                    job.error = None
                    await db.commit()
                    return
                if doc.drive_mime_type in NATIVE_TO_OFFICE:
                    expected_revision = (await gateway.call('native-revision', access_token=token,
                                                            file_id=doc.drive_file_id,
                                                            mime_type=doc.drive_mime_type))['revision_id']
                    checked = await gateway.call('metadata', access_token=token, file_id=doc.drive_file_id)
                    if str(checked['version']) != doc.drive_version:
                        raise DriveError('Drive conflict: file changed before write; reimport before retry')
                    if expected_revision is None:
                        checked = await gateway.call('metadata', access_token=token, file_id=doc.drive_file_id)
                        if str(checked['version']) != doc.drive_version:
                            raise DriveError('Drive conflict: file changed before write; reimport before retry')
                    await gateway.call('apply-native', access_token=token, file_id=doc.drive_file_id,
                                       mime_type=doc.drive_mime_type, operations=revision.operations or [],
                                       expected_revision=expected_revision)
                    if not doc.office_companion_id:
                        found = await gateway.call('find-created', access_token=token,
                                                   document_id=doc.id, role='office_companion')
                        if found['file']:
                            doc.office_companion_id = found['file']['id']
                    companion = await gateway.call('upload', access_token=token, name=doc.filename,
                                                   mime_type=doc.mime_type, content_b64=base64.b64encode(content).decode(),
                                                   file_id=doc.office_companion_id,
                                                   parents=metadata.get('parents'),
                                                   app_properties={'mh_document_id': doc.id, 'mh_role': 'office_companion'})
                    doc.office_companion_id = companion['id']
                else:
                    checked = await gateway.call('metadata', access_token=token, file_id=doc.drive_file_id)
                    if str(checked['version']) != doc.drive_version:
                        raise DriveError('Drive conflict: file changed before write; reimport before retry')
                    await gateway.call('upload', access_token=token, name=doc.filename,
                                       mime_type=doc.mime_type, content_b64=base64.b64encode(content).decode(),
                                       file_id=doc.drive_file_id)
                latest = await gateway.call('metadata', access_token=token, file_id=doc.drive_file_id)
                doc.drive_version = str(latest['version'])
            else:
                found = await gateway.call('find-created', access_token=token,
                                           document_id=doc.id, role='source')
                created = await gateway.call('upload', access_token=token, name=doc.filename,
                                             mime_type=doc.mime_type, content_b64=base64.b64encode(content).decode(),
                                             file_id=found['file']['id'] if found['file'] else None,
                                             app_properties={'mh_document_id': doc.id, 'mh_role': 'source'})
                doc.drive_file_id = created['id']
                doc.drive_mime_type = doc.mime_type
                doc.drive_version = str((await gateway.call('metadata', access_token=token,
                                                            file_id=doc.drive_file_id))['version'])
            job.status = 'synced'
            job.error = None
        except Exception as exc:
            log.exception('Drive sync failed for %s', job_id)
            job.status = 'failed'
            job.error = str(exc)[:2000]
            job.next_attempt_at = now() + timedelta(seconds=min(3600, 2 ** min(job.attempts, 10)))
            if 'Drive conflict:' in job.error or 'no longer has edit access' in job.error:
                job.attempts = 5
        await db.commit()


async def run():
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            preview_id = await claim_preview()
            if preview_id:
                await process_preview(preview_id)
                continue
            job_id = await claim_sync()
            if job_id:
                await process_sync(job_id)
                continue
        except Exception:
            log.exception('Worker cycle failed; will retry')
        await asyncio.sleep(1)


if __name__ == '__main__':
    asyncio.run(run())
