"""AWS Lambda handler with SnapStart support."""

import asyncio
import json
import logging
import os
import re
import sys
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

# Track whether connection initialization has been done after restore
_connection_initialized = False


# Runtime hook for SnapStart - this is called before the snapshot is taken
def on_snap_start(event):
    """
    Runtime hook called by Lambda before taking a snapshot.
    We close database connections that shouldn't be in the snapshot.
    """
    logger.info("SnapStart: Preparing for snapshot")

    # Close any existing database connections before the snapshot is taken
    if hasattr(app, "state") and hasattr(app.state, "dbpool") and app.state.dbpool:
        logger.info("SnapStart: Closing database pool")
        try:
            app.state.dbpool.close()
            app.state.dbpool = None
            logger.info("SnapStart: Database pool closed successfully")
        except Exception as e:
            logger.error(f"SnapStart: Error closing database pool: {e}")

    logger.info("SnapStart: Snapshot preparation complete")
    return {"statusCode": 200}


# Register the runtime hook at module level - this is crucial for SnapStart
if "AWS_LAMBDA_RUNTIME_API" in os.environ:
    current_module = sys.modules[__name__]
    setattr(current_module, "on_snap_start", on_snap_start)


@app.on_event("startup")
async def startup_event() -> None:
    """Connect to database on startup."""
    start_time = time.monotonic()
    logger.info("FastAPI startup: Initializing application resources")

    # Connect to database
    db_start = time.monotonic()
    logger.info("FastAPI startup: Connecting to database")
    await connect_to_db(app, settings=pg_settings)
    logger.info(
        f"FastAPI startup: Database connected in {time.monotonic() - db_start:.3f}s"
    )

    # Initialize path templates for routing
    templates_start = time.monotonic()
    logger.info("FastAPI startup: Building route templates")
    app.state.path_templates = {}
    for route in app.routes:
        if isinstance(route, APIRoute):
            # replace : with _ to make it regexable
            route_path = route.path.replace(":", "__")
            pattern = re.sub(r"{([^}]+)}", r"(?P<\1>[^/]+)", route_path)
            app.state.path_templates[re.compile(f"^{pattern}$")] = route_path
    logger.info(
        f"FastAPI startup: Route templates built in {time.monotonic() - templates_start:.3f}s"
    )

    logger.info(
        f"FastAPI startup: Initialization completed in {time.monotonic() - start_time:.3f}s"
    )


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


# Create Mangum handler with lifespan="off" - we'll manage lifespan manually
mangum_handler = Mangum(app, lifespan="off")

# Run FastAPI startup events during Lambda initialization
if "AWS_EXECUTION_ENV" in os.environ:
    logger.info("Lambda Init: Running FastAPI startup events")
    loop = asyncio.get_event_loop()
    loop.run_until_complete(app.router.startup())
    logger.info("Lambda Init: FastAPI startup complete")


def handler(event, context):
    """
    Lambda handler with SnapStart support.
    Manages database connections for cold starts and SnapStart restoration.
    """
    global _connection_initialized

    start_time = time.monotonic()

    # Get Lambda execution context information
    initialization_type = os.environ.get("AWS_LAMBDA_INITIALIZATION_TYPE", "unknown")
    function_version = getattr(context, "function_version", "unknown")

    logger.info(
        f"Lambda invocation: "
        f"initialization_type={initialization_type}, "
        f"function_version={function_version}, "
        f"connection_initialized={_connection_initialized}"
    )

    # For SnapStart restorations or first invocations, initialize the DB connections
    if initialization_type == "snap-start" and not _connection_initialized:
        logger.info("SnapStart restoration detected - recreating database connections")

        try:
            # Get the event loop or create a new one
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # Close any existing pool (from snapshot)
            if hasattr(app.state, "dbpool") and app.state.dbpool:
                logger.info("Closing existing DB pool from snapshot")
                try:
                    app.state.dbpool.close()
                except Exception as e:
                    logger.warning(f"Error closing stale pool: {e}")
                app.state.dbpool = None

            # Create fresh connection pool
            logger.info("Creating new database connection pool")
            connection_start = time.monotonic()
            loop.run_until_complete(connect_to_db(app, settings=pg_settings))
            logger.info(
                f"Database connection established in {time.monotonic() - connection_start:.3f}s"
            )

            _connection_initialized = True

        except Exception as e:
            logger.error(f"Failed to initialize database connection: {e}")
            raise

        logger.info(
            f"SnapStart restoration processing completed in {time.monotonic() - start_time:.3f}s"
        )

    # Process the request using Mangum
    return mangum_handler(event, context)
