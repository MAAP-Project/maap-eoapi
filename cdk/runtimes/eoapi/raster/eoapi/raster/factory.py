"""Custom factory classes for titiler-pgstac"""

import asyncio
import logging
import os
import uuid
from asyncio import wait_for
from functools import partial
from typing import Annotated, Dict, List, Optional
from urllib.parse import urlencode

import morecantile
import rasterio
from attrs import define
from cogeo_mosaic.backends import DynamoDBBackend
from cogeo_mosaic.errors import MosaicError
from cogeo_mosaic.mosaic import MosaicJSON
from fastapi import Depends, Header, HTTPException, Path, Query
from pydantic import Field
from pystac_client import Client
from rio_tiler.constants import MAX_THREADS
from rio_tiler.io import Reader
from starlette import status
from starlette.requests import Request
from starlette.responses import Response
from titiler.core.factory import img_endpoint_params
from titiler.core.models.mapbox import TileJSON
from titiler.core.resources.enums import ImageType, MediaType, OptionalHeader
from titiler.core.resources.responses import JSONResponse, XMLResponse
from titiler.mosaic import factory
from titiler.mosaic.models.responses import Point

from eoapi.raster.models import (
    Link,
    MosaicEntity,
    StacApiQueryRequestBody,
    StoreException,
    TooManyResultsException,
    UnsupportedOperationException,
    UrisRequestBody,
)
from eoapi.raster.settings import MosaicSettings

mosaic_config = MosaicSettings()


@define(kw_only=True)
class MosaicTilerFactory(factory.MosaicTilerFactory):
    """Custom MosaicTiler Factory."""

    logger = logging.getLogger(__name__)

    def register_routes(self):  # noqa
        """This Method register routes to the router."""
        # super().register_routes()  # Register default endpoints from titiler MosaicTilerFactory

        # Register Custom endpoints
        # with dynamodb backend, the tiles field for this is always empty
        # https://github.com/developmentseed/cogeo-mosaic/issues/175
        @self.router.get(
            "/mosaics/{mosaic_id}",
            response_model=MosaicEntity,
            responses={
                status.HTTP_200_OK: {
                    "description": "Return a Mosaic resource for the given ID."
                },
                status.HTTP_404_NOT_FOUND: {
                    "description": "Mosaic resource for the given ID does not exist."
                },
            },
        )
        async def get_mosaic(
            request: Request,
            mosaic_id: str = Path(..., description="MosaicId"),
        ) -> MosaicEntity:
            self_uri = self.url_for(request, "get_mosaic", mosaic_id=mosaic_id)
            if await retrieve(mosaic_id):
                return mk_mosaic_entity(mosaic_id=mosaic_id, self_uri=self_uri)

            else:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    "Error: mosaic with given ID does not exist.",
                )

        @self.router.get(
            "/mosaics/{mosaic_id}/mosaicjson",
            response_model=MosaicJSON,
            responses={
                200: {
                    "description": "Return a MosaicJSON definition for the given ID."
                },
                404: {
                    "description": "Mosaic resource for the given ID does not exist."
                },
            },
        )
        async def get_mosaic_mosaicjson(
            mosaic_id: str = Path(..., description="MosaicId"),
        ) -> MosaicJSON:
            if m := await retrieve(mosaic_id, include_tiles=True):
                return m
            else:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    "Error: mosaic with given ID does not exist.",
                )

        @self.router.post(
            "/mosaics",
            status_code=status.HTTP_201_CREATED,
            responses={
                status.HTTP_201_CREATED: {"description": "Created a new mosaic"},
                status.HTTP_409_CONFLICT: {
                    "description": "Conflict while trying to create mosaic"
                },
                status.HTTP_500_INTERNAL_SERVER_ERROR: {
                    "description": "Mosaic could not be created"
                },
            },
            response_model=MosaicEntity,
            openapi_extra={
                "requestBody": {
                    "content": {
                        "": {
                            "schema": MosaicJSON.model_json_schema(
                                ref_template="#/components/schemas"
                            )
                        },
                        "application/json": {
                            "schema": MosaicJSON.model_json_schema(
                                ref_template="#/components/schemas"
                            )
                        },
                        "application/json; charset=utf-8": {
                            "schema": MosaicJSON.model_json_schema(
                                ref_template="#/components/schemas"
                            )
                        },
                        "application/vnd.titiler.mosaicjson+json": {
                            "schema": MosaicJSON.model_json_schema(
                                ref_template="#/components/schemas"
                            )
                        },
                        "application/vnd.titiler.urls+json": {
                            "schema": UrisRequestBody.model_json_schema(
                                ref_template="#/components/schemas"
                            )
                        },
                        "application/vnd.titiler.stac-api-query+json": {
                            "schema": StacApiQueryRequestBody.model_json_schema(
                                ref_template="#/components/schemas"
                            )
                        },
                    },
                    "required": True,
                },
            },
        )
        async def post_mosaics(
            request: Request,
            response: Response,
            mosaicjson: MosaicJSON = Depends(populate_mosaicjson),
        ) -> MosaicEntity:
            """Create a MosaicJSON"""
            mosaic_id = str(uuid.uuid4())

            # duplicate IDs are unlikely to exist, but handle it just to be safe
            try:
                await store(mosaic_id, mosaicjson, overwrite=False)

            except StoreException:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Error: mosaic with given ID already exists",
                )

            except Exception as e:
                logging.error(f"could not save mosaic: {e}")
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "Error: could not save mosaic",
                )

            self_uri = self.url_for(request, "get_mosaic", mosaic_id=mosaic_id)
            response.headers["Location"] = self_uri

            return mk_mosaic_entity(mosaic_id, self_uri)

        # derived from cogeo.xyz
        @self.router.get(
            "/mosaics/{mosaic_id}/tilejson.json",
            response_model=TileJSON,
            responses={
                200: {"description": "Return a tilejson for the given ID."},
                404: {
                    "description": "Mosaic resource for the given ID does not exist."
                },
            },
            response_model_exclude_none=True,
        )
        async def tilejson(
            request: Request,
            mosaic_id: str = Path(..., description="MosaicId"),
            tile_format: Optional[ImageType] = Query(
                None, description="Output image type. Default is auto."
            ),
            tile_scale: int = Query(
                1, gt=0, lt=4, description="Tile size scale. 1=256x256, 2=512x512..."
            ),
            minzoom: Optional[int] = Query(
                None, description="Overwrite default minzoom."
            ),
            maxzoom: Optional[int] = Query(
                None, description="Overwrite default maxzoom."
            ),
            layer_params=Depends(self.layer_dependency),
            dataset_params=Depends(self.dataset_dependency),  # noqa
            pixel_selection=Depends(self.pixel_selection_dependency),  # noqa
            post_process=Depends(self.process_dependency),  # noqa
            colormap=Depends(self.colormap_dependency),  # noqa
            render_params=Depends(self.render_dependency),  # noqa
            backend_params=Depends(self.backend_dependency),  # noqa
            reader_params=Depends(self.reader_dependency),  # noqa
            env=Depends(self.environment_dependency),  # noqa
        ):
            """Return TileJSON document for a MosaicJSON."""
            kwargs = {
                "mosaic_id": mosaic_id,
                "z": "{z}",
                "x": "{x}",
                "y": "{y}",
                "scale": tile_scale,
            }
            if tile_format:
                kwargs["format"] = tile_format.value
            tiles_url = self.url_for(request, "tile", **kwargs)

            qs_key_to_remove = [
                "tile_format",
                "tile_scale",
                "minzoom",
                "maxzoom",
            ]
            qs = [
                (key, value)
                for (key, value) in request.query_params._list
                if key.lower() not in qs_key_to_remove
            ]
            if qs:
                tiles_url += f"?{urlencode(qs)}"

            if mosaicjson := await retrieve(mosaic_id):
                center = list(mosaicjson.center)
                if minzoom is not None:
                    center[-1] = minzoom
                return {
                    "bounds": mosaicjson.bounds,
                    "center": tuple(center),
                    "minzoom": minzoom if minzoom is not None else mosaicjson.minzoom,
                    "maxzoom": maxzoom if maxzoom is not None else mosaicjson.maxzoom,
                    "name": mosaic_id,
                    "tiles": [tiles_url],
                }

            else:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    "Error: mosaic with given ID does not exist.",
                )

        # derived from cogeo-xyz
        @self.router.get(
            "/mosaics/{mosaic_id}/tiles/{z}/{x}/{y}", **img_endpoint_params
        )
        @self.router.get(
            "/mosaics/{mosaic_id}/tiles/{z}/{x}/{y}.{format}", **img_endpoint_params
        )
        @self.router.get(
            "/mosaics/{mosaic_id}/tiles/{z}/{x}/{y}@{scale}x", **img_endpoint_params
        )
        @self.router.get(
            "/mosaics/{mosaic_id}/tiles/{z}/{x}/{y}@{scale}x.{format}",
            **img_endpoint_params,
        )
        def tile(
            mosaic_id: str = Path(..., description="MosaicId"),
            z: int = Path(..., ge=0, le=30, description="Mercator tiles's zoom level"),
            x: int = Path(..., description="Mercator tiles's column"),
            y: int = Path(..., description="Mercator tiles's row"),
            scale: Annotated[
                int,
                Field(
                    gt=0, le=4, description="Tile size scale. 1=256x256, 2=512x512..."
                ),
            ] = 1,
            format: Annotated[
                ImageType,
                "Default will be automatically defined if the output image needs a mask (png) or not (jpeg).",
            ] = None,
            backend_params=Depends(self.backend_dependency),
            reader_params=Depends(self.reader_dependency),
            layer_params=Depends(self.layer_dependency),
            dataset_params=Depends(self.dataset_dependency),
            pixel_selection=Depends(self.pixel_selection_dependency),
            tile_params=Depends(self.tile_dependency),
            post_process=Depends(self.process_dependency),
            colormap=Depends(self.colormap_dependency),
            render_params=Depends(self.render_dependency),
            env=Depends(self.environment_dependency),
        ):
            """Create map tile from a mosaic."""
            threads = int(os.getenv("MOSAIC_CONCURRENCY", MAX_THREADS))

            mosaic_uri = mk_src_path(mosaic_id)
            tms = self.supported_tms.get("WebMercatorQuad")
            with rasterio.Env(**env):
                with self.backend(
                    mosaic_uri,
                    tms=tms,
                    reader=self.dataset_reader,
                    reader_options=reader_params.as_dict(),
                    **backend_params.as_dict(),
                ) as src_dst:
                    image, assets = src_dst.tile(
                        x,
                        y,
                        z,
                        pixel_selection=pixel_selection,
                        tilesize=scale * 256,
                        threads=threads,
                        **tile_params.as_dict(),
                        **layer_params.as_dict(),
                        **dataset_params.as_dict(),
                    )

            if post_process:
                image = post_process(image)

            content, media_type = self.render_func(
                image,
                output_format=format,
                colormap=colormap,
                **render_params.as_dict(),
            )

            headers: Dict[str, str] = {}
            if OptionalHeader.x_assets in self.optional_headers:
                headers["X-Assets"] = ",".join(assets)

            return Response(content, media_type=media_type, headers=headers)

        @self.router.get(
            "/mosaics/{mosaic_id}/WMTSCapabilities.xml", response_class=XMLResponse
        )
        def wmts(
            request: Request,
            mosaic_id: str = Path(..., description="MosaicId"),
            tile_format: ImageType = Query(
                ImageType.png, description="Output image type. Default is png."
            ),
            tile_scale: int = Query(
                1, gt=0, lt=4, description="Tile size scale. 1=256x256, 2=512x512..."
            ),
            minzoom: Optional[int] = Query(
                None, description="Overwrite default minzoom."
            ),
            maxzoom: Optional[int] = Query(
                None, description="Overwrite default maxzoom."
            ),
            layer_params=Depends(self.layer_dependency),  # noqa
            dataset_params=Depends(self.dataset_dependency),  # noqa
            pixel_selection=Depends(self.pixel_selection_dependency),  # noqa
            buffer: Optional[float] = Query(  # noqa
                None,
                gt=0,
                title="Tile buffer.",
                description="Buffer on each side of the given tile. It must be a multiple of `0.5`. Output **tilesize** will be expanded to `tilesize + 2 * tile_buffer` (e.g 0.5 = 257x257, 1.0 = 258x258).",
            ),
            post_process=Depends(self.process_dependency),  # noqa
            colormap=Depends(self.colormap_dependency),  # noqa
            render_params=Depends(self.render_dependency),  # noqa
            backend_params=Depends(self.backend_dependency),
            reader_params=Depends(self.reader_dependency),
            env=Depends(self.environment_dependency),
        ):
            """OGC WMTS endpoint."""
            route_params = {
                "mosaic_id": mosaic_id,
                "z": "{TileMatrix}",
                "x": "{TileCol}",
                "y": "{TileRow}",
                "scale": tile_scale,
                "format": tile_format.value,
            }
            tiles_url = self.url_for(request, "tile", **route_params)

            qs_key_to_remove = [
                "tile_format",
                "tile_scale",
                "minzoom",
                "maxzoom",
                "service",
                "request",
            ]
            qs = [
                (key, value)
                for (key, value) in request.query_params._list
                if key.lower() not in qs_key_to_remove
            ]
            if qs:
                tiles_url += f"?{urlencode(qs)}"

            mosaic_uri = mk_src_path(mosaic_id)
            with rasterio.Env(**env):
                with self.backend(
                    mosaic_uri,
                    reader=self.dataset_reader,
                    reader_options=reader_params.to_dict(),
                    **backend_params.to_dict(),
                ) as src_dst:
                    bounds = src_dst.bounds
                    minzoom = minzoom if minzoom is not None else src_dst.minzoom
                    maxzoom = maxzoom if maxzoom is not None else src_dst.maxzoom

            tms = morecantile.tms.get("WebMercatorQuad")

            tileMatrix = []
            for zoom in range(minzoom, maxzoom + 1):  # type: ignore
                matrix = tms.matrix(zoom)
                tm = f"""
                        <TileMatrix>
                            <ows:Identifier>{matrix.id}</ows:Identifier>
                            <ScaleDenominator>{matrix.scaleDenominator}</ScaleDenominator>
                            <TopLeftCorner>{matrix.pointOfOrigin[0]} {matrix.pointOfOrigin[1]}</TopLeftCorner>
                            <TileWidth>{matrix.tileWidth}</TileWidth>
                            <TileHeight>{matrix.tileHeight}</TileHeight>
                            <MatrixWidth>{matrix.matrixWidth}</MatrixWidth>
                            <MatrixHeight>{matrix.matrixHeight}</MatrixHeight>
                        </TileMatrix>"""
                tileMatrix.append(tm)

            return self.templates.TemplateResponse(
                "wmts.xml",
                {
                    "request": request,
                    "tiles_endpoint": tiles_url,
                    "bounds": bounds,
                    "tileMatrix": tileMatrix,
                    "tms": tms,
                    "title": "Mosaic",
                    "layer_name": mosaic_id,
                    "media_type": tile_format.mediatype,
                },
                media_type=MediaType.xml.value,
            )

        @self.router.get(
            "/mosaics/{mosaic_id}/point/{lon},{lat}",
            response_model=Point,
            response_class=JSONResponse,
            responses={200: {"description": "Return a value for a point"}},
        )
        def point(
            mosaic_id: str = Path(..., description="MosaicId"),
            lon: float = Path(..., description="Longitude"),
            lat: float = Path(..., description="Latitude"),
            # https://github.com/developmentseed/titiler/blob/17cdff2f0ddf08dbd9a47c2140b13c4bbcc30b6d/src/titiler/mosaic/titiler/mosaic/factory.py#L698-L727
            # coord_crs=Depends(CoordCRSParams), added in titiler>=0.12
            layer_params=Depends(self.layer_dependency),
            dataset_params=Depends(self.dataset_dependency),
            backend_params=Depends(self.backend_dependency),
            reader_params=Depends(self.reader_dependency),
            env=Depends(self.environment_dependency),
        ):
            """Get Point value for a Mosaic."""
            threads = int(os.getenv("MOSAIC_CONCURRENCY", MAX_THREADS))

            mosaic_uri = mk_src_path(mosaic_id)
            with rasterio.Env(**env):
                with self.backend(
                    mosaic_uri,
                    reader=self.dataset_reader,
                    reader_options=reader_params.to_dict(),
                    **backend_params.to_dict(),
                ) as src_dst:
                    values = src_dst.point(
                        lon,
                        lat,
                        threads=threads,
                        **layer_params.to_dict(),
                        **dataset_params.to_dict(),
                    )

            return {
                "coordinates": [lon, lat],
                "values": [
                    (src, pts.data.tolist(), pts.band_names) for src, pts in values
                ],
            }

        async def store(
            mosaic_id: str,
            mosaicjson: MosaicJSON,
            overwrite: bool,
        ) -> None:
            try:
                existing = await retrieve(mosaic_id)
            except Exception:
                existing = False

            if not overwrite and existing:
                raise StoreException("Attempting to create already existing mosaic")
            if overwrite and not existing:
                raise StoreException("Attempting to update non-existant mosaic")

            mosaic_uri = mk_src_path(mosaic_id)

            try:
                await wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        None,  # executor
                        mosaic_write,  # func
                        mosaic_uri,
                        mosaicjson,
                        overwrite,
                    ),
                    20,
                )
            except asyncio.TimeoutError:
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "Error: timeout storing mosaic in datastore",
                )

        def mosaic_write(
            mosaic_uri: str,
            mosaicjson: MosaicJSON,
            overwrite: bool,
        ) -> None:
            with self.backend(mosaic_uri, mosaic_def=mosaicjson) as mosaic:
                mosaic.write(overwrite=overwrite)

        async def retrieve(
            mosaic_id: str, include_tiles: bool = False
        ) -> Optional[MosaicJSON]:
            mosaic_uri = mk_src_path(mosaic_id)

            try:
                return await wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        None,  # executor
                        read_mosaicjson_sync,  # func
                        mosaic_uri,
                        include_tiles,
                    ),
                    20,
                )
            except asyncio.TimeoutError:
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "Error: timeout retrieving mosaic from datastore.",
                )
            except MosaicError:
                return None

        def read_mosaicjson_sync(mosaic_uri: str, include_tiles: bool) -> MosaicJSON:
            with self.backend(
                mosaic_uri,
                reader=self.dataset_reader,
                # TODO
                # **self.backend_options,  # Replaced with an endpoint dependency
            ) as mosaic:
                mosaicjson = mosaic.mosaic_def
                if include_tiles and isinstance(mosaic, DynamoDBBackend):
                    keys = (mosaic._fetch_dynamodb(qk) for qk in mosaic._quadkeys)
                    mosaicjson.tiles = {x["quadkey"]: x["assets"] for x in keys}
                return mosaicjson

        async def delete(mosaic_id: str) -> None:
            mosaic_uri = mk_src_path(mosaic_id)

            try:
                await wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        None,
                        delete_mosaicjson_sync,
                        mosaic_uri,  # executor  # func
                    ),
                    20,
                )
            except asyncio.TimeoutError:
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "Error: timeout deleting mosaic.",
                )

            return

        def delete_mosaicjson_sync(mosaic_uri: str) -> None:
            with self.backend(
                mosaic_uri,
                reader=self.dataset_reader,
                # TODO
                # **self.backend_options,  # Replaced with an endpoint dependency
            ) as mosaic:
                if isinstance(mosaic, DynamoDBBackend):
                    mosaic.delete()  # delete is only supported by DynamoDB
                else:
                    raise UnsupportedOperationException("Delete is not supported")


# todo: make this safer in case visual doesn't exist
# how to handle others?
# support for selection by role?
def asset_href(feature: dict, asset_name: str) -> str:
    """Get asset url."""
    if href := feature.get("assets", {}).get(asset_name, {}).get("href"):
        return href
    else:
        raise Exception(f"Asset with name '{asset_name}' could not be found.")


def mk_src_path(mosaic_id: str) -> str:
    """Return Mosaic Path."""
    if mosaic_config.backend == "dynamodb://":
        return f"{mosaic_config.backend}{mosaic_config.host}:{mosaic_id}"
    else:
        return f"{mosaic_config.backend}{mosaic_config.host}/{mosaic_id}{mosaic_config.format}"


async def mosaicjson_from_urls(urisrb: UrisRequestBody) -> MosaicJSON:
    """Create MosaicJSON from a list of COG URLs."""
    if len(urisrb.urls) > MAX_ITEMS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Error: a maximum of {MAX_ITEMS} URLs can be mosaiced.",
        )

    try:
        mosaicjson = await wait_for(
            asyncio.get_running_loop().run_in_executor(
                None,  # executor
                lambda: MosaicJSON.from_urls(
                    urls=urisrb.urls,
                    minzoom=urisrb.minzoom,
                    maxzoom=urisrb.maxzoom,
                    max_threads=int(
                        os.getenv("MOSAIC_CONCURRENCY", MAX_THREADS)
                    ),  # todo
                ),
            ),
            20,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Error: timeout reading URLs and generating MosaicJSON definition",
        )

    if mosaicjson is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Error: could not extract mosaic data",
        )

    mosaicjson.name = urisrb.name
    mosaicjson.description = urisrb.description
    mosaicjson.attribution = urisrb.attribution
    mosaicjson.version = urisrb if urisrb.version else "0.0.1"

    return mosaicjson


async def mosaicjson_from_stac_api_query(req: StacApiQueryRequestBody) -> MosaicJSON:
    """Create a mosaic from a STAC-API search request."""
    if not req.stac_api_root:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Error: stac_api_root field must be non-empty.",
        )

    try:
        try:
            features = await wait_for(
                asyncio.get_running_loop().run_in_executor(
                    None,
                    execute_stac_search,
                    req,  # executor  # func
                ),
                30,
            )

        except asyncio.TimeoutError:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Error: timeout executing STAC API search.",
            )

        except TooManyResultsException as e:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Error: too many results from STAC API Search: {e}",
            )

        if not features:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Error: STAC API Search returned no results.",
            )

        try:
            mosaicjson = await wait_for(
                asyncio.get_running_loop().run_in_executor(
                    None,
                    extract_mosaicjson_from_features,
                    features,
                    req.asset_name if req.asset_name else "visual",
                ),
                60,  # todo: how much time should/can it take?
            )

        except asyncio.TimeoutError:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Error: timeout reading a COG asset and generating MosaicJSON definition",
            )

        if mosaicjson is None:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Error: could not extract mosaic data",
            )

        mosaicjson.name = req.name
        mosaicjson.description = req.description
        mosaicjson.attribution = req.attribution
        mosaicjson.version = req if req.version else "0.0.1"

        return mosaicjson

    except HTTPException as e:
        raise e

    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Error: {e}")


MAX_ITEMS = 100


def execute_stac_search(mosaic_request: StacApiQueryRequestBody) -> List[dict]:
    """Send Search request to the stac-api."""
    try:
        search_result = Client.open(mosaic_request.stac_api_root).search(
            # **mosaic_request.dict(), ?? this feel a little unsafe
            ids=mosaic_request.ids,
            collections=mosaic_request.collections,
            datetime=mosaic_request.datetime,
            bbox=mosaic_request.bbox,
            intersects=mosaic_request.intersects,
            query=mosaic_request.query,
            max_items=MAX_ITEMS,
            limit=mosaic_request.limit if mosaic_request.limit else 100,
            # setting limit >500 causes an error https://github.com/stac-utils/pystac-client/issues/56
        )
        matched = search_result.matched()
        if matched > MAX_ITEMS:
            raise TooManyResultsException(
                f"too many results: {matched} Items matched, but only a maximum of {MAX_ITEMS} are allowed."
            )

        return search_result.items_as_collection().to_dict()["features"]

    except TooManyResultsException as e:
        raise e

    except Exception as e:
        raise Exception(f"STAC Search error: {e}")


# assumes all assets are uniform. get the min and max zoom from the first.
def extract_mosaicjson_from_features(
    features: List[dict], asset_name: str
) -> Optional[MosaicJSON]:
    """Get COG Min/Max Zoom from STAC items and create a MosaicJSON."""
    if features:
        try:
            with Reader(asset_href(features[0], asset_name)) as cog:
                info = cog.info()

            return MosaicJSON.from_features(
                features,
                minzoom=info.minzoom,
                maxzoom=info.maxzoom,
                accessor=partial(asset_href, asset_name=asset_name),
            )

        # when Item geometry is a MultiPolygon (instead of a Polygon), supermercado raises
        # handle error "local variable 'x' referenced before assignment"
        # supermercado/burntiles.py ", line 38, in _feature_extrema
        # as this method only handles Polygon, LineString, and Point :grimace:
        # https://github.com/mapbox/supermercado/issues/47
        except UnboundLocalError:
            raise Exception(
                "STAC Items likely have MultiPolygon geometry, and only Polygon is supported."
            )

        except Exception as e:
            raise Exception(f"Error extracting mosaic data from results: {e}")

    else:
        return None


async def populate_mosaicjson(
    request: Request,
    content_type: Optional[str] = Header(None),
) -> MosaicJSON:
    """Post MosaicJSON dependency."""
    body_json = await request.json()

    # Case 1: we received a MosaicJSON document
    if (
        not content_type
        or content_type == "application/json"
        or content_type == "application/json; charset=utf-8"
        or content_type == "application/vnd.titiler.mosaicjson+json"
    ):
        mosaicjson = MosaicJSON(**body_json)

    # Case 2: we received a list of URLs
    elif content_type == "application/vnd.titiler.urls+json":
        mosaicjson = await mosaicjson_from_urls(UrisRequestBody(**body_json))

    # Case 3: we received a stac-api search request
    elif content_type == "application/vnd.titiler.stac-api-query+json":
        mosaicjson = await mosaicjson_from_stac_api_query(
            StacApiQueryRequestBody(**body_json)
        )

    else:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Error: media in Content-Type header is not supported.",
        )

    return mosaicjson


def mk_mosaic_entity(mosaic_id, self_uri):
    """Mosaic Links response."""
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
                href=f"{self_uri}/tilejson.json",
                type="application/json",
                title="TileJSON",
            ),
            Link(
                rel="tiles",
                href=f"{self_uri}/tiles/{{z}}/{{x}}/{{y}}",
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
