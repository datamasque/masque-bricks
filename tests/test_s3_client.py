"""Tests for s3_uri normalisation."""

from masque_bricks.s3_client import s3_uri


def test_single_part():
    assert s3_uri("bucket", "prefix") == "s3://bucket/prefix"


def test_strips_and_joins_slashes():
    assert s3_uri("bucket", "/raw/", "users/", "/ts") == "s3://bucket/raw/users/ts"


def test_skips_empty_parts():
    assert s3_uri("bucket", "", "raw", "") == "s3://bucket/raw"


def test_no_parts():
    assert s3_uri("bucket") == "s3://bucket"


def test_all_empty_parts():
    assert s3_uri("bucket", "", "/") == "s3://bucket"
