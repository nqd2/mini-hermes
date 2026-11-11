from __future__ import annotations

from io import BytesIO

import pytest
from botocore.exceptions import ClientError

from open_webui.mini_hermes.storage import ObjectStore


class ConditionalS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, **kwargs):
        key = kwargs['Key']
        if kwargs.get('IfNoneMatch') == '*' and key in self.objects:
            raise ClientError(
                {
                    'Error': {'Code': 'PreconditionFailed', 'Message': 'Object already exists'},
                    'ResponseMetadata': {'HTTPStatusCode': 412},
                },
                'PutObject',
            )
        self.objects[key] = kwargs['Body']

    def get_object(self, *, Bucket, Key):
        return {'Body': BytesIO(self.objects[Key])}


@pytest.mark.asyncio
async def test_immutable_object_put_is_idempotent_but_never_overwrites(monkeypatch):
    client = ConditionalS3()
    monkeypatch.setattr('open_webui.mini_hermes.storage.boto3.client', lambda *args, **kwargs: client)
    store = ObjectStore()

    await store.put_immutable('documents/doc/staged/sha256', b'original', 'text/plain')
    await store.put_immutable('documents/doc/staged/sha256', b'original', 'text/plain')

    with pytest.raises(IOError, match='different bytes'):
        await store.put_immutable('documents/doc/staged/sha256', b'changed', 'text/plain')

    assert client.objects['documents/doc/staged/sha256'] == b'original'
