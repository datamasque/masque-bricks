"""Tests for config loading: env-override precedence and verify_ssl default."""

import textwrap

import pytest

from masque_bricks.config import _as_bool, load_config


def _write_config(tmp_path, body: str):
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(body))
    return path


BASE_CONFIG = """\
    databricks:
      host: https://file.cloud.databricks.com
      http_path: /sql/1.0/warehouses/abc
      token: file-token
    datamasque:
      host: https://dm.example.com
      username: file-user
      password: file-pass
    s3:
      bucket: file-bucket
      region: eu-west-1
"""


def test_load_config_reads_file(tmp_path):
    cfg = load_config(_write_config(tmp_path, BASE_CONFIG))
    assert cfg.databricks.host == "https://file.cloud.databricks.com"
    assert cfg.databricks.token == "file-token"
    assert cfg.datamasque.username == "file-user"
    assert cfg.s3.region == "eu-west-1"


def test_env_overrides_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABRICKS_TOKEN", "env-token")
    monkeypatch.setenv("DATAMASQUE_PASSWORD", "env-pass")
    monkeypatch.setenv("S3_BUCKET", "env-bucket")
    cfg = load_config(_write_config(tmp_path, BASE_CONFIG))
    assert cfg.databricks.token == "env-token"
    assert cfg.datamasque.password == "env-pass"
    assert cfg.s3.bucket == "env-bucket"


def test_verify_ssl_defaults_secure(tmp_path):
    cfg = load_config(_write_config(tmp_path, BASE_CONFIG))
    assert cfg.datamasque.verify_ssl is True


def test_verify_ssl_from_file(tmp_path):
    body = BASE_CONFIG.replace(
        "      password: file-pass\n",
        "      password: file-pass\n      verify_ssl: false\n",
    )
    cfg = load_config(_write_config(tmp_path, body))
    assert cfg.datamasque.verify_ssl is False


def test_verify_ssl_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAMASQUE_VERIFY_SSL", "false")
    cfg = load_config(_write_config(tmp_path, BASE_CONFIG))
    assert cfg.datamasque.verify_ssl is False


@pytest.mark.parametrize(
    "value,default,expected",
    [
        ("true", False, True),
        ("False", True, False),
        ("yes", False, True),
        ("0", True, False),
        ("", True, True),
        (None, False, False),
        (True, False, True),
        ("off", True, False),
        # Unrecognised values keep the default — fail-secure for security toggles.
        ("enabled", True, True),
        ("ture", True, True),
        ("enabled", False, False),
    ],
)
def test_as_bool(value, default, expected):
    assert _as_bool(value, default) is expected


def test_missing_required_raises(tmp_path):
    body = "datamasque:\n  host: https://dm.example.com\n"
    with pytest.raises(ValueError, match="Missing required configuration"):
        load_config(_write_config(tmp_path, body))
