# Raster runtime

The runtime targets Python 3.12 and uses TiTiler pgSTAC 3.2.0 with TiTiler core, mosaic, and extensions 2.3.0. Dependencies are locked in `uv.lock`.

## Route migration

pgSTAC tile endpoints now use an explicit tile matrix set. For example:

- `/searches/{search_id}/tiles/WebMercatorQuad/{z}/{x}/{y}`
- `/searches/{search_id}/WebMercatorQuad/tilejson.json`
- `/searches/{search_id}/WebMercatorQuad/map.html`

The same route families are available under `/collections/{collection_id}`. Legacy no-TMS tile, TileJSON, and map-viewer paths redirect with HTTP 307 to their `WebMercatorQuad` equivalents. The old `/mosaic` prefix still redirects other routes to `/searches`; `/mosaic/register` and `/mosaic/list` map to `/searches/register` and `/searches/` respectively. Info, point, WMTS, registration, statistics, and tileset-list routes do not get a tile matrix segment injected.

The custom `/mosaics` creation and resource API remains available. Its native tile routes are TMS-qualified, for example `/mosaics/{mosaic_id}/tiles/WebMercatorQuad/{z}/{x}/{y}`.

For local Compose, raster database settings use the TiTiler pgSTAC `PGUSER`, `PGPASSWORD`, `PGDATABASE`, `PGHOST`, and `PGPORT` variables. Other services retain their own environment names.

## Focused checks

From this directory, run:

```sh
MOSAIC_BACKEND=dynamodb:// MOSAIC_HOST=us-west-2/test-table \
  uv run --locked --extra lambda --extra psycopg-binary --with pytest \
  pytest -q tests/test_runtime.py
```
