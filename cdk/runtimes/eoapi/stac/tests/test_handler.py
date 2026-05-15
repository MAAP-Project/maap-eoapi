"""Handler tests for the MAAP STAC runtime."""

from __future__ import annotations

import asyncio

import pytest
from stac_fastapi.pgstac.config import PostgresSettings

from eoapi.stac import handler


class FakePool:
    """Simple pool stub that records close calls."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        """Mark the pool as closed."""
        self.closed = True


@pytest.fixture(autouse=True)
def clear_handler_state() -> None:
    """Reset handler globals and app state between tests."""
    original_readpool = getattr(handler.app.state, "readpool", None)
    original_writepool = getattr(handler.app.state, "writepool", None)
    original_initialized = handler._CONNECTIONS_INITIALIZED
    original_with_transactions = handler.WITH_COLLECTION_TRANSACTIONS
    handler.app.state.readpool = None
    handler.app.state.writepool = None
    handler._CONNECTIONS_INITIALIZED = False
    yield
    handler.app.state.readpool = original_readpool
    handler.app.state.writepool = original_writepool
    handler._CONNECTIONS_INITIALIZED = original_initialized
    handler.WITH_COLLECTION_TRANSACTIONS = original_with_transactions


def test_build_postgres_settings_requires_secret_arn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handler should fail clearly without PGSTAC_SECRET_ARN."""
    monkeypatch.delenv("PGSTAC_SECRET_ARN", raising=False)

    with pytest.raises(RuntimeError, match="PGSTAC_SECRET_ARN"):
        handler._build_postgres_settings()


def test_build_postgres_settings_loads_secret_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handler should map secret fields into Postgres settings."""
    monkeypatch.setenv("PGSTAC_SECRET_ARN", "pg-secret")
    monkeypatch.setattr(
        handler,
        "load_secret_dict",
        lambda secret_arn: {
            "host": "db.internal",
            "dbname": "pgstac",
            "username": "reader",
            "password": "secret",
            "port": "5432",
        },
    )

    settings = handler._build_postgres_settings()

    assert settings == PostgresSettings(
        pghost="db.internal",
        pgdatabase="pgstac",
        pguser="reader",
        pgpassword="secret",
        pgport=5432,
    )


def test_on_snapshot_closes_existing_pools() -> None:
    """Snapshot preparation should close and clear both pools."""
    readpool = FakePool()
    writepool = FakePool()
    handler.app.state.readpool = readpool
    handler.app.state.writepool = writepool

    response = handler.on_snapshot()

    assert response == {"statusCode": 200}
    assert readpool.closed is True
    assert writepool.closed is True
    assert handler.app.state.readpool is None
    assert handler.app.state.writepool is None


@pytest.mark.parametrize("with_transactions", [False, True])
def test_on_snap_restore_reconnects_with_expected_write_pool_setting(
    monkeypatch: pytest.MonkeyPatch,
    with_transactions: bool,
) -> None:
    """Snapshot restore should reconnect with the correct write-pool flag."""
    captured: dict[str, object] = {}

    async def fake_connect_to_db(
        app: object,
        *,
        postgres_settings: object,
        add_write_connection_pool: bool,
    ) -> None:
        captured["app"] = app
        captured["postgres_settings"] = postgres_settings
        captured["add_write_connection_pool"] = add_write_connection_pool

    settings = PostgresSettings(
        pghost="db.internal",
        pgdatabase="pgstac",
        pguser="reader",
        pgpassword="secret",
        pgport=5432,
    )
    handler.WITH_COLLECTION_TRANSACTIONS = with_transactions
    monkeypatch.setattr(handler, "connect_to_db", fake_connect_to_db)
    monkeypatch.setattr(handler, "_build_postgres_settings", lambda: settings)

    response = handler.on_snap_restore()

    assert response == {"statusCode": 200}
    assert handler._CONNECTIONS_INITIALIZED is True
    assert captured == {
        "app": handler.app,
        "postgres_settings": settings,
        "add_write_connection_pool": with_transactions,
    }


@pytest.mark.parametrize("with_transactions", [False, True])
def test_startup_event_connects_with_expected_write_pool_setting(
    monkeypatch: pytest.MonkeyPatch,
    with_transactions: bool,
) -> None:
    """Startup should reuse the same write-pool gate as restore."""
    captured: dict[str, object] = {}

    async def fake_connect_to_db(
        app: object,
        *,
        postgres_settings: object,
        add_write_connection_pool: bool,
    ) -> None:
        captured["app"] = app
        captured["postgres_settings"] = postgres_settings
        captured["add_write_connection_pool"] = add_write_connection_pool

    settings = PostgresSettings(
        pghost="db.internal",
        pgdatabase="pgstac",
        pguser="reader",
        pgpassword="secret",
        pgport=5432,
    )
    handler.WITH_COLLECTION_TRANSACTIONS = with_transactions
    monkeypatch.setattr(handler, "connect_to_db", fake_connect_to_db)
    monkeypatch.setattr(handler, "_build_postgres_settings", lambda: settings)

    asyncio.run(handler.startup_event())

    assert captured == {
        "app": handler.app,
        "postgres_settings": settings,
        "add_write_connection_pool": with_transactions,
    }


def test_shutdown_event_closes_db_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown should delegate to the shared close helper."""
    captured: dict[str, object] = {}

    async def fake_close_db_connection(app: object) -> None:
        captured["app"] = app

    monkeypatch.setattr(handler, "close_db_connection", fake_close_db_connection)

    asyncio.run(handler.shutdown_event())

    assert captured == {"app": handler.app}
