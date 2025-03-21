"""AWS Lambda handler."""

import asyncio
import json
import logging
import os
import re

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
            # Extract original parameter names
            original_params = re.findall(r"{([^}]+)}", route.path)

            # Create pattern with sanitized parameter names
            pattern = route.path
            for param in original_params:
                # Replace special chars with underscore for the regex group name
                safe_name = re.sub(r"[^0-9a-zA-Z_]", "_", param)
                # Replace the param in the pattern
                pattern = pattern.replace(f"{{{param}}}", f"(?P<{safe_name}>[^/]+)")

            # Store the mapping of regex pattern to original route path
            app.state.path_templates[re.compile(f"^{pattern}$")] = {
                "template": route.path,
                "param_mapping": {
                    re.sub(r"[^0-9a-zA-Z_]", "_", p): p for p in original_params
                },
            }


@app.middleware("http")
async def log_request_data(request: Request, call_next):
    path = request.url.path
    method = request.method
    query_params = dict(request.query_params)

    # Extract path parameters
    path_template = path
    path_params = {}

    for pattern, route_info in app.state.path_templates.items():
        match = pattern.match(path)
        if match:
            path_template = route_info["template"]
            # Get regex-captured params
            captured_params = match.groupdict()
            # Map back to original parameter names
            for safe_name, value in captured_params.items():
                original_name = route_info["param_mapping"].get(safe_name, safe_name)
                path_params[original_name] = value
            break

    log_data = {
        "method": method,
        "path_template": path_template,
        "path": path,
        "path_params": path_params,
        "query_params": query_params,
    }

    logger.info(f"Request: {json.dumps(log_data)}")

    response = await call_next(request)
    return response


handler = Mangum(app, lifespan="off")

if "AWS_EXECUTION_ENV" in os.environ:
    loop = asyncio.get_event_loop()
    loop.run_until_complete(app.router.startup())
