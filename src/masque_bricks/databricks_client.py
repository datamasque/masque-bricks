"""Databricks client for table export/import operations."""

from typing import Literal

from databricks import sql

from .config import DatabricksConfig

FileFormat = Literal["PARQUET", "CSV", "JSON", "DELTA"]
ImportMode = Literal["OVERWRITE", "APPEND"]


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
        import requests

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
        file_format: FileFormat = "PARQUET",
        overwrite: bool = True,
    ) -> str:
        """Export a Databricks table directly to S3 using SQL.

        Uses CREATE TABLE ... AS SELECT to write data to S3.

        Args:
            table: Table name to export.
            schema: Schema (database) containing the table.
            s3_path: S3 path (e.g., 's3://bucket/prefix/').
            file_format: Output format - PARQUET, CSV, JSON, etc.
            overwrite: Whether to overwrite existing data.

        Returns:
            S3 path where data was written.
        """
        # Use a temporary external table name
        temp_table = f"_masque_export_{table}"
        full_temp_table = f"{schema}.{temp_table}"
        source_table = f"{schema}.{table}"

        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                # Drop temp table if exists
                if overwrite:
                    cursor.execute(f"DROP TABLE IF EXISTS {full_temp_table}")

                # Create external table at S3 location with data from source
                export_sql = f"""
                CREATE TABLE {full_temp_table}
                USING {file_format}
                LOCATION '{s3_path}'
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
        file_format: FileFormat = "PARQUET",
        mode: ImportMode = "OVERWRITE",
    ) -> None:
        """Import data from S3 into a managed Databricks table.

        Copies data into managed storage via CTAS / INSERT, so the target table is
        self-contained and the source S3 files can be deleted afterwards.

        Args:
            s3_path: S3 path containing the files to import.
            target_table: Target table name.
            schema: Schema (database) for the target table.
            file_format: Source file format - PARQUET, CSV, JSON, etc.
            mode: Import mode - 'OVERWRITE' (drop and replace) or 'APPEND' (insert into existing).
        """
        full_table = f"{schema}.{target_table}"
        source = f"{file_format.lower()}.`{s3_path}`"

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
        col_list = ", ".join(columns)
        placeholders = ", ".join("?" * len(columns))
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.executemany(sql, rows)
