"""Immutable document objects in the same S3-compatible bucket as Open WebUI."""

import asyncio
import os

import boto3
from botocore.config import Config


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

    async def get(self, key: str) -> bytes:
        response = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        return await asyncio.to_thread(response['Body'].read)

    async def delete(self, key: str):
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)
