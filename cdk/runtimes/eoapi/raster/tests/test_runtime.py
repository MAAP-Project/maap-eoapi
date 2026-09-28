"""Focused behavior tests for the raster runtime."""

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_bounds

os.environ.setdefault("MOSAIC_BACKEND", "dynamodb://")
os.environ.setdefault("MOSAIC_HOST", "us-west-2/test-table")

from eoapi.raster.factory import mosaic_config
from eoapi.raster.main import app


@pytest.mark.parametrize(
    ("method", "path", "location"),
    [
        (
            "GET",
            "/mosaic/search-1/tiles/1/2/3.png?x=a%2Fb&x=two&collection_id=ignored",
            "/searches/search-1/tiles/WebMercatorQuad/1/2/3.png?x=a%2Fb&x=two&collection_id=ignored",
        ),
        (
            "GET",
            "/mosaic/search-1/tiles/1/2/3@2x.webp",
            "/searches/search-1/tiles/WebMercatorQuad/1/2/3@2x.webp",
        ),
        (
            "GET",
            "/mosaic/search-1/tiles/1/2/3/assets",
            "/searches/search-1/tiles/WebMercatorQuad/1/2/3/assets",
        ),
        (
            "GET",
            "/mosaic/search-1/tilejson.json",
            "/searches/search-1/WebMercatorQuad/tilejson.json",
        ),
        (
            "GET",
            "/mosaic/search-1/map.html",
            "/searches/search-1/WebMercatorQuad/map.html",
        ),
        (
            "GET",
            "/collections/c-1/tiles/1/2/3.png",
            "/collections/c-1/tiles/WebMercatorQuad/1/2/3.png",
        ),
        (
            "GET",
            "/collections/c-1/tiles/1/2/3@2x.webp",
            "/collections/c-1/tiles/WebMercatorQuad/1/2/3@2x.webp",
        ),
        (
            "GET",
            "/collections/c-1/tiles/1/2/3/assets",
            "/collections/c-1/tiles/WebMercatorQuad/1/2/3/assets",
        ),
        (
            "GET",
            "/collections/c-1/tilejson.json",
            "/collections/c-1/WebMercatorQuad/tilejson.json",
        ),
        (
            "GET",
            "/collections/c-1/map.html",
            "/collections/c-1/WebMercatorQuad/map.html",
        ),
        ("POST", "/mosaic/register?token=a%2Fb", "/searches/register?token=a%2Fb"),
        ("GET", "/mosaic/list", "/searches/"),
        ("GET", "/mosaic/search-1/info", "/searches/search-1/info"),
        (
            "GET",
            "/mosaic/search-1/WMTSCapabilities.xml",
            "/searches/search-1/WMTSCapabilities.xml",
        ),
        ("GET", "/mosaic/search-1/statistics", "/searches/search-1/statistics"),
    ],
)
def test_legacy_redirects_preserve_methods_and_queries(method, path, location):
    """Legacy no-TMS routes redirect to native paths without changing scope."""
    response = TestClient(app).request(method, path, follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == location


def test_canonical_collection_paths_are_not_compatibility_redirects():
    """Native paths and unrelated collections endpoints never get rewritten."""
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        "/collections/c-1/tiles/WebMercatorQuad/1/2/3.png",
        follow_redirects=False,
    )

    # No pgSTAC database is configured in this focused test; a native handler is
    # reached and fails there, rather than being redirected by compatibility code.
    assert response.status_code == 500
    assert "location" not in response.headers


def test_native_routes_are_exposed_without_custom_route_overrides():
    """The upgraded pgSTAC and Mosaic factories expose their native routes."""
    paths = app.openapi()["paths"]

    assert "/searches/{search_id}/tiles/{tileMatrixSetId}/{z}/{x}/{y}" in paths
    assert "/searches/{search_id}/{tileMatrixSetId}/tilejson.json" in paths
    assert "/searches/{search_id}/{tileMatrixSetId}/map.html" in paths
    assert "/searches/{search_id}/statistics" in paths
    assert "/searches/{search_id}/feature" in paths
    assert "/mosaics/{mosaic_id}/tiles/{tileMatrixSetId}/{z}/{x}/{y}" in paths
    assert "/mosaics/{mosaic_id}/WMTSCapabilities.xml" in paths
    assert "/mosaic/{search_id}/tiles/{z}/{x}/{y}" not in paths


def test_custom_mosaic_creation_storage_and_native_tile_endpoint(tmp_path, monkeypatch):
    """Custom URL creation stores a mosaic usable by the native tile route."""
    monkeypatch.setattr(mosaic_config, "backend", "")
    monkeypatch.setattr(mosaic_config, "host", f"{tmp_path}/")
    monkeypatch.setattr(mosaic_config, "format", ".json")

    cog_path = tmp_path / "tiny.tif"
    with rasterio.open(
        cog_path,
        "w",
        driver="GTiff",
        height=16,
        width=16,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_bounds(-180, -85, 180, 85, 16, 16),
    ) as dataset:
        dataset.write(np.full((1, 16, 16), 100, dtype="uint8"))

    client = TestClient(app)
    response = client.post(
        "/mosaics",
        headers={"Content-Type": "application/vnd.titiler.urls+json"},
        json={"urls": [str(cog_path)], "minzoom": 0, "maxzoom": 0},
    )

    assert response.status_code == 201
    mosaic_id = response.json()["id"]
    assert response.headers["location"].endswith(f"/mosaics/{mosaic_id}")
    assert client.get(f"/mosaics/{mosaic_id}").status_code == 200
    assert client.get(f"/mosaics/{mosaic_id}/mosaicjson").status_code == 200

    entity = client.get(f"/mosaics/{mosaic_id}").json()
    assert any(
        link["href"].endswith(
            f"/mosaics/{mosaic_id}/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}"
        )
        for link in entity["links"]
    )
    tile = client.get(f"/mosaics/{mosaic_id}/tiles/WebMercatorQuad/0/0/0.png")
    assert tile.status_code == 200
    assert tile.headers["content-type"] == "image/png"


def test_lambda_startup_uses_new_pg_settings_and_handles_optional_snapstart(
    monkeypatch,
):
    """The Lambda module imports and builds routes with TiTiler 3.2 settings."""

    class SecretsClient:
        def get_secret_value(self, **kwargs):
            return {
                "SecretString": json.dumps(
                    {
                        "host": "database",
                        "dbname": "postgis",
                        "username": "raster",
                        "password": "secret",
                        "port": 5432,
                    }
                )
            }

    fake_boto3 = ModuleType("boto3")
    fake_boto3.session = SimpleNamespace(
        Session=lambda: SimpleNamespace(client=lambda **kwargs: SecretsClient())
    )
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[5]))
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setattr(app, "middleware_stack", None)
    monkeypatch.setenv("PGSTAC_SECRET_ARN", "arn:local:test")
    monkeypatch.delenv("AWS_EXECUTION_ENV", raising=False)

    module = importlib.import_module("cdk.handlers.raster_handler")
    assert module.pg_settings.pghost == "database"
    assert module.pg_settings.pgdatabase == "postgis"
    assert module.pg_settings.pguser == "raster"
    assert module.pg_settings.pgpassword == "secret"
    assert module.pg_settings.pgport == 5432

    connections = []

    async def connect_to_db(app, settings):
        connections.append(settings)

    monkeypatch.setattr(module, "connect_to_db", connect_to_db)
    asyncio.run(module.startup_event())

    assert connections == [module.pg_settings]
    assert any(
        path == "/searches/{search_id}/tiles/{tileMatrixSetId}/{z}/{x}/{y}"
        for path in module.app.state.path_templates.values()
    )
