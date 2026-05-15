# MAAP eoapi

[![Tests Status](https://github.com/MAAP-Project/maap-eoapi/actions/workflows/tests.yml/badge.svg)]((https://github.com/MAAP-Project/maap-eoapi/actions?query=workflow:tests))


## Overview

This repository contains the AWS CDK code (written in typescript) used to deploy the MAAP project eoapi infrastructure. It is based on the [eoapi-template example](https://github.com/developmentseed/eoapi-template). For the MAAP use case, we use a subset of the eoapi CDK constructs to define a database, an ingestion API, a STAC API, a raster API (i.e a tiling API) and a pgbouncer instance to manage connections to the database. Here, we deploy all these components into a custom VPC.


## Automated Deployment

Deployment happens through a github workflow manually triggered and defined in `.github/workflows/deploy.yaml`.

## User STAC collection transactions

The internal `userSTAC` deployment can now opt into collection-only STAC transactions.

Enable them with:

- `USER_STAC_COLLECTION_TRANSACTIONS_ENABLED=true`
- `USER_STAC_COLLECTION_TRANSACTIONS_AUTH_MODE=basic`

When enabled, this CDK stack creates and manages the Secrets Manager secret used for STAC basic auth by default, grants the STAC Lambda read access to it, and publishes the secret ARN to SSM at:

- `/maap-eoapi/<stage>/internal/stac-collection-transaction-auth-secret-arn`

You can still override the secret with `USER_STAC_COLLECTION_TRANSACTIONS_AUTH_SECRET_ARN` if you need to point at an existing secret instead.


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
