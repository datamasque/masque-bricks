# Manual Workflow: Masking Databricks Tables via DataMasque File Masking

This is the manual version of what [`masque-bricks`](../README.md) automates: export a Databricks table to S3 as Parquet, run DataMasque file masking, then import the masked Parquet back into Databricks. Use it to set up the prerequisites the CLI assumes are in place, to debug a CLI run, or to run the workflow without the CLI at all.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Databricks │────▶│  S3 (raw)   │────▶│ DataMasque  │────▶│ S3 (masked) │
│    Table    │     │   Parquet   │     │ File Masking│     │   Parquet   │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
                                                                    │
                                                                    ▼
                                                            ┌─────────────┐
                                                            │  Databricks │
                                                            │ Masked Table│
                                                            └─────────────┘
```

## Prerequisites

- Databricks workspace with SQL Warehouse access
- DataMasque instance with file masking enabled
- S3 bucket accessible by both Databricks and DataMasque
- IAM roles configured (see Step 1)

## Step 1: Configure S3 Access for Databricks

Databricks needs read/write access to your S3 bucket. This requires:

1. An **IAM Role** for Databricks with S3 access
2. An **External Location** in Databricks Unity Catalog pointing to the S3 bucket

References:

- [Configure S3 access with instance profiles](https://docs.databricks.com/en/connect/storage/tutorial-s3-instance-profile.html)
- [Create an external location in Unity Catalog](https://docs.databricks.com/en/sql/language-manual/sql-ref-syntax-ddl-create-location.html)
- [Manage external locations](https://docs.databricks.com/en/connect/unity-catalog/external-locations.html)

Example IAM policy for the Databricks role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::your-bucket-name",
        "arn:aws:s3:::your-bucket-name/*"
      ]
    }
  ]
}
```

## Step 2: Export the Databricks Table to S3

```sql
-- Create a temporary external table that writes to S3
CREATE TABLE your_schema._export_temp
USING PARQUET
LOCATION 's3://your-bucket/raw/your_table/'
AS SELECT * FROM your_schema.your_table;

-- Drop the table reference (the data remains in S3)
DROP TABLE IF EXISTS your_schema._export_temp;
```

The `raw/` prefix is a convention for unmasked data. DataMasque will read these Parquet files in step 5.

## Step 3: Create a DataMasque Source Connection

Point DataMasque at the directory of exported Parquet files.

**Via the DataMasque UI:**

1. **Connections** → **Add Connection**
2. Choose **S3 Connection**
3. Configure:
   - **Name**: e.g. `databricks_source`
   - **Bucket**: your S3 bucket
   - **Base Directory**: `raw`
   - **Is File Mask Source**: ✓
   - **IAM Role ARN**: (only for cross-account access)

**Via the API:**

```bash
curl -X POST "https://your-datamasque/api/connections/" \
  -H "Authorization: Token YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "version": "1.0",
    "name": "databricks_source",
    "type": "s3_connection",
    "mask_type": "file",
    "bucket": "your-bucket",
    "base_directory": "raw",
    "is_file_mask_source": true,
    "is_file_mask_destination": false
  }'
```

## Step 4: Create a DataMasque Destination Connection

Point DataMasque at where it should write masked output.

**Via the DataMasque UI:**

1. **Connections** → **Add Connection**
2. Choose **S3 Connection**
3. Configure:
   - **Name**: e.g. `databricks_destination`
   - **Bucket**: same bucket is fine
   - **Base Directory**: `masked` (must differ from source)
   - **Is File Mask Destination**: ✓

**Via the API:**

```bash
curl -X POST "https://your-datamasque/api/connections/" \
  -H "Authorization: Token YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "version": "1.0",
    "name": "databricks_destination",
    "type": "s3_connection",
    "mask_type": "file",
    "bucket": "your-bucket",
    "base_directory": "masked",
    "is_file_mask_source": false,
    "is_file_mask_destination": true
  }'
```

## Step 5: Create or Generate a Ruleset

**Option A — Auto-generate:** Have DataMasque scan the source files and propose masks via **Rulesets** → **Generate File Ruleset** → select your source connection.

**Option B — Hand-write a ruleset:**

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
            value: "MASKED"
```

## Step 6: Run the Masking Job

**Via the DataMasque UI:**

1. **Runs** → **New Run**
2. Select **Mask Type**: File, your source/destination connections, and the ruleset
3. **Start Run** and monitor until complete

**Via the API:**

```bash
# Start
curl -X POST "https://your-datamasque/api/runs/" \
  -H "Authorization: Token YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Databricks PII Masking",
    "mask_type": "file",
    "source_connection": "SOURCE_CONNECTION_ID",
    "destination_connection": "DEST_CONNECTION_ID",
    "ruleset": RULESET_ID,
    "options": {}
  }'

# Poll
curl "https://your-datamasque/api/runs/RUN_ID/" \
  -H "Authorization: Token YOUR_API_TOKEN"
```

## Step 7: Import Masked Data Back into Databricks

```sql
-- Drop existing masked table if replacing
DROP TABLE IF EXISTS your_schema.your_table_masked;

-- Create table from masked S3 location
CREATE TABLE your_schema.your_table_masked
USING PARQUET
LOCATION 's3://your-bucket/masked/your_table/';

-- Refresh so all files are picked up
REFRESH TABLE your_schema.your_table_masked;
```

Verify:

```sql
SELECT * FROM your_schema.your_table_masked LIMIT 10;
```

## Doing It with masque-bricks

The [`masque-bricks` CLI](../README.md) collapses everything above into one command:

```bash
masque-bricks run \
  --table source_table \
  --schema default \
  --target-table masked_table \
  --ruleset-file example_rulesets/sample_pii.yaml
```

`export`, `mask`, and `import` are also available as separate commands. The README covers installation and configuration.

## Related Resources

- [DataMasque file masking documentation](https://docs.datamasque.com/)
- [Databricks external locations](https://docs.databricks.com/en/connect/unity-catalog/external-locations.html)
- [AWS S3 IAM policies](https://docs.aws.amazon.com/AmazonS3/latest/userguide/example-policies-s3.html)
