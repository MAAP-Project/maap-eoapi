# MAAP eoapi

[![Tests Status](https://github.com/MAAP-Project/maap-eoapi/actions/workflows/tests.yml/badge.svg)]((https://github.com/MAAP-Project/maap-eoapi/actions?query=workflow:tests))

## Overview

This repository contains the AWS CDK code (written in Python) used to deploy the MAAP project eoapi infrastructure. It is based on the [eoapi-template example](https://github.com/developmentseed/eoapi-template). For the MAAP use case, we use a subset of the eoapi CDK constructs to define a database, an ingestion API, a STAC API, a raster API (i.e a tiling API) and a pgbouncer instance to manage connections to the database. Here, we deploy all these components into a custom VPC.

## Automated Deployment

Deployment happens through a github workflow manually triggered and defined in `.github/workflows/deploy.yaml`.

## User STAC catalogs and transactions

The MAAP-owned STAC runtime uses `stac-fastapi-pgstac[catalogs]` 6.3.0. Read-only multi-tenant catalog routes are enabled by default for deployed STAC APIs. Catalog write routes and collection write routes remain explicit opt-ins.

User STAC catalog configuration:

- `USER_STAC_CATALOGS_ENABLED=false` disables read-only `/catalogs` routes.
- `USER_STAC_CATALOGS_HIDE_ALTERNATE_PARENTS=true` hides alternate parent links in catalog responses.
- `USER_STAC_CATALOG_TRANSACTIONS_AUTH_MODE=basic` enables catalog write routes and selects the supported auth mode. Catalog write routes require catalogs to stay enabled.
- `USER_STAC_CATALOG_TRANSACTIONS_AUTH_SECRET_ARN` can point at an existing auth secret.

## DPS-generated STAC items

The DPS item generator assigns unregistered items to collections named
`{username}__{algorithm_name}__{algorithm_version}`. Authorized user-supplied
collection IDs are preserved. Generated items include the filterable
`maap-dps:algorithm_name`, `processing:version`, `maap-dps:username`,
and `maap-dps:tag` properties, the MAAP DPS STAC extension, and a `dps-metadata`
asset containing the source `.met.json` file. The generator also overwrites the
STAC Common Metadata `created` property with the UTC publication time shared by
all Items generated from that catalog.

To merge legacy tag-specific DPS collections into these tag-free IDs, preview
then apply the database migration:

```bash
./scripts/migrate_dps_collection_ids.py --dry-run
./scripts/migrate_dps_collection_ids.py --apply
```

It recognizes four-part IDs (`username__algorithm__version__tag`), merges their
items into the corresponding three-part ID, and adds the DPS metadata fields
from the legacy ID. Collections containing an item-ID collision after merging
are reported and left unchanged. For a deployed database, follow the
[RDS connection guide](#connect-to-rds-through-an-ssm-tunnel) below and the
RDS usage instructions in the migration script's docstring.

Collection-only STAC transactions can still be enabled with:

- `USER_STAC_COLLECTION_TRANSACTIONS_AUTH_MODE=basic`

When either collection or catalog transactions are enabled, this CDK stack creates and manages the Secrets Manager secret used for STAC basic auth by default, grants the STAC Lambda read access to it, and publishes the secret ARN to SSM at:

- `/maap-eoapi/<stage>/internal/stac-collection-transaction-auth-secret-arn`

You can still override the secret with `USER_STAC_COLLECTION_TRANSACTIONS_AUTH_SECRET_ARN` or `USER_STAC_CATALOG_TRANSACTIONS_AUTH_SECRET_ARN` if you need to point at an existing secret instead. If both write surfaces are enabled, they must use the same secret in this iteration.

The transaction auth secret must be a JSON object with string `username` and `password` fields.

### Local demo data

After starting the local pgSTAC database, you can load a small demo catalog hierarchy for sample users:

```bash
docker compose up -d database
./scripts/load_demo_stac_catalogs.py
```

The script is standalone and uses an inline `uv` execution header, so it installs `pypgstac[psycopg]` on demand. By default it connects to the local compose database on `127.0.0.1:5439` and creates:

- `DPS User Catalogs` as a root catalog, containing per-user catalogs for `hrodmn` and `jjfrench`
- `DPS Team Catalogs` as a root catalog, containing the shared `maap-demo-team` catalog
- two synthetic DPS-output collections per user

Useful options:

```bash
./scripts/load_demo_stac_catalogs.py --dry-run
./scripts/load_demo_stac_catalogs.py --reset  # deletes all existing catalog and collection records first
./scripts/load_demo_stac_catalogs.py --user hrodmn --user jjfrench
./scripts/load_demo_stac_catalogs.py --database-url postgresql://username:password@database:5432/postgis
```

The `database` hostname form is useful when running the script from a container attached to the `maap-eoapi` Docker network.

### What to verify after deployment

For a catalogs-enabled deployment, verify:

- OpenAPI includes read-only catalog routes such as `GET /catalogs`, `GET /catalogs/{catalog_id}`, and catalog-scoped collection/item reads.
- `GET /` includes `rel="child"` links for listed catalogs so STAC Browser can discover catalog roots.
- catalog write routes are absent unless `USER_STAC_CATALOG_TRANSACTIONS_AUTH_MODE=basic` is configured.

For a transaction-enabled internal deployment, verify:

- `GET /conformance` includes `https://api.stacspec.org/v1.0.0/collections/extensions/transaction` when collection transactions are enabled.
- OpenAPI advertises collection write routes only for collection transactions:
  - `POST /collections`
  - `PUT /collections/{collection_id}`
  - `PATCH /collections/{collection_id}`
  - `DELETE /collections/{collection_id}`
- OpenAPI advertises catalog write routes only for catalog transactions, including `POST /catalogs` and `PUT`/`DELETE /catalogs/{catalog_id}`.
- unauthenticated writes return `401`
- authenticated writes succeed
- item write routes are absent from the contract and return `404` or `405` rather than exposing item transaction behavior

## Networking and accessibility of the database

Because of security requirements, the networking set up imposes the following constraints :

- For security reasons, the database is in a _private_ subnet of the VPC. As such, only instances running inside of the same VPC can access the database. This means that, for example, even if a user has the password and her IP is allowed inbound connections to the database, access will _not_ be allowed.

This has three consequences :

1. The APIs that need access to the database (the STAC API, the tiling API, the ingestion API) need to be deployed in that same VPC.
2. In addition, because these APIs _also_ sometimes need access to the internet, a NAT gateway must in addition be deployed in that VPC.
3. For direct, administrative connections to the database, one _must_ go through an instance placed in the same VPC as the database.

### Connect to RDS through an SSM tunnel

For administrative database access, use the existing PgBouncer EC2 instance
as an SSM network relay and run your database client locally. Forward to the
**RDS endpoint**, not the PgBouncer service, to bypass connection pooling. RDS stays
private, and you do not need to install dependencies on the EC2 instance or
open inbound ports.

You need the AWS CLI, `jq`, `curl`, and a PostgreSQL client such as `psql`
on your workstation. Also
[install the Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html);
it is a separate installation from the AWS CLI.
Configure your AWS profile and region first. Your AWS
identity needs permission to read the stack resources, SSM parameter, and
database secret (including KMS decryption if applicable), and start sessions
using `AWS-StartPortForwardingSessionToRemoteHost`. The EC2 instance must be
SSM-managed with SSM Agent 3.1.1374.0 or later.

#### Find the database secret

Choose the deployment you want to connect to:

| Database | Stack name | SSM parameter type |
| --- | --- | --- |
| User STAC (including DPS outputs) | `MAAP-STAC-<stage>-userSTAC` | `internal` |
| Public STAC | `MAAP-STAC-<stage>-pgSTAC` | `public` |

The examples use userSTAC. Confirm your account and stage, then list the
secrets belonging to that CDK deployment:

```bash
aws sts get-caller-identity
STAGE=test  # change as appropriate
STACK="MAAP-STAC-${STAGE}-userSTAC"  # userSTAC or pgSTAC

aws cloudformation list-stack-resources \
  --stack-name "$STACK" \
  --query 'StackResourceSummaries[?ResourceType==`AWS::SecretsManager::Secret`].[LogicalResourceId,PhysicalResourceId]' \
  --output table
```

You can also find these under **CloudFormation → stack → Resources**. 

Select the database secret whose ID contains `pgstacdbbootstrappersecret`, not the
STAC HTTP basic-auth secret. CloudFormation gives you the secret's identifier; retrieve its value
from Secrets Manager. In the same terminal:

```bash
SECRET_ID='<database secret physical ID (not arn) from the table>'
DB_SECRET=$(aws secretsmanager get-secret-value \
  --secret-id "$SECRET_ID" --query SecretString --output text)

export PGHOST=$(jq -er '.host' <<< "$DB_SECRET")
export PGDATABASE=$(jq -er '.dbname' <<< "$DB_SECRET")
export PGUSER=$(jq -er '.username' <<< "$DB_SECRET")
export PGPASSWORD=$(jq -er '.password' <<< "$DB_SECRET")
unset DB_SECRET
```

Check that these commands succeed and that `PGHOST` matches the selected RDS
endpoint. Do not print the secret or run these commands with shell tracing
(`set -x`) enabled.

#### Start the tunnel

In a second terminal with the same AWS profile and region, retrieve the RDS
endpoint from the same secret and start the session. Variables set in the first
terminal are not available in this terminal:

```bash
STAGE=test  # use the same stage as above
TYPE=internal  # use public for the pgSTAC stack
SECRET_ID='<same database secret physical ID from the table>'
RDS_HOST=$(aws secretsmanager get-secret-value \
  --secret-id "$SECRET_ID" --query SecretString --output text | jq -er '.host')
INSTANCE_ID=$(aws ssm get-parameter \
  --name "/maap-eoapi/$STAGE/$TYPE/pgbouncer-instance-id" \
  --query Parameter.Value --output text)

aws ssm start-session \
  --target "$INSTANCE_ID" \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters "{\"host\":[\"$RDS_HOST\"],\"portNumber\":[\"5432\"],\"localPortNumber\":[\"15432\"]}"
```

Leave this terminal open while you use the database. The EC2 host needs
network access to RDS on port 5432, as it does for normal PgBouncer traffic.

#### Connect with a local client

Back in the first terminal, download the
[AWS RDS CA bundle](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html)
and configure TLS. `PGHOSTADDR` sends the connection through localhost while
`PGHOST` retains the RDS hostname for certificate verification:

```bash
curl --fail --show-error --silent \
  https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
  --output /tmp/maap-rds-global-bundle.pem

export PGHOSTADDR=127.0.0.1
export PGPORT=15432
export PGSSLMODE=verify-full
export PGSSLROOTCERT=/tmp/maap-rds-global-bundle.pem

psql -c 'SELECT current_database(), current_user;'
psql
```

`psql` and other libpq-based clients, including psycopg, can use these `PG*`
environment variables. A client's explicit connection string can override
them; check the tool's connection options before running commands.

Use `\q` to leave `psql`. When finished, clear the connection variables and
close the SSM session in the second terminal:

```bash
unset PGPASSWORD PGHOST PGHOSTADDR PGPORT PGDATABASE PGUSER PGSSLMODE PGSSLROOTCERT
```

Before destructive operations, confirm the target database and ensure you have
a recoverable backup. For work expected to run for hours, prefer a durable
in-VPC execution environment over a workstation tunnel.

## Ingestion

The term "ingestion" refers to the process of cataloging data in the STAC catalog associated with this deployment.

### Direct ingestion

For a small record ingestion (for example a collection record or just one item), one can directly connect to the database and perform loading. This can be done using the `pypgstac` library. For example, to load an item stored locally in `test_item.json`, with `pypgstac` installed, you can run the following command :

```shell
pypgstac load --table items test_item.json
```

or for a collection

```shell
pypgstac load --table collections test_collection.json
```

### Indirect ingestion through the ingestion pipeline deployment

For larger scale ingestions, in MAAP we rely on [a fork of the stactools-pipelines repository](https://github.com/MAAP-Project/stactools-pipelines/tree/non-standard-inventory). If you want to ingest a collection in MAAP using this tool, you should develop a 'pipeline'. Details of this procedure can be found in the linked repository. You can follow an example that [was developed for maap here](https://github.com/MAAP-Project/stactools-pipelines/tree/non-standard-inventory/stactools_pipelines/pipelines/nisar-sim).
