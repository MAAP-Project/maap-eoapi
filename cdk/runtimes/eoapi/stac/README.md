## eoapi.stac

MAAP-owned STAC API runtime scaffolding.

This package is the local home for the custom STAC runtime used by MAAP deployments. In this first slice it provides the packaging, Docker build path, and local development wiring needed to iterate on the runtime in-repo.

### Local development

Start the local pgSTAC + STAC + raster stack from the repository root:

```bash
docker compose up --build stac raster database
```

The local compose setup bind-mounts `cdk/runtimes/eoapi/stac/` into the container and runs `uvicorn --reload`, so changes under `cdk/runtimes/eoapi/stac/eoapi/stac/` are picked up without rebuilding the image. Add or override environment variables with `.stac.env`, `.raster.env`, or `.env` as needed.

When you enable collection transactions, the runtime now fails closed unless these env vars are present:

- `MAAP_TRANSACTION_AUTH_MODE=basic`
- one of:
  - `MAAP_TRANSACTION_AUTH_SECRET_ARN`, or
  - both `MAAP_TRANSACTION_AUTH_USERNAME` and `MAAP_TRANSACTION_AUTH_PASSWORD`

The secret form is intended for Lambda deployments. The username/password env-var form is intended for local docker-compose development. If a secret ARN is present, it takes precedence.

The secret must be a JSON object with `username` and `password` string fields.

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
- Collection write-route auth is attached with FastAPI security dependencies on `POST /collections` plus `PUT`, `PATCH`, and `DELETE /collections/{collection_id}`.
- Those dependencies are declared as HTTP Basic auth in OpenAPI, so Swagger UI shows the protected routes with the built-in auth flow instead of relying only on the browser challenge popup.

### Post-deploy smoke checks

For a transaction-enabled deployment, verify:

- `GET /conformance` advertises only the collection transaction conformance class.
- OpenAPI includes collection write routes and does not advertise item transaction write routes.
- `POST /collections` without auth returns `401` with `WWW-Authenticate: Basic`.
- Authenticated `POST`, `PUT`, `PATCH`, and `DELETE` requests against `/collections` succeed when the backing pgSTAC deployment is healthy.
- Item write routes such as `POST /collections/{collection_id}/items` remain unavailable.
