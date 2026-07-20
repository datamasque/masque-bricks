"""Configuration management for masque-bricks."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class DatabricksConfig:
    """Databricks connection configuration.

    Supports two authentication methods:
    1. Personal Access Token: set `token`
    2. OAuth (Service Principal): set `client_id` and `client_secret`
    """

    host: str
    http_path: str
    # Token auth (option 1)
    token: str | None = None
    # OAuth auth (option 2)
    client_id: str | None = None
    client_secret: str | None = None

    def __post_init__(self):
        """Validate that one auth method is configured."""
        has_token = bool(self.token)
        has_oauth = bool(self.client_id and self.client_secret)

        if not has_token and not has_oauth:
            raise ValueError(
                "Databricks auth required: set either 'token' OR 'client_id' + 'client_secret'"
            )

    @property
    def auth_type(self) -> str:
        """Return the authentication type being used."""
        if self.client_id and self.client_secret:
            return "oauth"
        return "token"


@dataclass
class DataMasqueConfig:
    """DataMasque API configuration."""

    host: str
    username: str
    password: str
    # Verify the DataMasque server's TLS certificate. Default secure (True);
    # set False only for instances with self-signed certs you trust.
    verify_ssl: bool = True


@dataclass
class S3Config:
    """S3 configuration."""

    bucket: str
    region: str


@dataclass
class Config:
    """Complete application configuration."""

    databricks: DatabricksConfig
    datamasque: DataMasqueConfig
    s3: S3Config


DEFAULT_CONFIG_PATHS = [
    Path("config.yaml"),
    Path("config.yml"),
    Path.home() / ".config" / "masque-bricks" / "config.yaml",
]


def _as_bool(value: Any, default: bool) -> bool:
    """Coerce a YAML/env value to bool.

    Recognised true: 1/true/yes/on. Recognised false: 0/false/no/off. Anything
    unset, empty, or unrecognised returns ``default`` — so a typo in a security
    toggle (e.g. DATAMASQUE_VERIFY_SSL=enabled) keeps the secure default rather
    than silently flipping it off.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return default


def _get_nested(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Get a nested value from a dictionary."""
    for key in keys:
        if not isinstance(data, dict):
            return default
        data = data.get(key, default)
        if data is default:
            return default
    return data


def _find_config_file() -> Path | None:
    """Find the first existing config file from default paths."""
    for path in DEFAULT_CONFIG_PATHS:
        if path.exists():
            return path
    return None


def load_config(config_path: str | Path | None = None) -> Config:
    """Load configuration from a YAML file.

    Config file locations (in order of precedence):
        1. Explicitly provided path via config_path argument
        2. ./config.yaml or ./config.yml in current directory
        3. ~/.config/masque-bricks/config.yaml

    Databricks supports two auth methods:
        1. Personal Access Token: set databricks.token
        2. OAuth Service Principal: set databricks.client_id and databricks.client_secret

    Environment variables can override config file values:
        DATABRICKS_HOST, DATABRICKS_HTTP_PATH
        DATABRICKS_TOKEN (for token auth)
        DATABRICKS_CLIENT_ID, DATABRICKS_CLIENT_SECRET (for OAuth)
        DATAMASQUE_HOST, DATAMASQUE_USERNAME, DATAMASQUE_PASSWORD, DATAMASQUE_VERIFY_SSL
        S3_BUCKET, AWS_REGION

    Args:
        config_path: Optional explicit path to config file.

    Returns:
        Config object with all settings.

    Raises:
        FileNotFoundError: If no config file is found.
        ValueError: If required configuration is missing.

    Example config.yaml (OAuth - recommended):
        databricks:
          host: https://myworkspace.cloud.databricks.com
          http_path: /sql/1.0/warehouses/abc123
          client_id: <your-service-principal-client-id>
          client_secret: <your-service-principal-secret>

    Example config.yaml (Token):
        databricks:
          host: https://myworkspace.cloud.databricks.com
          http_path: /sql/1.0/warehouses/abc123
          token: <your-databricks-pat>
    """
    # Find config file
    if config_path:
        config_file = Path(config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
    else:
        config_file = _find_config_file()
        if not config_file:
            raise FileNotFoundError(
                f"No config file found. Create one at: {DEFAULT_CONFIG_PATHS[0]} "
                f"or specify with --config"
            )

    with open(config_file) as f:
        data = yaml.safe_load(f) or {}

    # Extract values with env var overrides
    missing = []

    # Databricks config
    databricks_host = os.environ.get(
        "DATABRICKS_HOST", _get_nested(data, "databricks", "host", default="")
    )
    if not databricks_host:
        missing.append("databricks.host")

    databricks_http_path = os.environ.get(
        "DATABRICKS_HTTP_PATH", _get_nested(data, "databricks", "http_path", default="")
    )
    if not databricks_http_path:
        missing.append("databricks.http_path")

    # Databricks auth - token OR client_id+client_secret
    databricks_token = os.environ.get(
        "DATABRICKS_TOKEN", _get_nested(data, "databricks", "token", default="")
    ) or None

    databricks_client_id = os.environ.get(
        "DATABRICKS_CLIENT_ID", _get_nested(data, "databricks", "client_id", default="")
    ) or None

    databricks_client_secret = os.environ.get(
        "DATABRICKS_CLIENT_SECRET", _get_nested(data, "databricks", "client_secret", default="")
    ) or None

    # Check auth - need token OR (client_id AND client_secret)
    has_token = bool(databricks_token)
    has_oauth = bool(databricks_client_id and databricks_client_secret)
    if not has_token and not has_oauth:
        missing.append("databricks.token OR (databricks.client_id + databricks.client_secret)")

    # DataMasque config
    datamasque_host = os.environ.get(
        "DATAMASQUE_HOST", _get_nested(data, "datamasque", "host", default="")
    )
    if not datamasque_host:
        missing.append("datamasque.host")

    datamasque_username = os.environ.get(
        "DATAMASQUE_USERNAME", _get_nested(data, "datamasque", "username", default="")
    )
    if not datamasque_username:
        missing.append("datamasque.username")

    datamasque_password = os.environ.get(
        "DATAMASQUE_PASSWORD", _get_nested(data, "datamasque", "password", default="")
    )
    if not datamasque_password:
        missing.append("datamasque.password")

    # TLS verification — default secure (True). Override per-instance for self-signed certs.
    datamasque_verify_ssl = _as_bool(
        os.environ.get(
            "DATAMASQUE_VERIFY_SSL",
            _get_nested(data, "datamasque", "verify_ssl", default=None),
        ),
        default=True,
    )

    # S3 config
    s3_bucket = os.environ.get(
        "S3_BUCKET", _get_nested(data, "s3", "bucket", default="")
    )
    if not s3_bucket:
        missing.append("s3.bucket")

    s3_region = os.environ.get(
        "AWS_REGION", _get_nested(data, "s3", "region", default="us-east-1")
    )

    if missing:
        raise ValueError(
            f"Missing required configuration: {', '.join(missing)}. "
            f"Add to {config_file} or set via environment variables."
        )

    return Config(
        databricks=DatabricksConfig(
            host=databricks_host,
            http_path=databricks_http_path,
            token=databricks_token,
            client_id=databricks_client_id,
            client_secret=databricks_client_secret,
        ),
        datamasque=DataMasqueConfig(
            host=datamasque_host,
            username=datamasque_username,
            password=datamasque_password,
            verify_ssl=datamasque_verify_ssl,
        ),
        s3=S3Config(
            bucket=s3_bucket,
            region=s3_region,
        ),
    )
