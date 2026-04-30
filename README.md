# masque-bricks

CLI tool to orchestrate Databricks table masking via DataMasque file masking.

## Overview

masque-bricks does three things:
1. Exports a Databricks table to Parquet in S3
2. Runs DataMasque file masking on those files
3. Imports the masked Parquet back into Databricks

## Prerequisites

- Databricks workspace with SQL Warehouse access
- DataMasque instance with file masking enabled
- S3 bucket accessible by both Databricks and DataMasque, with IAM configured per [docs/manual-workflow.md → Step 1](docs/manual-workflow.md#step-1-configure-s3-access-for-databricks)

## Installation

With [uv](https://docs.astral.sh/uv/) there's no separate install step. `uv run` syncs dependencies on the first run:

```bash
git clone git@github.com:datamasque/masque-bricks.git
cd masque-bricks

uv run masque-bricks --help
```

Or with pip:

```bash
pip install -e .
masque-bricks --help
```

## Configuration

Copy the example file and fill in your credentials:

```bash
cp config.example.yaml config.yaml
```

The CLI searches `--config` / `-c`, then `./config.yaml`, then `~/.config/masque-bricks/config.yaml`.

Databricks supports either a Personal Access Token or OAuth M2M (Service Principal). Pick one and put those fields under `databricks:`:

```yaml
databricks:
  host: https://myworkspace.cloud.databricks.com
  http_path: /sql/1.0/warehouses/abc123
  # Option 1 — Personal Access Token:
  token: <your-databricks-pat>
  # Option 2 — OAuth M2M:
  # client_id: <your-service-principal-client-id>
  # client_secret: <your-service-principal-secret>

datamasque:
  host: https://datamasque.example.com
  username: admin
  password: <your-datamasque-password>

s3:
  bucket: my-masking-bucket
  region: us-east-1  # defaults to us-east-1
```

Any field can be overridden via environment variable: 
- `DATABRICKS_HOST`
- `DATABRICKS_HTTP_PATH`
- `DATABRICKS_TOKEN`
- `DATABRICKS_CLIENT_ID`
- `DATABRICKS_CLIENT_SECRET`
- `DATAMASQUE_HOST`
- `DATAMASQUE_USERNAME`
- `DATAMASQUE_PASSWORD`
- `S3_BUCKET`
- `AWS_REGION`

AWS credentials for S3 follow the standard chain (env vars, `~/.aws/credentials`, or IAM role).

Verify with:

```bash
uv run masque-bricks check
```

## Usage

### Full Pipeline

Run the export → mask → import pipeline:

```bash
masque-bricks run \
  --table users \
  --schema default \
  --target-table users_masked \
  --ruleset-file example_rulesets/sample_pii.yaml
```

### Using a Custom Config File

```bash
masque-bricks --config /path/to/config.yaml run \
  --table users \
  --target-table users_masked
```

### Individual Steps

#### Export a table to S3

```bash
masque-bricks export \
  --table users \
  --schema default \
  --output-prefix raw/users/
```

#### Run DataMasque masking

```bash
masque-bricks mask \
  --source-prefix raw/users/ \
  --dest-prefix masked/users/ \
  --ruleset-file example_rulesets/sample_pii.yaml
```

#### Import masked data back to Databricks

```bash
masque-bricks import \
  --table users_masked \
  --schema default \
  --source-prefix masked/users/
```

## Rulesets

Rulesets define how data should be masked. See `example_rulesets/sample_pii.yaml` for an example:

```yaml
version: "1.0"
tasks:
  - type: mask_tabular_file
    recurse: true
    include:
      - glob: "*.parquet"
    rules:
      - column: first_name
        masks:
          - type: from_fixed
            value: MASKED
```

Refer to the [DataMasque Ruleset documentation](https://www.datamasque.com/portal/documentation/latest/rulesets.html)
for the full list of available masking types.

## CLI Reference

```
Usage: masque-bricks [OPTIONS] COMMAND [ARGS]...

  masque-bricks: Databricks table masking via DataMasque.

Options:
  --version          Show the version and exit.
  -c, --config PATH  Path to config YAML file
  --help             Show this message and exit.

Commands:
  # Validation / Debugging
  check               Test connections to Databricks, S3, and DataMasque.
  list-catalogs       List available catalogs (Unity Catalog).
  list-schemas        List schemas/databases.
  list-tables         List tables in a schema.
  describe            Show table schema (columns and types).
  preview             Preview rows from a table.
  query               Run a SQL query and show results.
  list-s3             List files in the S3 bucket.
  load-test-data      Generate and load sample PII data into a Databricks table.

  # DataMasque Management
  list-connections    List DataMasque connections.
  create-connection   Create an S3 file masking connection in DataMasque.
  list-rulesets       List DataMasque rulesets.
  create-ruleset      Create or update a ruleset in DataMasque.

  # Masking Pipeline
  export              Export a Databricks table to S3 as Parquet.
  mask                Run DataMasque file masking on S3 data.
  import              Import masked Parquet from S3 to Databricks.
  run                 Full pipeline: export -> mask -> import.
```

## How It Works

[docs/manual-workflow.md](docs/manual-workflow.md) has the architecture diagram and walks through the SQL the CLI runs and the DataMasque API calls it makes. Read it to debug a run or to do the masking by hand.

## License

Proprietary - DataMasque
