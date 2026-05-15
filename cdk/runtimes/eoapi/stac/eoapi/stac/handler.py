"""AWS Lambda handler for the MAAP STAC runtime."""

from __future__ import annotations

import asyncio
import logging
import os

from mangum import Mangum
from stac_fastapi.pgstac.config import PostgresSettings
from stac_fastapi.pgstac.db import close_db_connection, connect_to_db

from eoapi.stac.auth import load_secret_dict
from eoapi.stac.main import (
    COLLECTION_TRANSACTION_EXTENSION,
    app,
    parse_enabled_extensions,
)

try:
    from snapshot_restore_py import register_after_restore, register_before_snapshot
except ImportError:

    def register_before_snapshot(func):
        """Fallback decorator when snapshot_restore_py is unavailable."""
        return func

    def register_after_restore(func):
        """Fallback decorator when snapshot_restore_py is unavailable."""
        return func


logger = logging.getLogger(__name__)


_CONNECTIONS_INITIALIZED = False
WITH_COLLECTION_TRANSACTIONS = (
    COLLECTION_TRANSACTION_EXTENSION
    in parse_enabled_extensions(os.environ.get("ENABLED_EXTENSIONS"))
)


def _build_postgres_settings() -> PostgresSettings:
    """Fetch pgSTAC credentials from Secrets Manager."""
    secret_arn = os.getenv("PGSTAC_SECRET_ARN")
    if not secret_arn:
        raise RuntimeError("PGSTAC_SECRET_ARN must be set for the STAC Lambda runtime")

    logger.info("Loading pgSTAC connection secret")
    secret = load_secret_dict(secret_arn)
    return PostgresSettings(
        pghost=secret["host"],
        pgdatabase=secret["dbname"],
        pguser=secret["username"],
        pgpassword=secret["password"],
        pgport=int(secret["port"]),
    )


def _close_pool(pool_name: str) -> None:
    """Close a database pool on the global app state if it exists."""
    pool = getattr(app.state, pool_name, None)
    if not pool:
        return

    try:
        pool.close()
    except Exception:
        logger.exception("SnapStart: error closing %s", pool_name)
    finally:
        setattr(app.state, pool_name, None)


def _close_pools() -> None:
    """Close both read and write pools if they exist."""
    _close_pool("readpool")
    _close_pool("writepool")


async def _initialize_db_connections() -> None:
    """Initialize database pools for the Lambda runtime."""
    await connect_to_db(
        app,
        postgres_settings=_build_postgres_settings(),
        add_write_connection_pool=WITH_COLLECTION_TRANSACTIONS,
    )


def _initialize_db_connections_sync(*, close_existing_pools: bool = False) -> None:
    """Initialize database pools from synchronous Lambda hooks."""
    global _CONNECTIONS_INITIALIZED

    if close_existing_pools:
        _close_pools()

    asyncio.run(_initialize_db_connections())
    _CONNECTIONS_INITIALIZED = True


@register_before_snapshot
def on_snapshot():
    """Close DB pools before the Lambda snapshot is taken."""
    _close_pools()
    return {"statusCode": 200}


@register_after_restore
def on_snap_restore():
    """Recreate DB pools after a Lambda snapshot restore."""
    try:
        _initialize_db_connections_sync(close_existing_pools=True)
    except Exception:
        logger.exception("SnapStart: failed to initialize database connection")
        raise

    return {"statusCode": 200}


@app.on_event("startup")
async def startup_event() -> None:
    """Connect to the database when the app starts."""
    logger.info("Setting up DB connection")
    await _initialize_db_connections()
    logger.info("DB connection setup complete")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    """Close database pools during shutdown."""
    logger.info("Closing DB connection")
    await close_db_connection(app)
    logger.info("DB connection closed")


handler = Mangum(
    app,
    lifespan="off",
    text_mime_types=["text/", "application/"],
)


if "AWS_EXECUTION_ENV" in os.environ and not _CONNECTIONS_INITIALIZED:
    logger.info("Cold start: initializing database connection")
    _initialize_db_connections_sync()
    logger.info("Database connection initialized")
