"""AWS Lambda handler."""

import asyncio
import inspect
import json
import logging
import os
import re
import time
from typing import Any, Dict
from urllib.parse import urlparse

from eoapi.raster.main import app
from eoapi.raster.utils import get_secret_dict
from fastapi import Request
from fastapi.routing import APIRoute
from mangum import Mangum
from psycopg_pool import ConnectionPool
from titiler.pgstac.settings import PostgresSettings

logging.getLogger("mangum.lifespan").setLevel(logging.ERROR)
logging.getLogger("mangum.http").setLevel(logging.ERROR)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

pgstac_secret_arn = os.environ["PGSTAC_SECRET_ARN"]
pgbouncer_host = os.getenv("PGBOUNCER_HOST")
secret = get_secret_dict(pgstac_secret_arn)


# Runtime hooks for SnapStart
def on_snap_start(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Runtime hook called by Lambda before taking a snapshot.
    We use this to close database connections that shouldn't be in the snapshot.
    """
    logger.info("on_snap_start: Preparing for snapshot")

    # Close any existing DB connections before snapshot
    if hasattr(app.state, "dbpool") and app.state.dbpool:
        logger.info("Closing DB pool before snapshot")
        try:
            app.state.dbpool.close()
            app.state.dbpool = None
        except Exception as e:
            logger.error(f"Error closing DB pool before snapshot: {e}")

    return {"statusCode": 200}


# Make sure Lambda can find this hook
if "AWS_LAMBDA_RUNTIME_API" in os.environ:
    # Register the runtime hook
    # Must be at module level, not inside a function
    current_module = inspect.getmodule(inspect.currentframe())
    setattr(current_module, "on_snap_start", on_snap_start)


pg_settings = PostgresSettings(
    postgres_host=pgbouncer_host or secret["host"],
    postgres_dbname=secret["dbname"],
    postgres_user=secret["username"],
    postgres_pass=secret["password"],
    postgres_port=secret["port"],
)

_pool_reset_done = False


async def connect_to_db(app, settings=None):
    """Connect to Database with true SnapStart awareness."""
    if not settings:
        settings = pg_settings

    initialization_type = os.environ.get("AWS_LAMBDA_INITIALIZATION_TYPE")
    is_snap_start = initialization_type == "snap-start"

    logger.info(f"DB connection requested (initialization_type: {initialization_type})")

    # For SnapStart restorations, use different connection settings
    if is_snap_start:
        logger.info("Using SnapStart-optimized connection settings")
        # Use optimized settings for connections after snapshot restore
        pool_kwargs = {
            "min_size": 1,
            "max_size": 5,
            "max_waiting": 10,
            "max_idle": 20,
            "num_workers": 1,
            "timeout": 5.0,  # Shorter timeout for SnapStart restored environments
            "kwargs": {
                "options": "-c search_path=pgstac,public -c application_name=pgstac-lambda-snapstart"
            },
        }
    else:
        logger.info("Using standard connection settings")
        # Use standard settings for cold starts or non-SnapStart environments
        pool_kwargs = {
            "min_size": settings.db_min_conn_size,
            "max_size": settings.db_max_conn_size,
            "max_waiting": settings.db_max_queries,
            "max_idle": settings.db_max_idle,
            "num_workers": settings.db_num_workers,
            "kwargs": {
                "options": "-c search_path=pgstac,public -c application_name=pgstac-lambda"
            },
        }

    # Create connection pool
    try:
        app.state.dbpool = ConnectionPool(
            conninfo=str(settings.database_url), open=True, **pool_kwargs
        )

        # Wait for pool to be ready with appropriate timeout
        timeout = 3.0 if is_snap_start else 5.0
        await asyncio.to_thread(lambda: app.state.dbpool.wait(timeout=timeout))

        # Validate the connection
        await asyncio.to_thread(validate_db_connection, app.state.dbpool, is_snap_start)

        logger.info(
            f"Database pool successfully initialized: {pool_status(app.state.dbpool)}"
        )
        return True

    except Exception as e:
        logger.error(f"Database connection failed: {str(e)}")
        raise


def pool_status(pool):
    """Get pool status information for logging."""
    try:
        return f"size={pool.get_stats()['size']}, idle={pool.get_stats()['idle']}"
    except:
        return "stats unavailable"


def validate_db_connection(pool, is_snap_start):
    """Validate DB connection with special handling for SnapStart."""
    with pool.connection() as conn:
        with conn.cursor() as cursor:
            # Basic connection test
            cursor.execute("SELECT 1")
            result = cursor.fetchone()[0]

            # If this is a SnapStart restore, perform additional validation
            if is_snap_start:
                # Check that we can reach PostgreSQL through PgBouncer
                cursor.execute("SELECT pg_is_in_recovery()")
                in_recovery = cursor.fetchone()[0]

                # Check transaction isolation level - should be reset after restore
                cursor.execute("SHOW transaction_isolation")
                isolation = cursor.fetchone()[0]

                # Log detailed connection state for debugging
                cursor.execute("SELECT pg_backend_pid()")
                pid = cursor.fetchone()[0]

                logger.info(
                    f"SnapStart DB validation: pid={pid}, recovery={in_recovery}, isolation={isolation}"
                )

            return result == 1


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
    """Lambda handler function with improved SnapStart awareness."""
    global _pool_reset_done

    start_time = time.monotonic()
    initialization_type = os.environ.get("AWS_LAMBDA_INITIALIZATION_TYPE")
    is_snap_start = initialization_type == "snap-start"

    # Log key context information for debugging
    request_id = context.aws_request_id if context else "unknown"
    logger.info(
        f"Handler invoked: request_id={request_id}, init_type={initialization_type}"
    )

    # For SnapStart restorations or cold starts where we need to initialize the pool
    if (is_snap_start or initialization_type == "on-demand") and not _pool_reset_done:
        logger.info(
            f"{'SnapStart restoration' if is_snap_start else 'Cold start'} - initializing resources"
        )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Clean shutdown of any existing connections
        if hasattr(app.state, "dbpool") and app.state.dbpool:
            logger.info("Closing existing DB pool before re-initialization")
            try:
                app.state.dbpool.close()
            except Exception as e:
                logger.warning(f"Error closing existing pool: {e}")
            app.state.dbpool = None

        # Initialize fresh DB connections
        logger.info("Creating fresh DB connections")
        try:
            loop.run_until_complete(connect_to_db(app, settings=pg_settings))
            _pool_reset_done = True
            logger.info(
                f"Resource initialization completed in {(time.monotonic() - start_time):.4f}s"
            )
        except Exception as e:
            logger.error(f"FATAL: Resource initialization failed: {e}")
            raise e

    # Process the actual request
    return mangum_handler(event, context)
