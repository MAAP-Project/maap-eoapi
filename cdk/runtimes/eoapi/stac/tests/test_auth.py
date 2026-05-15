"""Authentication tests for collection transaction routes."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.security import HTTPBasicCredentials
from fastapi.testclient import TestClient
from pydantic import ValidationError

from eoapi.stac import auth
from eoapi.stac.main import COLLECTION_TRANSACTION_EXTENSION, create_app


@pytest.fixture(autouse=True)
def reload_transaction_auth_settings() -> None:
    """Refresh auth settings after env changes in each test."""
    auth.reset_transaction_auth_state()
    yield
    auth.reset_transaction_auth_state()


@pytest.fixture
def basic_auth_secret_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure basic auth to read credentials from Secrets Manager."""
    monkeypatch.setenv("MAAP_TRANSACTION_AUTH_MODE", "basic")
    monkeypatch.setenv("MAAP_TRANSACTION_AUTH_SECRET_ARN", "test-secret-arn")
    monkeypatch.delenv("MAAP_TRANSACTION_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("MAAP_TRANSACTION_AUTH_PASSWORD", raising=False)
    auth.reset_transaction_auth_state()


@pytest.fixture
def basic_auth_env_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure basic auth to read credentials directly from env vars."""
    monkeypatch.setenv("MAAP_TRANSACTION_AUTH_MODE", "basic")
    monkeypatch.delenv("MAAP_TRANSACTION_AUTH_SECRET_ARN", raising=False)
    monkeypatch.setenv("MAAP_TRANSACTION_AUTH_USERNAME", "bob")
    monkeypatch.setenv("MAAP_TRANSACTION_AUTH_PASSWORD", "builder")
    auth.reset_transaction_auth_state()


@pytest.fixture
def collection_transaction_app(
    monkeypatch: pytest.MonkeyPatch,
    basic_auth_env_credentials: None,
) -> Iterator[TestClient]:
    """Build a transaction-enabled app using env-provided credentials."""
    app = create_app(
        enabled_extensions={"query", "collection_search", COLLECTION_TRANSACTION_EXTENSION},
        connect_to_database=False,
    )
    with TestClient(app) as client:
        yield client


def test_require_transaction_auth_accepts_valid_basic_credentials(
    monkeypatch: pytest.MonkeyPatch,
    basic_auth_env_credentials: None,
) -> None:
    """Valid basic auth credentials should satisfy the dependency."""

    credentials = HTTPBasicCredentials(username="bob", password="builder")

    asyncio.run(auth.require_transaction_auth(credentials))


def test_collection_transaction_routes_require_auth(
    collection_transaction_app: TestClient,
) -> None:
    """Transaction routes should challenge unauthenticated requests."""
    response = collection_transaction_app.post("/collections", json={})

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Basic"


def test_invalid_basic_auth_is_rejected(
    collection_transaction_app: TestClient,
) -> None:
    """Invalid basic auth credentials should be rejected."""
    response = collection_transaction_app.post(
        "/collections",
        json={},
        auth=("alice", "wonderland"),
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Basic"


def test_read_routes_do_not_require_transaction_auth(
    collection_transaction_app: TestClient,
) -> None:
    """Read routes should not inherit the transaction auth dependency."""
    collections_get_route = next(
        route
        for route in collection_transaction_app.app.routes
        if getattr(route, "path", None) == "/collections"
        and "GET" in getattr(route, "methods", set())
    )

    assert collections_get_route.dependencies == []


def test_collection_write_routes_receive_transaction_auth_dependency(
    collection_transaction_app: TestClient,
) -> None:
    """Collection write routes should receive the auth dependency."""
    protected_routes = {
        (route.path, next(iter(route.methods))): route
        for route in collection_transaction_app.app.routes
        if getattr(route, "path", None) in {"/collections", "/collections/{collection_id}"}
        and getattr(route, "methods", None)
        and next(iter(route.methods)) in {"POST", "PUT", "PATCH", "DELETE"}
    }

    assert set(protected_routes) == {
        ("/collections", "POST"),
        ("/collections/{collection_id}", "PUT"),
        ("/collections/{collection_id}", "PATCH"),
        ("/collections/{collection_id}", "DELETE"),
    }
    for route in protected_routes.values():
        assert len(route.dependencies) == 1
        assert route.dependencies[0].dependency == auth.require_transaction_auth


def test_transaction_enabled_app_accepts_secret_manager_credentials(
    monkeypatch: pytest.MonkeyPatch,
    basic_auth_secret_env: None,
) -> None:
    """Secrets Manager credentials should still be supported."""
    monkeypatch.setattr(
        auth,
        "load_secret_dict",
        lambda secret_arn: {"username": "bob", "password": "builder"},
    )

    app = create_app(
        enabled_extensions={COLLECTION_TRANSACTION_EXTENSION},
        connect_to_database=False,
    )

    assert app is not None


def test_transaction_enabled_app_fails_closed_without_any_basic_auth_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing basic-auth config should fail app creation."""
    monkeypatch.setenv("MAAP_TRANSACTION_AUTH_MODE", "basic")
    monkeypatch.delenv("MAAP_TRANSACTION_AUTH_SECRET_ARN", raising=False)
    monkeypatch.delenv("MAAP_TRANSACTION_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("MAAP_TRANSACTION_AUTH_PASSWORD", raising=False)
    auth.reset_transaction_auth_state()

    with pytest.raises(RuntimeError, match="MAAP_TRANSACTION_AUTH_USERNAME"):
        create_app(
            enabled_extensions={COLLECTION_TRANSACTION_EXTENSION},
            connect_to_database=False,
        )


def test_transaction_enabled_app_fails_closed_for_unsupported_auth_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported auth modes should fail app creation."""
    monkeypatch.setenv("MAAP_TRANSACTION_AUTH_MODE", "none")
    monkeypatch.setenv("MAAP_TRANSACTION_AUTH_SECRET_ARN", "test-secret-arn")

    with pytest.raises(ValidationError, match="Input should be 'basic'"):
        auth.reset_transaction_auth_state()
