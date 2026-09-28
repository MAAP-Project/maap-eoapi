"""Custom MosaicJSON creation and resource routes."""

import asyncio
import logging
import os
import re
import uuid
from functools import partial
from typing import Annotated
from urllib.parse import unquote_plus

from cogeo_mosaic.backends import DynamoDBBackend, MosaicBackend
from cogeo_mosaic.errors import MosaicError, MosaicExistsError
from cogeo_mosaic.mosaic import MosaicJSON
from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Request,
    Response,
    status,
)
from fastapi.responses import RedirectResponse
from pystac_client import Client
from rio_tiler.constants import MAX_THREADS
from rio_tiler.io import Reader

from eoapi.raster.models import (
    Link,
    MosaicEntity,
    StacApiQueryRequestBody,
    TooManyResultsException,
    UrisRequestBody,
)
from eoapi.raster.settings import MosaicSettings

logger = logging.getLogger(__name__)
mosaic_config = MosaicSettings()
router = APIRouter()


async def mosaicjson_from_urls(request: UrisRequestBody) -> MosaicJSON:
    """Create a MosaicJSON from COG URLs."""
    if len(request.urls) > MAX_ITEMS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Error: a maximum of {MAX_ITEMS} URLs can be mosaiced.",
        )

    try:
        mosaicjson = await asyncio.wait_for(
            asyncio.to_thread(
                MosaicJSON.from_urls,
                urls=request.urls,
                minzoom=request.minzoom,
                maxzoom=request.maxzoom,
                max_threads=int(os.getenv("MOSAIC_CONCURRENCY", MAX_THREADS)),
            ),
            timeout=20,
        )
    except TimeoutError as e:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Error: timeout reading URLs and generating MosaicJSON definition",
        ) from e

    if mosaicjson is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Error: could not extract mosaic data",
        )

    mosaicjson.name = request.name
    mosaicjson.description = request.description
    mosaicjson.attribution = request.attribution
    mosaicjson.version = request.version or "0.0.1"
    return mosaicjson


async def mosaicjson_from_stac_api_query(
    request: StacApiQueryRequestBody,
) -> MosaicJSON:
    """Create a MosaicJSON from a STAC API search request."""
    if not request.stac_api_root:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Error: stac_api_root field must be non-empty.",
        )

    try:
        try:
            features = await asyncio.wait_for(
                asyncio.to_thread(execute_stac_search, request), timeout=30
            )
        except TimeoutError as e:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Error: timeout executing STAC API search.",
            ) from e
        except TooManyResultsException as e:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Error: too many results from STAC API Search: {e}",
            ) from e

        if not features:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Error: STAC API Search returned no results.",
            )

        try:
            mosaicjson = await asyncio.wait_for(
                asyncio.to_thread(
                    extract_mosaicjson_from_features,
                    features,
                    request.asset_name or "visual",
                ),
                timeout=60,
            )
        except TimeoutError as e:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Error: timeout reading a COG asset and generating "
                "MosaicJSON definition",
            ) from e

        if mosaicjson is None:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Error: could not extract mosaic data",
            )

        mosaicjson.name = request.name
        mosaicjson.description = request.description
        mosaicjson.attribution = request.attribution
        mosaicjson.version = request.version or "0.0.1"
        return mosaicjson
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Error: {e}") from e


MAX_ITEMS = 100


def execute_stac_search(request: StacApiQueryRequestBody) -> list[dict]:
    """Run a bounded STAC API search for mosaic source items."""
    try:
        result = Client.open(request.stac_api_root).search(
            ids=request.ids,
            collections=request.collections,
            datetime=request.datetime,
            bbox=request.bbox,
            intersects=request.intersects,
            query=request.query,
            max_items=MAX_ITEMS,
            limit=request.limit or MAX_ITEMS,
        )
        matched = result.matched()
        if matched > MAX_ITEMS:
            raise TooManyResultsException(
                f"too many results: {matched} Items matched, "
                f"but only a maximum of {MAX_ITEMS} are allowed."
            )
        return result.items_as_collection().to_dict()["features"]
    except TooManyResultsException:
        raise
    except Exception as e:
        raise Exception(f"STAC Search error: {e}") from e


def asset_href(feature: dict, asset_name: str) -> str:
    """Return the named asset href from a STAC Item."""
    if href := feature.get("assets", {}).get(asset_name, {}).get("href"):
        return href
    raise ValueError(f"Asset with name '{asset_name}' could not be found.")


def extract_mosaicjson_from_features(
    features: list[dict], asset_name: str
) -> MosaicJSON | None:
    """Build a MosaicJSON from STAC Items using their named COG asset."""
    if not features:
        return None

    try:
        with Reader(asset_href(features[0], asset_name)) as cog:
            info = cog.info()
        return MosaicJSON.from_features(
            features,
            minzoom=info.minzoom,
            maxzoom=info.maxzoom,
            accessor=partial(asset_href, asset_name=asset_name),
        )
    except UnboundLocalError as e:
        raise ValueError(
            "STAC Items likely have MultiPolygon geometry; only Polygon is supported."
        ) from e
    except Exception as e:
        raise ValueError(f"Error extracting mosaic data from results: {e}") from e


async def populate_mosaicjson(
    request: Request,
    content_type: Annotated[str | None, Header()] = None,
) -> MosaicJSON:
    """Parse the supported MosaicJSON creation request bodies."""
    body = await request.json()

    if content_type in (
        None,
        "application/json",
        "application/json; charset=utf-8",
        "application/vnd.titiler.mosaicjson+json",
    ):
        return MosaicJSON.model_validate(body)
    if content_type == "application/vnd.titiler.urls+json":
        return await mosaicjson_from_urls(UrisRequestBody.model_validate(body))
    if content_type == "application/vnd.titiler.stac-api-query+json":
        return await mosaicjson_from_stac_api_query(
            StacApiQueryRequestBody.model_validate(body)
        )
    raise HTTPException(
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        "Error: media in Content-Type header is not supported.",
    )


def mk_src_path(mosaic_id: str) -> str:
    """Build the configured cogeo-mosaic storage URI for a mosaic ID."""
    if mosaic_config.backend == "dynamodb://":
        return f"{mosaic_config.backend}{mosaic_config.host}:{mosaic_id}"
    return (
        f"{mosaic_config.backend}{mosaic_config.host}/{mosaic_id}{mosaic_config.format}"
    )


def mosaic_path(mosaic_id: Annotated[str, Path()]) -> str:
    """Resolve a resource ID to its configured storage URI."""
    return mk_src_path(mosaic_id)


def mosaic_write(mosaic_id: str, mosaicjson: MosaicJSON) -> None:
    """Write a MosaicJSON to the configured backend."""
    with MosaicBackend(mk_src_path(mosaic_id), mosaic_def=mosaicjson) as backend:
        backend.write(overwrite=False)


def read_mosaicjson(mosaic_id: str, include_tiles: bool = False) -> MosaicJSON:
    """Read a MosaicJSON, restoring DynamoDB tile rows when requested."""
    with MosaicBackend(mk_src_path(mosaic_id)) as backend:
        mosaicjson = backend.mosaic_def
        if include_tiles and isinstance(backend, DynamoDBBackend):
            mosaicjson.tiles = {
                item["quadkey"]: item["assets"]
                for item in (backend._fetch_dynamodb(key) for key in backend._quadkeys)
            }
        return mosaicjson


async def retrieve_mosaic(
    mosaic_id: str, include_tiles: bool = False
) -> MosaicJSON | None:
    """Read a stored MosaicJSON, returning None when it does not exist."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(read_mosaicjson, mosaic_id, include_tiles), timeout=20
        )
    except TimeoutError as e:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Error: timeout retrieving mosaic from datastore.",
        ) from e
    except MosaicError:
        return None


def mk_mosaic_entity(mosaic_id: str, self_uri: str) -> MosaicEntity:
    """Build the custom Mosaic resource and its native TMS-qualified links."""
    return MosaicEntity(
        id=mosaic_id,
        links=[
            Link(rel="self", href=self_uri, type="application/json", title="Self"),
            Link(
                rel="mosaicjson",
                href=f"{self_uri}/mosaicjson",
                type="application/json",
                title="MosaicJSON",
            ),
            Link(
                rel="tilejson",
                href=f"{self_uri}/WebMercatorQuad/tilejson.json",
                type="application/json",
                title="TileJSON",
            ),
            Link(
                rel="tiles",
                href=f"{self_uri}/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}",
                type="application/json",
                title="Tiles",
            ),
            Link(
                rel="wmts",
                href=f"{self_uri}/WMTSCapabilities.xml",
                type="application/json",
                title="WMTS",
            ),
        ],
    )


@router.get("/mosaics/{mosaic_id}", name="get_mosaic")
async def get_mosaic(request: Request, mosaic_id: str) -> MosaicEntity:
    """Return the custom Mosaic resource for a stored ID."""
    self_uri = str(request.url_for("get_mosaic", mosaic_id=mosaic_id))
    if await retrieve_mosaic(mosaic_id):
        return mk_mosaic_entity(mosaic_id, self_uri)
    raise HTTPException(
        status.HTTP_404_NOT_FOUND, "Error: mosaic with given ID does not exist."
    )


@router.get("/mosaics/{mosaic_id}/mosaicjson")
async def get_mosaicjson(mosaic_id: str) -> MosaicJSON:
    """Return the stored MosaicJSON definition."""
    if mosaicjson := await retrieve_mosaic(mosaic_id, include_tiles=True):
        return mosaicjson
    raise HTTPException(
        status.HTTP_404_NOT_FOUND, "Error: mosaic with given ID does not exist."
    )


@router.post(
    "/mosaics",
    status_code=status.HTTP_201_CREATED,
    openapi_extra={
        "requestBody": {
            "content": {
                "": {"schema": MosaicJSON.model_json_schema()},
                "application/json": {"schema": MosaicJSON.model_json_schema()},
                "application/json; charset=utf-8": {
                    "schema": MosaicJSON.model_json_schema()
                },
                "application/vnd.titiler.mosaicjson+json": {
                    "schema": MosaicJSON.model_json_schema()
                },
                "application/vnd.titiler.urls+json": {
                    "schema": UrisRequestBody.model_json_schema()
                },
                "application/vnd.titiler.stac-api-query+json": {
                    "schema": StacApiQueryRequestBody.model_json_schema()
                },
            },
            "required": True,
        }
    },
)
async def post_mosaics(
    request: Request,
    response: Response,
    mosaicjson: Annotated[MosaicJSON, Depends(populate_mosaicjson)],
) -> MosaicEntity:
    """Create and store a MosaicJSON using the custom API."""
    mosaic_id = str(uuid.uuid4())
    try:
        await asyncio.wait_for(
            asyncio.to_thread(mosaic_write, mosaic_id, mosaicjson), 20
        )
    except TimeoutError as e:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Error: timeout storing mosaic in datastore",
        ) from e
    except MosaicExistsError as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Error: mosaic with given ID already exists",
        ) from e
    except Exception as e:
        logger.exception("Could not save mosaic")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Error: could not save mosaic"
        ) from e

    self_uri = str(request.url_for("get_mosaic", mosaic_id=mosaic_id))
    response.headers["Location"] = self_uri
    return mk_mosaic_entity(mosaic_id, self_uri)


def _redirect(
    request: Request,
    path: str,
    *,
    tilesize: int | None = None,
    remove_tile_scale: bool = False,
) -> RedirectResponse:
    """Redirect while preserving unrelated query parameters and the method."""
    parts = request.scope["query_string"].decode("latin-1").split("&")
    keys = [unquote_plus(part.partition("=")[0]) for part in parts if part]
    has_tilesize = "tilesize" in keys
    if remove_tile_scale:
        parts = [
            part
            for part in parts
            if unquote_plus(part.partition("=")[0]) != "tile_scale"
        ]
    if tilesize is not None and not has_tilesize:
        parts.append(f"tilesize={tilesize}")
    query = "&".join(part for part in parts if part)
    return RedirectResponse(f"{path}?{query}" if query else path, status_code=307)


def redirect_collection_compatibility(request: Request) -> RedirectResponse | None:
    """Redirect only legacy collection paths missing their tile matrix set."""
    path = request.url.path
    if match := re.fullmatch(
        r"/collections/([^/]+)/tiles/(\d+)/(\d+)/(\d+)(?:@(\d+)x)?(\.[\w-]+)?(/assets)?",
        path,
    ):
        collection_id, z, x, y, scale, extension, assets = match.groups()
        target = (
            f"/collections/{collection_id}/tiles/WebMercatorQuad/"
            f"{z}/{x}/{y}{extension or ''}"
        )
        tilesize = 256 * int(scale) if scale and not assets else None
        return _redirect(request, target + (assets or ""), tilesize=tilesize)

    if match := re.fullmatch(
        r"/collections/([^/]+)/([^/]+)/WMTSCapabilities\.xml", path
    ):
        collection_id, _ = match.groups()
        return _redirect(request, f"/collections/{collection_id}/WMTSCapabilities.xml")

    if match := re.fullmatch(r"/collections/([^/]+)/(tilejson\.json|map\.html)", path):
        collection_id, endpoint = match.groups()
        if endpoint == "tilejson.json":
            try:
                tile_scale = int(request.query_params.get("tile_scale", "1"))
            except ValueError as e:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY) from e
            if not 0 < tile_scale < 4:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY)
            return _redirect(
                request,
                f"/collections/{collection_id}/WebMercatorQuad/{endpoint}",
                tilesize=256 * tile_scale,
                remove_tile_scale=True,
            )
        return _redirect(
            request, f"/collections/{collection_id}/WebMercatorQuad/{endpoint}"
        )
    return None


def _redirect_search_id(
    search_id: str | None, _collection_id: str | None = None
) -> str:
    """Return the native pgSTAC search path for a legacy route."""
    return f"/searches/{search_id}"


@router.api_route(
    "/mosaic/{search_id}/tiles/{z:int}/{x:int}/{y:int}",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
async def redirect_tiles(
    request: Request,
    z: int,
    x: int,
    y: int,
    search_id: str | None = None,
    collection_id: str | None = None,
):
    """Redirect legacy no-TMS tile paths to native WebMercatorQuad routes."""
    target = _redirect_search_id(search_id, collection_id)
    return _redirect(request, f"{target}/tiles/WebMercatorQuad/{z}/{x}/{y}")


@router.api_route(
    "/mosaic/{search_id}/tiles/{z:int}/{x:int}/{y:int}.{format}",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
async def redirect_tiles_format(
    request: Request,
    z: int,
    x: int,
    y: int,
    format: str,
    search_id: str | None = None,
    collection_id: str | None = None,
):
    """Redirect legacy formatted tile paths to native WebMercatorQuad routes."""
    target = _redirect_search_id(search_id, collection_id)
    return _redirect(request, f"{target}/tiles/WebMercatorQuad/{z}/{x}/{y}.{format}")


@router.api_route(
    "/mosaic/{search_id}/tiles/{z}/{x}/{y}@{scale}x",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
async def redirect_tiles_scale(
    request: Request,
    z: str,
    x: str,
    y: str,
    scale: int,
    search_id: str | None = None,
    collection_id: str | None = None,
):
    """Redirect scaled paths to native tilesize query parameters."""
    target = _redirect_search_id(search_id, collection_id)
    return _redirect(
        request,
        f"{target}/tiles/WebMercatorQuad/{z}/{x}/{y}",
        tilesize=256 * scale,
    )


@router.api_route(
    "/mosaic/{search_id}/tiles/{z}/{x}/{y}@{scale}x.{format}",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
async def redirect_tiles_scale_format(
    request: Request,
    z: str,
    x: str,
    y: str,
    scale: int,
    format: str,
    search_id: str | None = None,
    collection_id: str | None = None,
):
    """Redirect scaled formatted paths to native tilesize query parameters."""
    target = _redirect_search_id(search_id, collection_id)
    return _redirect(
        request,
        f"{target}/tiles/WebMercatorQuad/{z}/{x}/{y}.{format}",
        tilesize=256 * scale,
    )


@router.api_route(
    "/mosaic/{search_id}/tiles/{z}/{x}/{y}/assets",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
async def redirect_tile_assets(
    request: Request,
    z: str,
    x: str,
    y: str,
    search_id: str | None = None,
    collection_id: str | None = None,
):
    """Redirect legacy tile asset paths to native WebMercatorQuad routes."""
    target = _redirect_search_id(search_id, collection_id)
    return _redirect(request, f"{target}/tiles/WebMercatorQuad/{z}/{x}/{y}/assets")


@router.api_route(
    "/mosaic/{search_id}/{tile_matrix_set_id}/WMTSCapabilities.xml",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
async def redirect_wmts(
    request: Request,
    search_id: str,
    tile_matrix_set_id: str,
):
    """Redirect the legacy TMS-specific WMTS path to the native endpoint."""
    target = _redirect_search_id(search_id)
    return _redirect(request, f"{target}/WMTSCapabilities.xml")


@router.api_route(
    "/mosaic/{search_id}/tilejson.json",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
async def redirect_tilejson(
    request: Request,
    search_id: str | None = None,
    collection_id: str | None = None,
):
    """Redirect legacy TileJSON scale options to native tilesize parameters."""
    try:
        tile_scale = int(request.query_params.get("tile_scale", "1"))
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY) from e
    if not 0 < tile_scale < 4:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY)
    target = _redirect_search_id(search_id, collection_id)
    return _redirect(
        request,
        f"{target}/WebMercatorQuad/tilejson.json",
        tilesize=256 * tile_scale,
        remove_tile_scale=True,
    )


@router.api_route(
    "/mosaic/{search_id}/map.html", methods=["GET", "HEAD"], include_in_schema=False
)
async def redirect_map(
    request: Request,
    search_id: str | None = None,
    collection_id: str | None = None,
):
    """Redirect legacy viewer paths to native WebMercatorQuad routes."""
    target = _redirect_search_id(search_id, collection_id)
    return _redirect(request, f"{target}/WebMercatorQuad/map.html")


@router.api_route(
    "/mosaic/{subpath:path}", methods=["GET", "POST"], include_in_schema=False
)
async def redirect_to_searches(request: Request, subpath: str):
    """Keep the old pgSTAC route prefix working for non-TMS endpoints."""
    new_path = "/searches/" if subpath == "list" else f"/searches/{subpath}"
    return _redirect(request, new_path)
