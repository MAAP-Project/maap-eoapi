"""AWS Lambda handler."""

import asyncio
import logging
import os

from eoapi.raster.utils import get_secret_dict

# Update postgres env variables before importing titiler.pgstac.main
pgstac_secret_arn = os.environ["PGSTAC_SECRET_ARN"]
pgbouncer_host = os.getenv("PGBOUNCER_HOST")

secret = get_secret_dict(pgstac_secret_arn)
os.environ.update(
    {
        "postgres_host": pgbouncer_host or secret["host"],
        "postgres_dbname": secret["dbname"],
        "postgres_user": secret["username"],
        "postgres_pass": secret["password"],
        "postgres_port": str(secret["port"]),
    }
)

from eoapi.raster.main import app
from mangum import Mangum
from titiler.pgstac.db import connect_to_db

logging.getLogger("mangum.lifespan").setLevel(logging.ERROR)
logging.getLogger("mangum.http").setLevel(logging.ERROR)


@app.on_event("startup")
async def startup_event() -> None:
    """Connect to database on startup."""
    await connect_to_db(app)


handler = Mangum(app, lifespan="off")

if "AWS_EXECUTION_ENV" in os.environ:
    loop = asyncio.get_event_loop()
    loop.run_until_complete(app.router.startup())
