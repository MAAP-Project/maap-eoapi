"""AWS Lambda handler."""

import asyncio
import json
import logging
import os
import re
from urllib.parse import urlparse

from eoapi.raster.main import app
from eoapi.raster.utils import get_secret_dict
from fastapi import Request
from fastapi.routing import APIRoute
from mangum import Mangum
from titiler.pgstac.db import connect_to_db
from titiler.pgstac.settings import PostgresSettings

logging.getLogger("mangum.lifespan").setLevel(logging.ERROR)
logging.getLogger("mangum.http").setLevel(logging.ERROR)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

pgstac_secret_arn = os.environ["PGSTAC_SECRET_ARN"]
pgbouncer_host = os.getenv("PGBOUNCER_HOST")
secret = get_secret_dict(pgstac_secret_arn)

pg_settings = PostgresSettings(
    postgres_host=pgbouncer_host or secret["host"],
    postgres_dbname=secret["dbname"],
    postgres_user=secret["username"],
    postgres_pass=secret["password"],
    postgres_port=secret["port"],
)


@app.on_event("startup")
async def startup_event() -> None:
    """Connect to database on startup."""
    await connect_to_db(app, settings=pg_settings)

    app.state.path_templates = {}
    for route in app.routes:
        if isinstance(route, APIRoute):
            # replace : with _ to make it regexable
            route_path = route.path.replace(":", "__")
            pattern = re.sub(r"{([^}]+)}", r"(?P<\1>[^/]+)", route_path)
            app.state.path_templates[re.compile(f"^{pattern}$")] = route_path


@app.middleware("http")
async def log_request_data(request: Request, call_next):
    path = request.url.path
    method = request.method
    query_params = dict(request.query_params)

    # Extract path parameters
    route = path
    path_params = {}

    for pattern, _route in app.state.path_templates.items():
        match = pattern.match(path)
        if match:
            route = _route
            path_params = match.groupdict()
            break

    log_data = {
        "method": method,
        "route": route,
        "path": path,
        "path_params": path_params,
        "query_params": query_params,
        "url_scheme": None,
        "url_netloc": None,
    }

    if url := query_params.get("url"):
        url_parsed = urlparse(url)
        log_data["url_scheme"] = url_parsed.scheme
        log_data["url_netloc"] = url_parsed.netloc

    logger.info(f"Request: {json.dumps(log_data)}")

    response = await call_next(request)
    return response


handler = Mangum(app, lifespan="off")

if "AWS_EXECUTION_ENV" in os.environ:
    loop = asyncio.get_event_loop()
    loop.run_until_complete(app.router.startup())
