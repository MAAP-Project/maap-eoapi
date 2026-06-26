# MAAP eoapi

[![Tests Status](https://github.com/MAAP-Project/maap-eoapi/actions/workflows/tests.yml/badge.svg)]((https://github.com/MAAP-Project/maap-eoapi/actions?query=workflow:tests))


## Overview

This repository contains the AWS CDK code (written in typescript) used to deploy the MAAP project eoapi infrastructure. It is based on the [eoapi-template example](https://github.com/developmentseed/eoapi-template). For the MAAP use case, we use a subset of the eoapi CDK constructs to define a database, an ingestion API, a STAC API, a raster API (i.e a tiling API) and a pgbouncer instance to manage connections to the database. Here, we deploy all these components into a custom VPC.


## Automated Deployment

Deployment happens through a github workflow manually triggered and defined in `.github/workflows/deploy.yaml`.

## User STAC catalogs and transactions

The MAAP-owned STAC runtime uses `stac-fastapi-pgstac[catalogs]` 6.3.0. Read-only multi-tenant catalog routes are enabled by default for deployed STAC APIs. Catalog write routes and collection write routes remain explicit opt-ins.

User STAC catalog configuration:

- `USER_STAC_CATALOGS_ENABLED=false` disables read-only `/catalogs` routes.
- `USER_STAC_CATALOGS_HIDE_ALTERNATE_PARENTS=true` hides alternate parent links in catalog responses.
- `USER_STAC_CATALOG_TRANSACTIONS_ENABLED=true` enables catalog write routes. This requires catalogs to stay enabled.
- `USER_STAC_CATALOG_TRANSACTIONS_AUTH_MODE=basic` selects the supported auth mode.
- `USER_STAC_CATALOG_TRANSACTIONS_AUTH_SECRET_ARN` can point at an existing auth secret.

Collection-only STAC transactions can still be enabled with:

- `USER_STAC_COLLECTION_TRANSACTIONS_ENABLED=true`
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
- catalog write routes are absent unless `USER_STAC_CATALOG_TRANSACTIONS_ENABLED=true`.

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


## Networking and accessibility of the database. 

Because of security requirements, the networking set up imposes the following constraints : 

- For security reasons, the database is in a _private_ subnet of the VPC. As such, only instances running inside of the same VPC can access the database. This means that, for example, even if a user has the password and her IP is allowed inbound connections to the database, access will _not_ be allowed. 

This has three consequences : 

1. The APIs that need access to the database (the STAC API, the tiling API, the ingestion API) need to be deployed in that same VPC. 
2. In addition, because these APIs _also_ sometimes need access to the internet, a NAT gateway must in addition be deployed in that VPC. 
3. For direct, administrative connections to the database, one _must_ go through an instance placed in the same VPC as the database. 


## Ingestion

The term "ingestion" refers to the process of cataloging data in the STAC catalog associated with this deployment. 


### Direct ingestion

For a small record ingestion (for example a collection record or just one item), one can directly connect to the database and perform loading. This can be done using the `pypgstac` library. For example, to load an item stored locally in `test_item.json`, with `pypgstac` installed, you can run the following command : 

```
pypgstac load --table items test_item.json
```

or for a collection

```
pypgstac load --table collections test_collection.json
```


### Indirect ingestion through the ingestion pipeline deployment

For larger scale ingestions, in MAAP we rely on [a fork of the stactools-pipelines repository](https://github.com/MAAP-Project/stactools-pipelines/tree/non-standard-inventory). If you want to ingest a collection in MAAP using this tool, you should develop a 'pipeline'. Details of this procedure can be found in the linked repository. You can follow an example that [was developed for maap here](https://github.com/MAAP-Project/stactools-pipelines/tree/non-standard-inventory/stactools_pipelines/pipelines/nisar-sim).
