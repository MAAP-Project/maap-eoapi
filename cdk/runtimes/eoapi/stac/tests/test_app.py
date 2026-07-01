"""Application tests for the MAAP STAC runtime."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from eoapi.stac import auth
from eoapi.stac.main import (
    CATALOG_TRANSACTION_EXTENSION,
    CATALOGS_EXTENSION,
    COLLECTION_TRANSACTION_EXTENSION,
    create_app,
    parse_enabled_extensions,
)


@pytest.fixture(autouse=True)
def reload_transaction_auth_settings() -> None:
    """Refresh auth settings after env changes in each test."""
    auth.reset_transaction_auth_state()
    yield
    auth.reset_transaction_auth_state()


@pytest.fixture
def collection_transaction_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Build a test client with collection transactions enabled."""
    monkeypatch.setenv("MAAP_TRANSACTION_AUTH_MODE", "basic")
    monkeypatch.delenv("MAAP_TRANSACTION_AUTH_SECRET_ARN", raising=False)
    monkeypatch.setenv("MAAP_TRANSACTION_AUTH_USERNAME", "bob")
    monkeypatch.setenv("MAAP_TRANSACTION_AUTH_PASSWORD", "builder")
    auth.reset_transaction_auth_state()
    app = create_app(
        enabled_extensions={
            "query",
            "sort",
            "collection_search",
            COLLECTION_TRANSACTION_EXTENSION,
        },
        connect_to_database=False,
    )
    with TestClient(app) as client:
        yield client


def test_read_only_app_omits_collection_transaction_routes() -> None:
    """The app should stay read-only when collection transactions are disabled."""
    app = create_app(
        enabled_extensions={"query", "sort", "collection_search"},
        connect_to_database=False,
    )
    openapi = app.openapi()

    assert "/collections" in openapi["paths"]
    assert "get" in openapi["paths"]["/collections"]
    assert "post" not in openapi["paths"]["/collections"]
    assert "/collections/{collection_id}" in openapi["paths"]
    assert set(openapi["paths"]["/collections/{collection_id}"].keys()) == {"get"}
    assert "/collections/{collection_id}/items" in openapi["paths"]
    assert set(openapi["paths"]["/collections/{collection_id}/items"].keys()) == {"get"}
    assert "/collections/{collection_id}/items/{item_id}" in openapi["paths"]
    assert set(
        openapi["paths"]["/collections/{collection_id}/items/{item_id}"].keys()
    ) == {"get"}


def test_catalog_routes_are_enabled_by_default() -> None:
    """Default extension parsing should include read-only catalog routes."""
    app = create_app(connect_to_database=False)
    openapi = app.openapi()

    assert "/catalogs" in openapi["paths"]
    assert set(openapi["paths"]["/catalogs"].keys()) == {"get"}
    assert "/catalogs/{catalog_id}" in openapi["paths"]
    assert set(openapi["paths"]["/catalogs/{catalog_id}"].keys()) == {"get"}
    assert "/catalogs/{catalog_id}/collections/{collection_id}/items" in openapi["paths"]
    assert "post" not in openapi["paths"]["/catalogs"]


def test_landing_page_lists_catalogs_as_child_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catalogs should be exposed as child links for STAC Browser discovery."""

    async def fake_get_all_catalogs(self, token, limit, request, sort=None):
        return (
            [
                {"id": "maap-demo", "title": "MAAP Demo Catalog"},
                {"id": "user-hrodmn", "title": "hrodmn DPS Outputs"},
            ],
            2,
            None,
        )

    monkeypatch.setattr(
        "stac_fastapi.pgstac.extensions.catalogs.catalogs_database_logic."
        "CatalogsDatabaseLogic.get_all_catalogs",
        fake_get_all_catalogs,
    )
    app = create_app(enabled_extensions={CATALOGS_EXTENSION}, connect_to_database=False)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    links = response.json()["links"]
    assert next(link for link in links if link["rel"] == "catalogs")["href"] == (
        "http://testserver/catalogs"
    )
    assert {
        (link["rel"], link.get("title"), link["href"])
        for link in links
        if link["rel"] == "child"
    } == {
        ("child", "MAAP Demo Catalog", "http://testserver/catalogs/maap-demo"),
        ("child", "hrodmn DPS Outputs", "http://testserver/catalogs/user-hrodmn"),
    }


def test_catalog_routes_can_be_disabled() -> None:
    """Explicit extension configuration should be able to omit catalogs."""
    app = create_app(
        enabled_extensions={"query", "sort", "collection_search"},
        connect_to_database=False,
    )
    openapi = app.openapi()

    assert all(not path.startswith("/catalogs") for path in openapi["paths"])


def test_catalog_transactions_are_opt_in() -> None:
    """Read-only catalogs should not expose catalog write routes."""
    app = create_app(
        enabled_extensions={CATALOGS_EXTENSION},
        connect_to_database=False,
    )
    openapi = app.openapi()

    assert set(openapi["paths"]["/catalogs"].keys()) == {"get"}
    assert set(openapi["paths"]["/catalogs/{catalog_id}"].keys()) == {"get"}
    assert set(openapi["paths"]["/catalogs/{catalog_id}/collections"].keys()) == {
        "get"
    }


def test_catalog_transaction_routes_require_catalogs() -> None:
    """Catalog write routes should fail closed when catalogs are disabled."""
    with pytest.raises(ValueError, match="catalog_transaction requires catalogs"):
        create_app(
            enabled_extensions={CATALOG_TRANSACTION_EXTENSION},
            connect_to_database=False,
        )


def test_catalog_transaction_app_registers_catalog_write_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catalog transactions should register the documented catalog write routes."""
    monkeypatch.setenv("MAAP_TRANSACTION_AUTH_MODE", "basic")
    monkeypatch.delenv("MAAP_TRANSACTION_AUTH_SECRET_ARN", raising=False)
    monkeypatch.setenv("MAAP_TRANSACTION_AUTH_USERNAME", "bob")
    monkeypatch.setenv("MAAP_TRANSACTION_AUTH_PASSWORD", "builder")
    auth.reset_transaction_auth_state()
    app = create_app(
        enabled_extensions={CATALOGS_EXTENSION, CATALOG_TRANSACTION_EXTENSION},
        connect_to_database=False,
    )
    openapi = app.openapi()

    assert set(openapi["paths"]["/catalogs"].keys()) == {"get", "post"}
    assert set(openapi["paths"]["/catalogs/{catalog_id}"].keys()) == {
        "get",
        "put",
        "delete",
    }
    assert set(openapi["paths"]["/catalogs/{catalog_id}/collections"].keys()) == {
        "get",
        "post",
    }
    assert set(
        openapi["paths"]["/catalogs/{catalog_id}/collections/{collection_id}"].keys()
    ) == {"get", "put", "delete"}
    assert set(openapi["paths"]["/catalogs/{catalog_id}/catalogs"].keys()) == {
        "get",
        "post",
    }
    assert set(
        openapi["paths"]["/catalogs/{catalog_id}/catalogs/{sub_catalog_id}"].keys()
    ) == {"delete"}
    assert openapi["paths"]["/catalogs"]["post"]["security"] == [
        {"HTTPBasic": []}
    ]
    assert "security" not in openapi["paths"]["/catalogs"]["get"]
    assert any(
        "transaction" in item for item in app.state.catalogs_conformance_classes
    )


def test_catalog_conformance_is_read_only_without_catalog_transactions() -> None:
    """Catalog transaction conformance should be absent in read-only mode."""
    app = create_app(
        enabled_extensions={CATALOGS_EXTENSION},
        connect_to_database=False,
    )

    conformance_classes = app.state.catalogs_conformance_classes
    assert conformance_classes
    assert all("transaction" not in item for item in conformance_classes)


def test_collection_transaction_app_registers_collection_only_routes(
    collection_transaction_app: TestClient,
) -> None:
    """Enabling collection transactions should expose only collection write routes."""
    openapi = collection_transaction_app.app.openapi()

    assert set(openapi["paths"]["/collections"].keys()) == {"get", "post"}
    assert set(openapi["paths"]["/collections/{collection_id}"].keys()) == {
        "get",
        "put",
        "patch",
        "delete",
    }
    assert set(openapi["paths"]["/collections/{collection_id}/items"].keys()) == {"get"}
    assert set(
        openapi["paths"]["/collections/{collection_id}/items/{item_id}"].keys()
    ) == {"get"}
    assert {
        parameter["name"]
        for parameter in openapi["paths"]["/collections"]["get"]["parameters"]
    } >= {
        "query",
        "sortby",
    }


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/collections/test/items"),
        ("put", "/collections/test/items/item-1"),
        ("patch", "/collections/test/items/item-1"),
        ("delete", "/collections/test/items/item-1"),
    ],
)
def test_item_transaction_write_methods_are_not_registered(
    collection_transaction_app: TestClient,
    method: str,
    path: str,
) -> None:
    """Item transaction write methods should stay unregistered."""
    request_kwargs = {"json": {}} if method != "delete" else {}
    response = getattr(collection_transaction_app, method)(path, **request_kwargs)

    assert response.status_code == 405


def test_openapi_and_conformance_advertise_collection_transactions_only(
    collection_transaction_app: TestClient,
) -> None:
    """OpenAPI and conformance output should match the collection-only contract."""
    openapi = collection_transaction_app.app.openapi()

    assert "/collections/test/items" not in openapi["paths"]
    assert "/collections/{collection_id}/items/{item_id}" in openapi["paths"]
    assert "put" not in openapi["paths"]["/collections/{collection_id}/items/{item_id}"]
    assert (
        "patch" not in openapi["paths"]["/collections/{collection_id}/items/{item_id}"]
    )
    assert (
        "delete" not in openapi["paths"]["/collections/{collection_id}/items/{item_id}"]
    )
    assert openapi["components"]["securitySchemes"]["HTTPBasic"] == {
        "type": "http",
        "scheme": "basic",
        "description": "HTTP Basic authentication for collection transaction routes.",
    }
    assert openapi["paths"]["/collections"]["post"]["security"] == [{"HTTPBasic": []}]
    assert openapi["paths"]["/collections/{collection_id}"]["put"]["security"] == [
        {"HTTPBasic": []}
    ]
    assert openapi["paths"]["/collections/{collection_id}"]["patch"]["security"] == [
        {"HTTPBasic": []}
    ]
    assert openapi["paths"]["/collections/{collection_id}"]["delete"]["security"] == [
        {"HTTPBasic": []}
    ]
    assert "security" not in openapi["paths"]["/collections"]["get"]

    response = collection_transaction_app.get("/conformance")

    assert response.status_code == 200
    conformance_classes = response.json()["conformsTo"]
    assert (
        "https://api.stacspec.org/v1.0.0/collections/extensions/transaction"
        in conformance_classes
    )
    assert (
        "https://api.stacspec.org/v1.0.0/ogcapi-features/extensions/transaction"
        not in conformance_classes
    )


def test_parse_enabled_extensions_rejects_malformed_values() -> None:
    """Malformed extension configuration should fail clearly."""
    with pytest.raises(ValueError, match="Invalid ENABLED_EXTENSIONS"):
        parse_enabled_extensions("query,,collection_transaction")
