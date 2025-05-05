"""AWS Lambda handler."""

import asyncio
import json
import logging
import os
import re
import time
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

_pool_reset_done = False


@app.on_event("startup")
async def startup_event() -> None:
    """Connect to database on startup."""
    print("Lambda Init: Connecting to DB and creating pool...")
    await connect_to_db(app, settings=pg_settings)
    print("Lambda Init: DB Pool created.")

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

    referer = request.headers.get("referer") or request.headers.get("referrer")
    origin = request.headers.get("origin")

    # find generic route path, fall back to actual route path if no match found
    route = path

    # re-map /mosaic requests to new /searches/
    route = route.replace("/mosaic/", "/searches/")

    path_params = {}

    for pattern, _route in app.state.path_templates.items():
        match = pattern.match(route)
        if match:
            route = _route
            path_params = match.groupdict()
            break

    log_data = {
        "method": method,
        "referer": referer,
        "origin": origin,
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


mangum_handler = Mangum(app, lifespan="off")

if "AWS_EXECUTION_ENV" in os.environ:
    print("Lambda Init: Running FastAPI startup events...")
    loop = asyncio.get_event_loop()
    loop.run_until_complete(app.router.startup())
    print("Lambda Init: FastAPI startup complete.")


def handler(event, context):
    """
    Lambda handler function with post-SnapStart restore logic.
    """
    global _pool_reset_done

    start_time = time.monotonic()

    if not _pool_reset_done:
        print("First invocation after restore OR cold start: Resetting DB connections.")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if hasattr(app.state, "dbpool") and app.state.dbpool:
            print("Closing existing DB pool...")
            try:
                app.state.dbpool.close()
                print("Existing DB pool closed.")
            except Exception as e:
                print(f"Error closing potentially stale pool: {e}")
            app.state.dbpool = None

        print("Re-initializing DB pool...")
        try:
            loop.run_until_complete(connect_to_db(app, settings=pg_settings))
            print("DB Pool re-initialized successfully.")
            _pool_reset_done = True  # Mark reset as done for this environment instance
        except Exception as e:
            print(f"FATAL: Failed to re-initialize DB pool after restore: {e}")
            raise e

        print(f"Post-restore pool reset took: {(time.monotonic() - start_time):.4f}s")

    return mangum_handler(event, context)
