## eoapi.stac

MAAP-owned STAC API runtime scaffolding.

This package is the local home for the custom STAC runtime used by MAAP deployments. In this first slice it provides the packaging, Docker build path, and local development wiring needed to iterate on the runtime in-repo.

### Local development

Start the local pgSTAC + STAC + raster stack from the repository root:

```bash
docker compose up --build stac raster database
```

The local compose setup keeps STAC collection transactions disabled by default. Add or override environment variables with `.stac.env`, `.raster.env`, or `.env` as needed.

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

The local raster service also expects mosaic settings, so the compose file provides development defaults for:

- `MOSAIC_BACKEND`
- `MOSAIC_HOST`

### Packaging notes

- `cdk/dockerfiles/Dockerfile.stac` has separate `lambda` and `local` targets.
- The Docker build context for local and CDK builds is `cdk/`.
- `docker-compose.yml` builds the `local` target, which layers `uvicorn` on top of the runtime asset for local development only.
- Lambda builds should continue using the default `lambda` target without `uvicorn`.
- The local compose stack runs the MAAP app via `uvicorn eoapi.stac.main:app`.
- Runtime behavior, auth, and collection transaction wiring will land in this package in follow-up units.
