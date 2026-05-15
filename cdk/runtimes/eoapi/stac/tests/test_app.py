"""Application tests for the MAAP STAC runtime."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from eoapi.stac.main import COLLECTION_TRANSACTION_EXTENSION, create_app, parse_enabled_extensions


@pytest.fixture
def collection_transaction_app() -> Iterator[TestClient]:
    """Build a test client with collection transactions enabled."""
    app = create_app(
        enabled_extensions={"query", "sort", "collection_search", COLLECTION_TRANSACTION_EXTENSION},
        connect_to_database=False,
    )
    with TestClient(app) as client:
        yield client


def test_read_only_app_omits_collection_transaction_routes() -> None:
    """The default app should stay read-only when collection transactions are disabled."""
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
    assert set(openapi["paths"]["/collections/{collection_id}/items/{item_id}"].keys()) == {"get"}


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
    assert set(openapi["paths"]["/collections/{collection_id}/items/{item_id}"].keys()) == {"get"}
    assert {parameter["name"] for parameter in openapi["paths"]["/collections"]["get"]["parameters"]} >= {
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
    assert "patch" not in openapi["paths"]["/collections/{collection_id}/items/{item_id}"]
    assert "delete" not in openapi["paths"]["/collections/{collection_id}/items/{item_id}"]

    response = collection_transaction_app.get("/conformance")

    assert response.status_code == 200
    conformance_classes = response.json()["conformsTo"]
    assert "https://api.stacspec.org/v1.0.0/collections/extensions/transaction" in conformance_classes
    assert "https://api.stacspec.org/v1.0.0/ogcapi-features/extensions/transaction" not in conformance_classes


def test_parse_enabled_extensions_rejects_malformed_values() -> None:
    """Malformed extension configuration should fail clearly."""
    with pytest.raises(ValueError, match="Invalid ENABLED_EXTENSIONS"):
        parse_enabled_extensions("query,,collection_transaction")
