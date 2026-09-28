# Raster runtime

The runtime targets Python 3.12 and uses TiTiler pgSTAC 3.2.0 with TiTiler core, mosaic, and extensions 2.3.0. Dependencies are locked in `uv.lock`.

## Route migration

pgSTAC tile endpoints now use an explicit tile matrix set. For example:

- `/searches/{search_id}/tiles/WebMercatorQuad/{z}/{x}/{y}`
- `/searches/{search_id}/WebMercatorQuad/tilejson.json`
- `/searches/{search_id}/WebMercatorQuad/map.html`

The same route families are available under `/collections/{collection_id}`. Compatibility redirects are intentionally limited to the documented missing-TMS `/mosaic` and collection tile, TileJSON, and map-viewer aliases, plus the implemented TMS-qualified WMTS capability aliases and the existing `/mosaic`-to-`/searches` prefix remapping. These redirects use HTTP 307. Legacy `@{scale}x` tiles become unscaled tile paths with `tilesize=256*scale`; an explicit `tilesize` wins. Legacy TileJSON `tile_scale` becomes `tilesize` (including the old default scale of 1). TMS-qualified WMTS capability paths redirect to the native unqualified endpoint, which advertises all configured TMSs; the released WMTS extension has no parameter to limit the document to one TMS. This is not full compatibility for every old `/searches` endpoint or TMS-qualified scaling path or query.

The custom `/mosaics` creation and resource API remains available, but plural `/mosaics` URLs are not compatibility-redirected. Its native tile routes are TMS-qualified, for example `/mosaics/{mosaic_id}/tiles/WebMercatorQuad/{z}/{x}/{y}`.

For local Compose, raster database settings use the TiTiler pgSTAC `PGUSER`, `PGPASSWORD`, `PGDATABASE`, `PGHOST`, and `PGPORT` variables. Other services retain their own environment names.

## Focused checks

From this directory, run:

```sh
MOSAIC_BACKEND=dynamodb:// MOSAIC_HOST=us-west-2/test-table \
  uv run --locked --extra lambda --extra psycopg-binary --with pytest \
  pytest -q tests/test_runtime.py
```
