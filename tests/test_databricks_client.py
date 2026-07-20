"""Tests for SQL identifier quoting / format + path validation."""

import pytest

from masque_bricks.databricks_client import (
    _quote_identifier,
    _validate_s3_path,
)


def test_quote_simple():
    assert _quote_identifier("users") == "`users`"


def test_quote_qualified():
    assert _quote_identifier("cat.sch.tbl") == "`cat`.`sch`.`tbl`"


def test_quote_escapes_backtick():
    assert _quote_identifier("we`ird") == "`we``ird`"


def test_quote_rejects_empty_part():
    with pytest.raises(ValueError):
        _quote_identifier("schema..table")


def test_validate_s3_path_rejects_quote():
    with pytest.raises(ValueError):
        _validate_s3_path("s3://bucket/p'; DROP TABLE x; --")


def test_validate_s3_path_ok():
    assert _validate_s3_path("s3://bucket/raw/users") == "s3://bucket/raw/users"
