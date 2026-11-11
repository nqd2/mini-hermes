"""Immutable document objects in the same S3-compatible bucket as Open WebUI."""

import asyncio
import hashlib
import os

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


class ObjectStore:
    def __init__(self):
        self.bucket = os.getenv('S3_BUCKET_NAME', 'mini-hermes')
        self.client = boto3.client(
            's3',
            endpoint_url=os.getenv('S3_ENDPOINT_URL', 'http://127.0.0.1:8333'),
            region_name=os.getenv('S3_REGION_NAME', 'us-east-1'),
            aws_access_key_id=os.getenv('S3_ACCESS_KEY_ID', 'admin'),
            aws_secret_access_key=os.getenv('S3_SECRET_ACCESS_KEY', 'secret'),
            config=Config(s3={'addressing_style': 'path'}),
        )

    async def put(self, key: str, content: bytes, mime_type: str):
        await asyncio.to_thread(self.client.put_object, Bucket=self.bucket, Key=key, Body=content, ContentType=mime_type)

    async def put_immutable(self, key: str, content: bytes, mime_type: str):
        """Create a content-addressed object once; never replace existing bytes."""
        digest = hashlib.sha256(content).hexdigest()
        try:
            await asyncio.to_thread(
                self.client.put_object,
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType=mime_type,
                Metadata={'sha256': digest},
                IfNoneMatch='*',
            )
        except ClientError as exc:
            status = exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
            code = exc.response.get('Error', {}).get('Code')
            if status not in {409, 412} and code not in {'PreconditionFailed', 'ConditionalRequestConflict'}:
                raise
            current = await self.get(key)
            if hashlib.sha256(current).hexdigest() != digest:
                raise IOError('Immutable object key already contains different bytes') from exc

    async def list_prefix(self, prefix: str) -> list[str]:
        """List keys for bounded cleanup of staged artifacts."""
        keys: list[str] = []
        continuation_token = None
        while True:
            kwargs = {'Bucket': self.bucket, 'Prefix': prefix}
            if continuation_token:
                kwargs['ContinuationToken'] = continuation_token
            page = await asyncio.to_thread(self.client.list_objects_v2, **kwargs)
            keys.extend(item['Key'] for item in page.get('Contents', []))
            if not page.get('IsTruncated'):
                return keys
            continuation_token = page.get('NextContinuationToken')

    async def get(self, key: str) -> bytes:
        response = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        return await asyncio.to_thread(response['Body'].read)

    async def get_range(self, key: str, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0:
            raise ValueError('object byte ranges must be non-negative')
        if length == 0:
            return b''
        response = await asyncio.to_thread(
            self.client.get_object,
            Bucket=self.bucket,
            Key=key,
            Range=f'bytes={offset}-{offset + length - 1}',
        )
        return await asyncio.to_thread(response['Body'].read)

    async def delete(self, key: str):
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)
