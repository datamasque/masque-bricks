# masque-bricks — out-of-place Delta masking for Databricks

> **Reference blueprint — adapt to your environment.** This repository is a
> starting point, not a turnkey product. Review and harden IAM, networking,
> secrets, and TLS for your own environment before any production use.

[DataMasque](https://datamasque.com) replaces sensitive production data with
synthetically identical customer data so teams can build, test, and analyse against
non-production data without exposing PII. **masque-bricks** is a small CLI that
applies that to Databricks: it exports a Databricks table to Parquet in S3, runs
DataMasque file masking over those files, and imports the masked Parquet back
into a new Databricks table — an *out-of-place* pipeline that leaves the source
table untouched and produces a separate masked copy.

**Learn more:** [datamasque.com](https://datamasque.com) ·
[Product docs](https://datamasque.com/portal/documentation/) ·
[Book a demo](https://datamasque.com/request-a-demo)

---

## Which approach should I use?

**Native Databricks masking is the recommended default.** DataMasque can mask
Databricks data *in place* using a SQL Warehouse — covering Delta tables and
Lakebase — without copying data out to S3 and back. If your tables fit the
in-place model, prefer it: fewer moving parts, no S3 round-trip, no second copy
to manage. See the DataMasque
[native Databricks masking docs](https://datamasque.com/portal/documentation/latest/databricks.html)
for setup.

**masque-bricks is the retained out-of-place option.** It is *not* deprecated.
Use it when in-place masking does not fit your case — for example when you need
an out-of-place workflow that masks a source table into a distinct masked copy,
when you are masking via DataMasque's file-masking engine and Parquet on S3, or
when operational constraints (separation of source and target, S3-based review of
masked output, file-format-specific masking) make the export → mask → import flow
the better match. masque-bricks is a supported, distinct option that sits
alongside native masking rather than being superseded by it.

---

## Overview

masque-bricks does three things:

1. Exports a Databricks table to Parquet in S3 (`raw/<table>/<timestamp>/`)
2. Runs DataMasque file masking on those files (output to `masked/<table>/<timestamp>/`)
3. Imports the masked Parquet back into Databricks as a new table

## Prerequisites

- Databricks workspace with SQL Warehouse access
- DataMasque instance with file masking enabled
- S3 bucket accessible by both Databricks and DataMasque, with IAM configured per
  [docs/manual-workflow.md → Step 1](docs/manual-workflow.md#step-1-configure-s3-access-for-databricks)

## Installation

With [uv](https://docs.astral.sh/uv/) there's no separate install step. `uv run`
syncs dependencies on the first run:

```bash
git clone https://github.com/datamasque/masque-bricks.git
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

The CLI searches `--config` / `-c`, then `./config.yaml`, then
`~/.config/masque-bricks/config.yaml`.

Databricks supports either a Personal Access Token or OAuth M2M (Service
Principal). Pick one and put those fields under `databricks:`:

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
  username: <your-datamasque-username>
  password: <your-datamasque-password>
  # TLS verification is on by default. Set false ONLY for an instance with a
  # self-signed certificate you trust:
  # verify_ssl: false

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
- `DATAMASQUE_VERIFY_SSL` (`true`/`false`; default `true`)
- `S3_BUCKET`
- `AWS_REGION`

> **TLS:** `verify_ssl` defaults to `true`. Only disable it for a DataMasque
> instance presenting a self-signed certificate you control, and prefer
> installing the CA certificate over disabling verification.

AWS credentials for S3 follow the standard chain (env vars,
`~/.aws/credentials`, or IAM role).

Verify with:

```bash
uv run masque-bricks check
```

## Usage

### Full Pipeline

Run the export → mask → import pipeline. The source table is read unchanged and a
separate masked table is written:

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

Rulesets define how data should be masked. The bundled
`example_rulesets/sample_pii.yaml` masks **every** PII column produced by
`masque-bricks load-test-data` (`first_name`, `last_name`, `name`, `email`,
`phone`, `ssn`) so the demo never leaves SSNs or emails in the masked output —
adapt it to your own columns:

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
          - type: from_file            # built-in seed of realistic names
            seed_file: DataMasque_firstNames_mixed.csv
            seed_column: firstname-mixed
      - column: ssn
        masks:
          - type: social_security_number
      # ... last_name, name, email, phone
```

Refer to the
[DataMasque ruleset documentation](https://datamasque.com/portal/documentation/)
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

[docs/manual-workflow.md](docs/manual-workflow.md) has the architecture diagram
and walks through the SQL the CLI runs and the DataMasque API calls it makes.
Read it to debug a run or to do the masking by hand. The `run` pipeline keeps the
exported (`raw/…`) and masked (`masked/…`) paths aligned by sub-path so the import
step reads back exactly what masking produced.

## License

Apache 2.0 — see [LICENSE](LICENSE).

---

## Related DataMasque blueprints

- [AWS RDS masking (Step Functions)](https://github.com/datamasque/DataMasque-AWS-RDS-masking-stepfunctions-blueprint)
- [Azure DB masking (Logic Apps)](https://github.com/datamasque/DataMasque-Azure-DB-masking-logicapps-blueprint)
- [AWS Service Catalog DB provisioning](https://github.com/datamasque/DataMasque-AWS-service-catalog-database-provisioning-blueprint)
- [AWS Cross-Account Bucket Access](https://github.com/datamasque/DataMasque-AWS-Cross-Account-Bucket-Access)
- [AWS ECS Deployment](https://github.com/datamasque/DataMasque-AWS-ECS-Deployment)
