"""S3 client for listing and deleting masking intermediate files."""

import boto3

from .config import S3Config


def s3_uri(bucket: str, *parts: str) -> str:
    """Build an ``s3://bucket/path`` URI, joining parts with ``/`` and normalising slashes.

    Empty parts are skipped, and leading/trailing slashes on each part are stripped before joining.
    """
    path = "/".join(p.strip("/") for p in parts if p)
    return f"s3://{bucket}/{path}" if path else f"s3://{bucket}"


class S3Client:
    """Client for interacting with S3."""

    def __init__(self, config: S3Config):
        """Initialize the S3 client.

        Args:
            config: S3 configuration with bucket and region.
        """
        self.config = config
        self.client = boto3.client("s3", region_name=config.region)

    def list_files(self, prefix: str) -> list[str]:
        """List files under a prefix in the bucket.

        Args:
            prefix: S3 prefix to list.

        Returns:
            List of S3 keys matching the prefix.
        """
        keys = []
        paginator = self.client.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=self.config.bucket, Prefix=prefix):
            if "Contents" in page:
                for obj in page["Contents"]:
                    keys.append(obj["Key"])

        return keys

    def delete_prefix(self, prefix: str) -> int:
        """Delete all files under a prefix.

        Args:
            prefix: S3 prefix to delete.

        Returns:
            Number of files deleted.
        """
        keys = self.list_files(prefix)
        # S3 DeleteObjects accepts up to 1000 keys per call.
        for chunk_start in range(0, len(keys), 1000):
            chunk = keys[chunk_start:chunk_start + 1000]
            self.client.delete_objects(
                Bucket=self.config.bucket,
                Delete={"Objects": [{"Key": k} for k in chunk]},
            )
        return len(keys)

