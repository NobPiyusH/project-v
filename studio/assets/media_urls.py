"""Short-lived presigned URLs so the dashboard can play media straight from the local S3 store (SeaweedFS supports range requests)."""

from __future__ import annotations

from typing import Any

from studio.config import Settings


class MediaUrls:
    def __init__(self, settings: Settings, client: Any | None = None):
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
                region_name="us-east-1",
                config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
            )
        self._s3 = client

    def presign(self, s3_uri: str | None, ttl_seconds: int) -> str:
        if s3_uri is None or not s3_uri.startswith("s3://"):
            raise ValueError(f"Not an s3 uri: {s3_uri}")
        bucket, _, key = s3_uri[5:].partition("/")
        if not key:
            raise ValueError(f"Not an s3 uri: {s3_uri}")
        return self._s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=ttl_seconds)
