"""Databricks client for table export/import operations."""

from typing import Literal

import requests
from databricks import sql

from .config import DatabricksConfig

ImportMode = Literal["OVERWRITE", "APPEND"]

# The pipeline is Parquet-only end to end (export writes Parquet, import reads it back).
FILE_FORMAT = "PARQUET"

# Databricks SQL identifiers (catalog/schema/table) are not parameterisable, so they are
# interpolated into SQL text. Backtick-quote each dot-separated part and reject anything
# that can't be a plain identifier to keep these strings off the SQL-injection surface.


def _quote_identifier(name: str) -> str:
    """Backtick-quote a possibly-qualified identifier (``a.b.c`` -> `` `a`.`b`.`c` ``).

    Each part is validated as a non-empty identifier and embedded backticks are doubled,
    matching Databricks' delimited-identifier escaping.
    """
    parts = name.split(".")
    quoted = []
    for part in parts:
        part = part.strip()
        if not part:
            raise ValueError(f"Invalid SQL identifier: {name!r}")
        quoted.append("`" + part.replace("`", "``") + "`")
    return ".".join(quoted)


def _validate_s3_path(s3_path: str) -> str:
    """Reject S3 paths with SQL-delimiter characters.

    The path is embedded both in a single-quoted LOCATION literal and in a backtick-quoted
    ``parquet.`...``` read path, so a single-quote or backtick could break out of either.
    """
    if "'" in s3_path or "`" in s3_path:
        raise ValueError(f"Invalid character in S3 path: {s3_path!r}")
    return s3_path


class DatabricksClient:
    """Client for interacting with Databricks SQL warehouse."""

    def __init__(self, config: DatabricksConfig):
        """Initialize the Databricks client.

        Args:
            config: Databricks connection configuration.
        """
        self.config = config

    def _get_connection(self):
        """Create a new database connection.

        Uses OAuth (client_id/client_secret) if configured, otherwise uses token.
        """
        # Strip https:// from host if present - the connector expects just the hostname
        hostname = self.config.host
        if hostname.startswith("https://"):
            hostname = hostname[8:]
        if hostname.startswith("http://"):
            hostname = hostname[7:]

        if self.config.auth_type == "oauth":
            # OAuth M2M (Service Principal) authentication
            access_token = self._get_oauth_token(hostname)
            return sql.connect(
                server_hostname=hostname,
                http_path=self.config.http_path,
                access_token=access_token,
            )
        else:
            # Personal Access Token authentication
            return sql.connect(
                server_hostname=hostname,
                http_path=self.config.http_path,
                access_token=self.config.token,
            )

    def _get_oauth_token(self, hostname: str) -> str:
        """Get OAuth access token using client credentials flow.

        Args:
            hostname: Databricks workspace hostname.

        Returns:
            Access token string.
        """
        token_url = f"https://{hostname}/oidc/v1/token"

        response = requests.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "scope": "all-apis",
            },
            auth=(self.config.client_id, self.config.client_secret),
        )
        response.raise_for_status()

        return response.json()["access_token"]

    def export_table_to_s3(
        self,
        table: str,
        schema: str,
        s3_path: str,
    ) -> str:
        """Export a Databricks table directly to S3 as Parquet using SQL.

        Uses CREATE TABLE ... AS SELECT to write data to S3.

        Args:
            table: Table name to export.
            schema: Schema (database) containing the table.
            s3_path: S3 path (e.g., 's3://bucket/prefix/').

        Returns:
            S3 path where data was written.
        """
        safe_path = _validate_s3_path(s3_path)
        # Use a temporary external table name (quote the qualified schema, then the table part).
        full_temp_table = _quote_identifier(f"{schema}._masque_export_{table}")
        source_table = _quote_identifier(f"{schema}.{table}")

        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                # Drop any leftover temp table from a previous run
                cursor.execute(f"DROP TABLE IF EXISTS {full_temp_table}")

                # Create external table at S3 location with data from source
                export_sql = f"""
                CREATE TABLE {full_temp_table}
                USING {FILE_FORMAT}
                LOCATION '{safe_path}'
                AS SELECT * FROM {source_table}
                """
                cursor.execute(export_sql)

                # Drop the table reference (keeps the data in S3)
                cursor.execute(f"DROP TABLE IF EXISTS {full_temp_table}")

        return s3_path

    def import_table_from_s3(
        self,
        s3_path: str,
        target_table: str,
        schema: str,
        mode: ImportMode = "OVERWRITE",
    ) -> None:
        """Import Parquet data from S3 into a managed Databricks table.

        Copies data into managed storage via CTAS / INSERT, so the target table is
        self-contained and the source S3 files can be deleted afterwards.

        Args:
            s3_path: S3 path containing the files to import.
            target_table: Target table name.
            schema: Schema (database) for the target table.
            mode: Import mode - 'OVERWRITE' (drop and replace) or 'APPEND' (insert into existing).
        """
        safe_path = _validate_s3_path(s3_path)
        full_table = _quote_identifier(f"{schema}.{target_table}")
        # Databricks file-format read syntax: e.g. ``parquet.`s3://bucket/prefix```.
        source = f"{FILE_FORMAT.lower()}.`{safe_path}`"

        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                if mode == "OVERWRITE":
                    cursor.execute(f"DROP TABLE IF EXISTS {full_table}")
                    cursor.execute(f"CREATE TABLE {full_table} AS SELECT * FROM {source}")
                else:
                    cursor.execute(f"INSERT INTO {full_table} SELECT * FROM {source}")

    def execute_sql(self, sql_statement: str) -> list:
        """Execute arbitrary SQL and return results.

        Args:
            sql_statement: SQL to execute.

        Returns:
            List of result rows.
        """
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql_statement)
                return cursor.fetchall()

    def insert_rows(
        self,
        table: str,
        columns: list[str],
        rows: list[tuple],
    ) -> None:
        """Insert rows into a table using parameterised queries.

        Args:
            table: Fully qualified table name.
            columns: Column names in the order matching each row tuple.
            rows: Row values as tuples.
        """
        if not rows:
            return
        safe_table = _quote_identifier(table)
        col_list = ", ".join(_quote_identifier(c) for c in columns)
        placeholders = ", ".join("?" * len(columns))
        sql = f"INSERT INTO {safe_table} ({col_list}) VALUES ({placeholders})"
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.executemany(sql, rows)
