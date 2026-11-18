"""Authentication tests / stac-auth-proxy (Keycloak/OIDC) tests."""

from __future__ import annotations

import sys
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from eoapi.stac.main import (
    create_app,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI

ISSUER = "https://keycloak.example/realms/maap"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
ORIGIN = "https://browser.example"


@pytest.fixture(scope="module")
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def oidc_env(monkeypatch: pytest.MonkeyPatch, private_key: rsa.RSAPrivateKey) -> None:
    """Enable auth and mock OIDC discovery + JWKS"""
    monkeypatch.setenv("MAAP_AUTH_OIDC_DISCOVERY_URL", DISCOVERY_URL)
    monkeypatch.setenv("MAAP_AUTH_UPSTREAM_URL", "https://stac.example")
    monkeypatch.setenv("MAAP_AUTH_ALLOWED_JWT_AUDIENCES", "account")

    enforce = sys.modules["stac_auth_proxy.middleware.EnforceAuthMiddleware"]
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"jwks_uri": f"{ISSUER}/protocol/openid-connect/certs"},
    )
    monkeypatch.setattr(enforce.httpx, "get", lambda _url: response)
    fake_jwks = SimpleNamespace(
        get_signing_key_from_jwt=lambda _t: SimpleNamespace(
            key=private_key.public_key()
        )
    )
    monkeypatch.setattr(enforce.jwt, "PyJWKClient", lambda _url: fake_jwks)


@pytest.fixture
def app(oidc_env: None) -> FastAPI:
    return create_app(enabled_extensions=set(), connect_to_database=False)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    yield TestClient(app, raise_server_exceptions=False)


def _token(key: rsa.RSAPrivateKey, **overrides: object) -> dict[str, str]:
    claims = {
        "iss": ISSUER,
        "aud": "account",
        "exp": int(time.time()) + 300,
        **overrides,
    }
    token = jwt.encode(claims, key, algorithm="RS256")
    return {"Authorization": f"Bearer {token}"}


def test_reads_require_a_token(client: TestClient) -> None:
    response = client.get("/collections")
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


def test_valid_token_is_accepted(client: TestClient, private_key) -> None:
    response = client.get("/collections", headers=_token(private_key))
    assert response.status_code not in (401, 403)


def test_expired_token_is_rejected(client: TestClient, private_key) -> None:
    headers = _token(private_key, exp=int(time.time()) - 10)
    assert client.get("/collections", headers=headers).status_code == 401


def test_default_has_reads_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAAP_AUTH_OIDC_DISCOVERY_URL", raising=False)
    app = create_app(enabled_extensions=set(), connect_to_database=False)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/collections").status_code not in (401, 403)
