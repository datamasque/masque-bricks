"""Command-line interface for masque-bricks."""

from datetime import datetime
from pathlib import Path

import click
from dotenv import load_dotenv

from .config import load_config
from .databricks_client import DatabricksClient
from .datamasque_client import DataMasqueClient, DataMasqueError
from .s3_client import S3Client, s3_uri

load_dotenv(override=False)


def get_clients(config_path: str | None = None):
    """Initialize all clients from config."""
    config = load_config(config_path)
    return (
        DatabricksClient(config.databricks),
        S3Client(config.s3),
        DataMasqueClient(config.datamasque),
        config,
    )


def qualify_schema(catalog: str | None, schema: str) -> str:
    """Build a fully-qualified schema reference, optionally prefixed with a catalog."""
    return f"{catalog}.{schema}" if catalog else schema


def qualify_table(catalog: str | None, schema: str, table: str) -> str:
    """Build a fully-qualified table reference, optionally prefixed with a catalog."""
    return f"{catalog}.{schema}.{table}" if catalog else f"{schema}.{table}"


def resolve_ruleset_path(ruleset_file: str) -> Path:
    """Resolve a ruleset path, falling back to the package directory for relative paths."""
    path = Path(ruleset_file)
    if not path.is_absolute() and not path.exists():
        path = Path(__file__).parent.parent.parent / ruleset_file
    if not path.exists():
        raise click.ClickException(f"Ruleset file not found: {ruleset_file}")
    return path


@click.group()
@click.version_option()
@click.option(
    "--config", "-c",
    type=click.Path(exists=True),
    default=None,
    help="Path to config YAML file (default: ./config.yaml or ~/.config/masque-bricks/config.yaml)",
)
@click.pass_context
def main(ctx, config):
    """masque-bricks: Databricks table masking via DataMasque."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config


@main.command()
@click.option("--table", required=True, help="Table name to export")
@click.option("--schema", default="default", help="Schema/database containing the table")
@click.option("--catalog", default=None, help="Catalog name (for Unity Catalog)")
@click.option("--output-prefix", default="raw/", help="S3 prefix for output files")
@click.pass_context
def export(ctx, table: str, schema: str, catalog: str | None, output_prefix: str):
    """Export a Databricks table to S3 as Parquet (via SQL)."""
    databricks_client, _, _, config = get_clients(ctx.obj.get("config_path"))

    full_schema = qualify_schema(catalog, schema)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    s3_path = s3_uri(config.s3.bucket, output_prefix, table, timestamp)

    click.echo(f"Exporting {full_schema}.{table} to S3...")
    click.echo(f"  Destination: {s3_path}")

    try:
        databricks_client.export_table_to_s3(
            table=table,
            schema=full_schema,
            s3_path=s3_path,
        )
        click.echo(f"Exported to: {s3_path}")
    except Exception as e:
        raise click.ClickException(f"Export failed: {e}")


@main.command()
@click.option("--source-prefix", required=True, help="S3 prefix containing source files")
@click.option("--dest-prefix", required=True, help="S3 prefix for masked output")
@click.option(
    "--ruleset-file",
    default="example_rulesets/sample_pii.yaml",
    help="Path to ruleset YAML file",
)
@click.option("--ruleset-name", default=None, help="Name for the ruleset in DataMasque")
@click.option("--timeout", default=3600, help="Timeout in seconds for masking run")
@click.pass_context
def mask(
    ctx,
    source_prefix: str,
    dest_prefix: str,
    ruleset_file: str,
    ruleset_name: str | None,
    timeout: int,
):
    """Run DataMasque file masking on S3 data."""
    _, _, datamasque_client, config = get_clients(ctx.obj.get("config_path"))

    ruleset_path = resolve_ruleset_path(ruleset_file)
    ruleset_yaml = ruleset_path.read_text()

    if ruleset_name is None:
        ruleset_name = ruleset_path.stem

    click.echo(f"Setting up DataMasque masking run...")

    # Create or get source connection
    source_conn = datamasque_client.get_or_create_connection(
        name=f"masque_bricks_source_{config.s3.bucket.replace('-', '_')}",
        bucket=config.s3.bucket,
        base_directory=source_prefix.rstrip("/"),
        is_source=True,
        is_destination=False,
    )
    click.echo(f"Source connection: {source_conn['name']} (ID: {source_conn['id']})")

    # Create or get destination connection
    dest_conn = datamasque_client.get_or_create_connection(
        name=f"masque_bricks_dest_{config.s3.bucket.replace('-', '_')}",
        bucket=config.s3.bucket,
        base_directory=dest_prefix.rstrip("/"),
        is_source=False,
        is_destination=True,
    )
    click.echo(f"Destination connection: {dest_conn['name']} (ID: {dest_conn['id']})")

    # Create or get ruleset
    ruleset = datamasque_client.get_or_create_ruleset(
        name=ruleset_name,
        config_yaml=ruleset_yaml,
        update_if_exists=True,
    )
    click.echo(f"Ruleset: {ruleset['name']} (ID: {ruleset['id']})")

    # Start masking run
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run = datamasque_client.start_masking_run(
        name=f"masque_bricks_{timestamp}",
        source_connection_id=source_conn["id"],
        destination_connection_id=dest_conn["id"],
        ruleset_id=ruleset["id"],
    )
    click.echo(f"Started masking run: {run['id']}")

    # Wait for completion
    click.echo("Waiting for masking run to complete...")
    try:
        result = datamasque_client.wait_for_run(run["id"], timeout=timeout)
        click.echo(f"Masking run completed successfully!")
        click.echo(f"Masked files written to: {s3_uri(config.s3.bucket, dest_prefix)}")
    except DataMasqueError as e:
        raise click.ClickException(str(e))


@main.command("import")
@click.option("--table", required=True, help="Target table name")
@click.option("--schema", default="default", help="Schema/database for the target table")
@click.option("--catalog", default=None, help="Catalog name (for Unity Catalog)")
@click.option("--source-prefix", required=True, help="S3 prefix containing Parquet files")
@click.option(
    "--mode",
    type=click.Choice(["OVERWRITE", "APPEND"]),
    default="OVERWRITE",
    help="Import mode",
)
@click.pass_context
def import_(ctx, table: str, schema: str, catalog: str | None, source_prefix: str, mode: str):
    """Import masked Parquet from S3 to Databricks (via SQL)."""
    databricks_client, _, _, config = get_clients(ctx.obj.get("config_path"))

    full_schema = qualify_schema(catalog, schema)
    s3_path = s3_uri(config.s3.bucket, source_prefix)

    click.echo(f"Importing from {s3_path} to {full_schema}.{table}...")

    try:
        databricks_client.import_table_from_s3(
            s3_path=s3_path,
            target_table=table,
            schema=full_schema,
            mode=mode,
        )
        click.echo(f"Import completed: {full_schema}.{table}")
    except Exception as e:
        raise click.ClickException(f"Import failed: {e}")


@main.command()
@click.option("--table", required=True, help="Source table name")
@click.option("--schema", default="default", help="Schema/database")
@click.option("--catalog", default=None, help="Catalog name (for Unity Catalog)")
@click.option("--target-table", required=True, help="Target table name for masked data")
@click.option("--target-schema", default=None, help="Target schema (defaults to source schema)")
@click.option(
    "--ruleset-file",
    default="example_rulesets/sample_pii.yaml",
    help="Path to ruleset YAML file",
)
@click.option("--timeout", default=3600, help="Timeout in seconds for masking run")
@click.option("--cleanup/--no-cleanup", default=True, help="Clean up S3 files after import")
@click.pass_context
def run(
    ctx,
    table: str,
    schema: str,
    catalog: str | None,
    target_table: str,
    target_schema: str | None,
    ruleset_file: str,
    timeout: int,
    cleanup: bool,
):
    """Full pipeline: export -> mask -> import."""
    if target_schema is None:
        target_schema = schema

    full_schema = qualify_schema(catalog, schema)
    full_target_schema = qualify_schema(catalog, target_schema)

    databricks_client, s3_client, datamasque_client, config = get_clients(
        ctx.obj.get("config_path")
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_prefix = f"raw/{table}/{timestamp}"
    masked_prefix = f"masked/{table}/{timestamp}"
    raw_s3_path = s3_uri(config.s3.bucket, raw_prefix)
    masked_s3_path = s3_uri(config.s3.bucket, masked_prefix)

    # Step 1: Export (Databricks -> S3)
    click.echo(f"Step 1: Exporting {full_schema}.{table} to S3...")
    click.echo(f"  Destination: {raw_s3_path}")

    try:
        databricks_client.export_table_to_s3(
            table=table,
            schema=full_schema,
            s3_path=raw_s3_path,
        )
        click.echo(f"  Export completed!")
    except Exception as e:
        raise click.ClickException(f"Export failed: {e}")

    # Step 2: Mask (DataMasque)
    click.echo(f"Step 2: Running DataMasque masking...")

    ruleset_path = resolve_ruleset_path(ruleset_file)
    ruleset_yaml = ruleset_path.read_text()
    ruleset_name = ruleset_path.stem

    source_conn = datamasque_client.get_or_create_connection(
        name=f"masque_bricks_source_{config.s3.bucket.replace('-', '_')}",
        bucket=config.s3.bucket,
        base_directory="raw",
        is_source=True,
        is_destination=False,
    )

    dest_conn = datamasque_client.get_or_create_connection(
        name=f"masque_bricks_dest_{config.s3.bucket.replace('-', '_')}",
        bucket=config.s3.bucket,
        base_directory="masked",
        is_source=False,
        is_destination=True,
    )

    ruleset = datamasque_client.get_or_create_ruleset(
        name=ruleset_name,
        config_yaml=ruleset_yaml,
        update_if_exists=True,
    )

    masking_run = datamasque_client.start_masking_run(
        name=f"masque_bricks_{table}_{timestamp}",
        source_connection_id=source_conn["id"],
        destination_connection_id=dest_conn["id"],
        ruleset_id=ruleset["id"],
    )

    click.echo(f"  Started run {masking_run['id']}, waiting for completion...")

    try:
        datamasque_client.wait_for_run(masking_run["id"], timeout=timeout)
        click.echo(f"  Masking completed!")
    except DataMasqueError as e:
        raise click.ClickException(str(e))

    # Step 3: Import (S3 -> Databricks)
    click.echo(f"Step 3: Importing masked data to {full_target_schema}.{target_table}...")

    try:
        databricks_client.import_table_from_s3(
            s3_path=masked_s3_path,
            target_table=target_table,
            schema=full_target_schema,
            mode="OVERWRITE",
        )
        click.echo(f"  Import completed!")
    except Exception as e:
        raise click.ClickException(f"Import failed: {e}")

    # Step 4: Cleanup (optional)
    if cleanup:
        click.echo(f"Step 4: Cleaning up S3 files...")
        raw_deleted = s3_client.delete_prefix(raw_prefix)
        masked_deleted = s3_client.delete_prefix(masked_prefix)
        click.echo(f"  Deleted {raw_deleted + masked_deleted} files")

    click.echo(f"\nPipeline completed successfully!")
    click.echo(f"Masked data available in: {full_target_schema}.{target_table}")


# =============================================================================
# Validation / Debugging Commands
# =============================================================================


@main.command()
@click.pass_context
def check(ctx):
    """Test connections to Databricks, S3, and DataMasque."""
    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)

    click.echo("Checking connections...\n")

    # Check Databricks
    click.echo("1. Databricks")
    click.echo(f"   Host: {config.databricks.host}")
    click.echo(f"   Auth: {config.databricks.auth_type}")
    if config.databricks.auth_type == "oauth":
        click.echo(f"   Client ID: {config.databricks.client_id[:8]}...")
    try:
        databricks_client = DatabricksClient(config.databricks)
        result = databricks_client.execute_sql("SELECT 1 as test")
        click.echo("   Status: OK")
    except Exception as e:
        click.echo(f"   Status: FAILED - {e}")

    # Check S3
    click.echo("\n2. S3")
    click.echo(f"   Bucket: {config.s3.bucket}")
    click.echo(f"   Region: {config.s3.region}")
    try:
        s3_client = S3Client(config.s3)
        # Try to list (even empty results means we have access)
        s3_client.client.head_bucket(Bucket=config.s3.bucket)
        click.echo("   Status: OK")
    except Exception as e:
        click.echo(f"   Status: FAILED - {e}")

    # Check DataMasque
    click.echo("\n3. DataMasque")
    click.echo(f"   Host: {config.datamasque.host}")
    try:
        datamasque_client = DataMasqueClient(config.datamasque)
        # Try to list connections as a health check
        datamasque_client.list_connections()
        click.echo("   Status: OK")
    except Exception as e:
        click.echo(f"   Status: FAILED - {e}")

    click.echo("\nDone.")


@main.command("load-test-data")
@click.option("--table", default="test_pii_data", help="Table name to create")
@click.option("--schema", default="default", help="Schema/database name")
@click.option("--catalog", default=None, help="Catalog name (for Unity Catalog)")
@click.option("--rows", default=100, help="Number of rows to generate")
@click.option("--drop/--no-drop", default=True, help="Drop table if it exists")
@click.pass_context
def load_test_data(ctx, table: str, schema: str, catalog: str | None, rows: int, drop: bool):
    """Generate and load test PII data into a Databricks table.

    Creates a table with columns matching the sample_pii.yaml ruleset:
    first_name, last_name, email, phone, ssn, name
    """
    databricks_client, _, _, _ = get_clients(ctx.obj.get("config_path"))

    full_table = qualify_table(catalog, schema, table)

    # Sample data pools
    first_names = [
        "James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael", "Linda",
        "William", "Elizabeth", "David", "Barbara", "Richard", "Susan", "Joseph", "Jessica",
        "Thomas", "Sarah", "Charles", "Karen", "Emma", "Oliver", "Ava", "Liam", "Sophia",
    ]
    last_names = [
        "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
        "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
        "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Thompson", "White", "Harris",
    ]
    domains = ["gmail.com", "yahoo.com", "outlook.com", "company.com", "example.org"]

    import random
    random.seed(42)  # Reproducible data

    click.echo(f"Generating {rows} rows of test PII data...")

    columns = ["id", "first_name", "last_name", "name", "email", "phone", "ssn"]
    test_rows = []
    for i in range(rows):
        fn = random.choice(first_names)
        ln = random.choice(last_names)
        domain = random.choice(domains)
        email = f"{fn.lower()}.{ln.lower()}{random.randint(1, 999)}@{domain}"
        phone = f"+1-{random.randint(200,999)}-{random.randint(100,999)}-{random.randint(1000,9999)}"
        ssn = f"{random.randint(100,999)}-{random.randint(10,99)}-{random.randint(1000,9999)}"
        test_rows.append((i + 1, fn, ln, f"{fn} {ln}", email, phone, ssn))

    try:
        if drop:
            click.echo(f"Dropping table if exists: {full_table}")
            databricks_client.execute_sql(f"DROP TABLE IF EXISTS {full_table}")

        click.echo(f"Creating table: {full_table}")
        databricks_client.execute_sql(f"""
        CREATE TABLE IF NOT EXISTS {full_table} (
            id INT,
            first_name STRING,
            last_name STRING,
            name STRING,
            email STRING,
            phone STRING,
            ssn STRING
        )
        """)

        click.echo(f"Inserting {rows} rows...")
        databricks_client.insert_rows(full_table, columns, test_rows)

        click.echo(f"\nTest data loaded into {full_table}")
        click.echo(f"Preview with: uv run masque-bricks preview --table {table} --schema {schema}")

    except Exception as e:
        raise click.ClickException(f"Failed to load test data: {e}")


@main.command("list-catalogs")
@click.pass_context
def list_catalogs(ctx):
    """List available catalogs (Unity Catalog)."""
    databricks_client, _, _, _ = get_clients(ctx.obj.get("config_path"))

    click.echo("Catalogs:\n")
    try:
        results = databricks_client.execute_sql("SHOW CATALOGS")
        for row in results:
            click.echo(f"  {row[0]}")
    except Exception as e:
        raise click.ClickException(f"Failed to list catalogs: {e}")


@main.command("list-schemas")
@click.option("--catalog", default=None, help="Catalog name (for Unity Catalog)")
@click.pass_context
def list_schemas(ctx, catalog: str | None):
    """List schemas/databases."""
    databricks_client, _, _, _ = get_clients(ctx.obj.get("config_path"))

    if catalog:
        sql = f"SHOW SCHEMAS IN {catalog}"
        click.echo(f"Schemas in {catalog}:\n")
    else:
        sql = "SHOW SCHEMAS"
        click.echo("Schemas:\n")

    try:
        results = databricks_client.execute_sql(sql)
        for row in results:
            click.echo(f"  {row[0]}")
    except Exception as e:
        raise click.ClickException(f"Failed to list schemas: {e}")


@main.command("list-tables")
@click.option("--schema", default="default", help="Schema/database name")
@click.option("--catalog", default=None, help="Catalog name (for Unity Catalog)")
@click.pass_context
def list_tables(ctx, schema: str, catalog: str | None):
    """List tables in a schema."""
    databricks_client, _, _, _ = get_clients(ctx.obj.get("config_path"))

    full_schema = qualify_schema(catalog, schema)
    click.echo(f"Tables in {full_schema}:\n")

    try:
        results = databricks_client.execute_sql(f"SHOW TABLES IN {full_schema}")
        for row in results:
            # Format: database, tableName, isTemporary
            click.echo(f"  {row[1]}")
    except Exception as e:
        raise click.ClickException(f"Failed to list tables: {e}")


@main.command()
@click.option("--table", required=True, help="Table name")
@click.option("--schema", default="default", help="Schema/database name")
@click.option("--catalog", default=None, help="Catalog name (for Unity Catalog)")
@click.option("--limit", default=10, help="Number of rows to show")
@click.pass_context
def preview(ctx, table: str, schema: str, catalog: str | None, limit: int):
    """Preview rows from a table."""
    databricks_client, _, _, _ = get_clients(ctx.obj.get("config_path"))

    full_table = qualify_table(catalog, schema, table)
    click.echo(f"Preview of {full_table} (limit {limit}):\n")

    try:
        # Get column names
        columns = databricks_client.execute_sql(f"DESCRIBE {full_table}")
        col_names = [row[0] for row in columns if not row[0].startswith("#")]

        # Get data
        results = databricks_client.execute_sql(f"SELECT * FROM {full_table} LIMIT {limit}")

        if not results:
            click.echo("  (empty table)")
            return

        # Calculate column widths
        widths = [len(name) for name in col_names]
        for row in results:
            for i, val in enumerate(row):
                if i < len(widths):
                    widths[i] = max(widths[i], len(str(val)[:50]))

        # Print header
        header = " | ".join(name.ljust(widths[i]) for i, name in enumerate(col_names))
        click.echo(header)
        click.echo("-" * len(header))

        # Print rows
        for row in results:
            row_str = " | ".join(str(val)[:50].ljust(widths[i]) for i, val in enumerate(row))
            click.echo(row_str)

    except Exception as e:
        raise click.ClickException(f"Failed to preview table: {e}")


@main.command()
@click.option("--table", required=True, help="Table name")
@click.option("--schema", default="default", help="Schema/database name")
@click.option("--catalog", default=None, help="Catalog name (for Unity Catalog)")
@click.pass_context
def describe(ctx, table: str, schema: str, catalog: str | None):
    """Show table schema (columns and types)."""
    databricks_client, _, _, _ = get_clients(ctx.obj.get("config_path"))

    full_table = qualify_table(catalog, schema, table)
    click.echo(f"Schema of {full_table}:\n")

    try:
        results = databricks_client.execute_sql(f"DESCRIBE {full_table}")

        click.echo(f"{'Column':<30} {'Type':<20} {'Comment':<30}")
        click.echo("-" * 80)

        for row in results:
            if row[0].startswith("#") or not row[0]:
                continue
            col_name = row[0][:30]
            col_type = (row[1] or "")[:20]
            comment = (row[2] or "")[:30] if len(row) > 2 else ""
            click.echo(f"{col_name:<30} {col_type:<20} {comment:<30}")

    except Exception as e:
        raise click.ClickException(f"Failed to describe table: {e}")


@main.command()
@click.argument("sql")
@click.pass_context
def query(ctx, sql: str):
    """Run a SQL query and show results."""
    databricks_client, _, _, _ = get_clients(ctx.obj.get("config_path"))

    try:
        results = databricks_client.execute_sql(sql)

        if not results:
            click.echo("Query executed successfully. No results returned.")
            return

        # Print results
        for row in results:
            click.echo(" | ".join(str(val) for val in row))

    except Exception as e:
        raise click.ClickException(f"Query failed: {e}")


@main.command("list-s3")
@click.option("--prefix", default="", help="S3 prefix to list")
@click.pass_context
def list_s3(ctx, prefix: str):
    """List files in the S3 bucket."""
    _, s3_client, _, config = get_clients(ctx.obj.get("config_path"))

    click.echo(f"Files in {s3_uri(config.s3.bucket, prefix)}\n")

    try:
        files = s3_client.list_files(prefix)

        if not files:
            click.echo("  (no files)")
            return

        for f in files:
            click.echo(f"  {f}")

    except Exception as e:
        raise click.ClickException(f"Failed to list S3 files: {e}")


# =============================================================================
# DataMasque Management Commands
# =============================================================================


@main.command("list-connections")
@click.pass_context
def list_connections(ctx):
    """List DataMasque connections."""
    _, _, datamasque_client, _ = get_clients(ctx.obj.get("config_path"))

    click.echo("DataMasque Connections:\n")

    try:
        connections = datamasque_client.list_connections()

        if not connections:
            click.echo("  (no connections)")
            return

        for conn in connections:
            conn_type = conn.get("type", "unknown")
            name = conn.get("name", "unnamed")
            conn_id = conn.get("id", "?")
            is_source = conn.get("is_file_mask_source", False)
            is_dest = conn.get("is_file_mask_destination", False)
            flags = []
            if is_source:
                flags.append("source")
            if is_dest:
                flags.append("dest")
            flag_str = f" [{', '.join(flags)}]" if flags else ""

            click.echo(f"  {name} ({conn_type}) - ID: {conn_id}{flag_str}")
            if conn_type == "s3_connection":
                bucket = conn.get("bucket", "")
                base_dir = conn.get("base_directory", "")
                click.echo(f"    {s3_uri(bucket, base_dir)}")

    except Exception as e:
        raise click.ClickException(f"Failed to list connections: {e}")


@main.command("create-connection")
@click.option("--name", required=True, help="Connection name")
@click.option("--bucket", default=None, help="S3 bucket (defaults to config bucket)")
@click.option("--base-directory", required=True, help="Base directory/prefix in the bucket")
@click.option("--source/--no-source", default=False, help="Can be used as file mask source")
@click.option("--destination/--no-destination", default=False, help="Can be used as file mask destination")
@click.option("--iam-role-arn", default=None, help="IAM role ARN for cross-account access")
@click.pass_context
def create_connection(
    ctx,
    name: str,
    bucket: str | None,
    base_directory: str,
    source: bool,
    destination: bool,
    iam_role_arn: str | None,
):
    """Create an S3 file masking connection in DataMasque."""
    _, _, datamasque_client, config = get_clients(ctx.obj.get("config_path"))

    if not bucket:
        bucket = config.s3.bucket

    if not source and not destination:
        raise click.ClickException("Connection must be --source and/or --destination")

    click.echo(f"Creating S3 connection '{name}'...")
    click.echo(f"  Bucket: {bucket}")
    click.echo(f"  Base directory: {base_directory}")
    click.echo(f"  Source: {source}, Destination: {destination}")

    try:
        conn = datamasque_client.create_s3_connection(
            name=name,
            bucket=bucket,
            base_directory=base_directory,
            is_source=source,
            is_destination=destination,
            iam_role_arn=iam_role_arn,
        )
        click.echo(f"\nCreated connection: {conn['name']} (ID: {conn['id']})")

    except DataMasqueError as e:
        raise click.ClickException(f"Failed to create connection: {e}")


@main.command("list-rulesets")
@click.pass_context
def list_rulesets(ctx):
    """List DataMasque rulesets."""
    _, _, datamasque_client, _ = get_clients(ctx.obj.get("config_path"))

    click.echo("DataMasque Rulesets:\n")

    try:
        rulesets = datamasque_client.list_rulesets()

        if not rulesets:
            click.echo("  (no rulesets)")
            return

        for rs in rulesets:
            name = rs.get("name", "unnamed")
            rs_id = rs.get("id", "?")
            click.echo(f"  {name} - ID: {rs_id}")

    except Exception as e:
        raise click.ClickException(f"Failed to list rulesets: {e}")


@main.command("create-ruleset")
@click.option("--name", required=True, help="Ruleset name")
@click.option("--file", "ruleset_file", required=True, type=click.Path(exists=True), help="Path to ruleset YAML file")
@click.option("--update/--no-update", default=False, help="Update if ruleset already exists")
@click.pass_context
def create_ruleset(ctx, name: str, ruleset_file: str, update: bool):
    """Create or update a ruleset in DataMasque."""
    _, _, datamasque_client, _ = get_clients(ctx.obj.get("config_path"))

    ruleset_path = Path(ruleset_file)
    ruleset_yaml = ruleset_path.read_text()

    click.echo(f"Creating ruleset '{name}' from {ruleset_file}...")

    try:
        ruleset = datamasque_client.get_or_create_ruleset(
            name=name,
            config_yaml=ruleset_yaml,
            update_if_exists=update,
        )
        click.echo(f"Ruleset: {ruleset['name']} (ID: {ruleset['id']})")

    except DataMasqueError as e:
        raise click.ClickException(f"Failed to create ruleset: {e}")


if __name__ == "__main__":
    main()
