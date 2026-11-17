import asyncio
from functools import lru_cache

import boto3
from botocore.client import Config

from app.config import settings


@lru_cache
def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        config=Config(signature_version="s3v4"),
    )


def _put(key: str, data: bytes, mime: str) -> None:
    _client().put_object(Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=mime)


def _get(key: str) -> bytes:
    obj = _client().get_object(Bucket=settings.s3_bucket, Key=key)
    return obj["Body"].read()


async def put(key: str, data: bytes, mime: str) -> None:
    await asyncio.to_thread(_put, key, data, mime)


async def get(key: str) -> bytes:
    return await asyncio.to_thread(_get, key)
