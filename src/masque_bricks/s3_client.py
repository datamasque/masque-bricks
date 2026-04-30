"""S3 client for file upload/download operations."""

from pathlib import Path

import boto3
from botocore.exceptions import ClientError

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

    def upload_file(self, local_path: str, s3_key: str) -> str:
        """Upload a local file to S3.

        Args:
            local_path: Path to the local file.
            s3_key: S3 object key (path within the bucket).

        Returns:
            S3 URI of the uploaded file (s3://bucket/key).
        """
        self.client.upload_file(local_path, self.config.bucket, s3_key)
        return s3_uri(self.config.bucket, s3_key)

    def download_file(self, s3_key: str, local_path: str) -> str:
        """Download a file from S3 to local path.

        Args:
            s3_key: S3 object key to download.
            local_path: Local path to save the file.

        Returns:
            Path to the downloaded file.
        """
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.config.bucket, s3_key, local_path)
        return local_path

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

    def delete_file(self, s3_key: str) -> None:
        """Delete a file from S3.

        Args:
            s3_key: S3 object key to delete.
        """
        self.client.delete_object(Bucket=self.config.bucket, Key=s3_key)

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

    def file_exists(self, s3_key: str) -> bool:
        """Check if a file exists in S3.

        Args:
            s3_key: S3 object key to check.

        Returns:
            True if the file exists, False otherwise.
        """
        try:
            self.client.head_object(Bucket=self.config.bucket, Key=s3_key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise

