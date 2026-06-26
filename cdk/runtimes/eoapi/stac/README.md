## eoapi.stac

MAAP-owned STAC API runtime scaffolding.

This package is the local home for the custom STAC runtime used by MAAP deployments. In this first slice it provides the packaging, Docker build path, and local development wiring needed to iterate on the runtime in-repo.

### Local development

Start the local pgSTAC + STAC + raster stack from the repository root:

```bash
docker compose up --build stac raster database
```

The local compose setup bind-mounts `cdk/runtimes/eoapi/stac/` into the container and runs `uvicorn --reload`, so changes under `cdk/runtimes/eoapi/stac/eoapi/stac/` are picked up without rebuilding the image. Add or override environment variables with `.stac.env`, `.raster.env`, or `.env` as needed.

Multi-tenant catalog routes are enabled through `ENABLED_EXTENSIONS=catalogs` and the upstream-compatible `ENABLE_CATALOGS_EXTENSION=true` setting. The local compose default includes both read-only catalog routes and catalog transaction routes by setting `STAC_ENABLED_EXTENSIONS` to include `catalogs`, `collection_transaction`, and `catalog_transaction`.

To run the local API with read-only catalog routes only, override `STAC_ENABLED_EXTENSIONS` without `catalog_transaction`, for example:

```bash
STAC_ENABLED_EXTENSIONS=query,sort,fields,filter,free_text,pagination,collection_search,catalogs docker compose up --build stac database
```

Catalog transaction routes are separate from collection transaction routes. Enabling `catalogs` alone adds read routes such as `GET /catalogs`, `GET /catalogs/{catalog_id}`, and catalog-scoped collection/item reads. It does not add write routes.

When you enable collection or catalog transactions, the runtime fails closed unless these env vars are present:

- `MAAP_TRANSACTION_AUTH_MODE=basic`
- one of:
  - `MAAP_TRANSACTION_AUTH_SECRET_ARN`, or
  - both `MAAP_TRANSACTION_AUTH_USERNAME` and `MAAP_TRANSACTION_AUTH_PASSWORD`

The secret form is intended for Lambda deployments. The username/password env-var form is intended for local docker-compose development. If a secret ARN is present, it takes precedence.

The secret must be a JSON object with `username` and `password` string fields.

### Loading local demo data

From the repository root, load a small catalogs-extension demo into the local pgSTAC database with:

```bash
docker compose up -d database
./scripts/load_demo_stac_catalogs.py
```

The script uses `pypgstac[psycopg]` to load DPS user/team root catalogs, per-user catalogs for `hrodmn` and `jjfrench`, a shared team catalog, and synthetic DPS-output collections linked into those catalogs. Use `--dry-run` to inspect the records first or `--reset` to delete all existing catalog and collection records before reloading the demo records.

### Running tests

From this directory, run:

```bash
uv run pytest
```

These tests cover app construction, OpenAPI and conformance output, auth behavior, and the custom Lambda handler lifecycle.

### Environment shape

The local STAC service uses the same pgSTAC-style environment variables already used elsewhere in eoapi development:

- `POSTGRES_USER`
- `POSTGRES_PASS`
- `POSTGRES_DBNAME`
- `POSTGRES_HOST_READER`
- `POSTGRES_HOST_WRITER`
- `POSTGRES_PORT`
- `DB_MIN_CONN_SIZE`
- `DB_MAX_CONN_SIZE`
- `ENABLED_EXTENSIONS`
- `ENABLE_CATALOGS_EXTENSION`
- `HIDE_ALTERNATE_PARENTS`
- `TITILER_ENDPOINT`
- `MAAP_TRANSACTION_AUTH_MODE`
- `MAAP_TRANSACTION_AUTH_USERNAME`
- `MAAP_TRANSACTION_AUTH_PASSWORD`
- `MAAP_TRANSACTION_AUTH_SECRET_ARN`

The local raster service also expects mosaic settings, so the compose file provides development defaults for:

- `MOSAIC_BACKEND`
- `MOSAIC_HOST`

### Packaging notes

- `cdk/dockerfiles/Dockerfile.stac` has separate `lambda` and `local` targets.
- The Docker build context for local and CDK builds is `cdk/`.
- `docker-compose.yml` builds the `local` target, which layers `uvicorn` on top of the runtime asset for local development only.
- Lambda builds should continue using the default `lambda` target without `uvicorn`.
- The local compose stack runs the MAAP app via `uvicorn eoapi.stac.main:app --reload --reload-dir /workspace/eoapi/stac`.
- The Lambda runtime entrypoint is `eoapi.stac.handler.handler` and preserves the upstream SnapStart-aware connection lifecycle.
- Collection write-route auth is attached through the transaction extension `route_dependencies` hook on `POST /collections` plus `PUT`, `PATCH`, and `DELETE /collections/{collection_id}`.
- Catalog write-route auth is attached by a narrow local adapter around the upstream `CatalogsTransactionExtension` because version 0.4.0 does not expose a `route_dependencies` constructor hook.
- Those dependencies are declared as HTTP Basic auth in OpenAPI, so Swagger UI shows the protected routes with the built-in auth flow instead of relying only on the browser challenge popup.

### Post-deploy smoke checks

For a catalogs-enabled deployment, verify:

- OpenAPI includes read routes such as `GET /catalogs`, `GET /catalogs/{catalog_id}`, `GET /catalogs/{catalog_id}/collections`, and `GET /catalogs/{catalog_id}/collections/{collection_id}/items`.
- `GET /` includes a `rel="catalogs"` link and `rel="child"` links for listed catalogs so STAC Browser can discover catalog roots.
- `GET /catalogs/{catalog_id}/conformance` advertises catalog conformance classes.
- Catalog write routes are absent unless `catalog_transaction` is enabled.

For a transaction-enabled deployment, verify:

- `GET /conformance` advertises only the collection transaction conformance class when collection transactions are enabled.
- OpenAPI includes collection write routes and does not advertise item transaction write routes.
- OpenAPI includes catalog write routes only when `catalog_transaction` is enabled.
- `POST /collections` and `POST /catalogs` without auth return `401` with `WWW-Authenticate: Basic` when their transaction extensions are enabled.
- Authenticated write requests succeed when the backing pgSTAC deployment is healthy.
- Item write routes such as `POST /collections/{collection_id}/items` remain unavailable.
