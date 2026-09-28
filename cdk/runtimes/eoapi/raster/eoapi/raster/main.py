"""
Handler for AWS Lambda.
"""

from cogeo_mosaic.backends import MosaicBackend
from fastapi import Request
from rio_tiler.io import STACReader
from titiler.core.factory import MultiBaseTilerFactory, TilerFactory
from titiler.extensions import (
    cogValidateExtension,
    cogViewerExtension,
    stacViewerExtension,
)
from titiler.mosaic.extensions.mosaicjson import MosaicJSONExtension
from titiler.mosaic.extensions.wmts import wmtsExtension
from titiler.mosaic.factory import MosaicTilerFactory
from titiler.pgstac.main import app  # noqa: E402

from eoapi.raster.factory import (
    mosaic_path,
    redirect_collection_compatibility,
)
from eoapi.raster.factory import (
    router as custom_mosaic_router,
)

########################################
# Include the /cog router
########################################
cog = TilerFactory(
    router_prefix="/cog",
    extensions=[
        cogValidateExtension(),
        cogViewerExtension(),
    ],
)

app.include_router(
    cog.router,
    prefix="/cog",
    tags=["Cloud Optimized GeoTIFF"],
)


################################################
# Include the /stac router (for stac_ipyleaflet)
################################################
stac = MultiBaseTilerFactory(
    reader=STACReader,
    router_prefix="/stac",
    extensions=[
        stacViewerExtension(),
    ],
)

app.include_router(
    stac.router,
    prefix="/stac",
    tags=["SpatioTemporal Asset Catalog"],
)

#############################################################
# Include native MosaicJSON tiling routes and the custom storage resource API.
mosaic = MosaicTilerFactory(
    backend=MosaicBackend,
    path_dependency=mosaic_path,
    router_prefix="/mosaics/{mosaic_id}",
    add_statistics=True,
    add_part=True,
    extensions=[MosaicJSONExtension(), wmtsExtension()],
)
app.include_router(mosaic.router, prefix="/mosaics/{mosaic_id}", tags=["MosaicJSON"])
app.include_router(custom_mosaic_router)


@app.middleware("http")
async def redirect_legacy_collection_routes(request: Request, call_next):
    """Redirect old collection paths before native pgSTAC dependencies run."""
    if response := redirect_collection_compatibility(request):
        return response
    return await call_next(request)
