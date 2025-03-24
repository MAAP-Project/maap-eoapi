"""
Handler for AWS Lambda.
"""

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from rio_tiler.io import STACReader
from titiler.core.factory import MultiBaseTilerFactory, TilerFactory
from titiler.extensions import (
    cogValidateExtension,
    cogViewerExtension,
    stacViewerExtension,
)
from titiler.pgstac.main import app  # noqa: E402

from eoapi.raster.factory import MosaicTilerFactory

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
# Include the /mosaics router (for legacy mosaicjson support)
#############################################################
mosaic = MosaicTilerFactory()
app.include_router(mosaic.router, tags=["MosaicJSON"])

########################################
# Redirect /mosaic requests to /searches
########################################
redirect_router = APIRouter()


@redirect_router.api_route(
    "/mosaic/{subpath:path}", methods=["GET", "POST"], status_code=307
)
async def redirect_to_searches(request: Request):
    new_path = request.url.path.replace("/mosaic", "/searches", 1)
    query_string = request.url.query
    if query_string:
        new_path = f"{new_path}?{query_string}"
    return RedirectResponse(url=new_path)


app.include_router(redirect_router)
