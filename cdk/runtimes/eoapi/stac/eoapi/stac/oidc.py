"""Use stac-auth-proxy (OIDC / Keycloak) on the STAC API"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from stac_auth_proxy import configure_app
from starlette.middleware.cors import CORSMiddleware

from eoapi.stac.settings import OidcAuthSettings

if TYPE_CHECKING:
    from fastapi import FastAPI
    from stac_fastapi.pgstac.config import Settings

HEALTH_CHECK_PATH = r"^/_mgmt/ping$"

WRITE_ENDPOINTS: dict[str, list[tuple[str, str]]] = {
    r"^/collections$": [("POST", "stac:collection:create")],
    r"^/collections/([^/]+)$": [
        ("PUT", "stac:collection:update"),
        ("PATCH", "stac:collection:update"),
        ("DELETE", "stac:collection:delete"),
    ],
    r"^/catalogs$": [("POST", "stac:catalog:create")],
    r"^/catalogs/([^/]+)$": [
        ("PUT", "stac:catalog:update"),
        ("DELETE", "stac:catalog:delete"),
    ],
    r"^/catalogs/([^/]+)/catalogs$": [("POST", "stac:catalog:create")],
    # DELETE here only unlinks the child from its parent (it rewrites the child's
    # parent_ids), so it needs the update scope, not the delete scope
    r"^/catalogs/([^/]+)/catalogs/([^/]+)$": [("DELETE", "stac:catalog:update")],
    r"^/catalogs/([^/]+)/collections$": [("POST", "stac:collection:create")],
    r"^/catalogs/([^/]+)/collections/([^/]+)$": [
        ("PUT", "stac:collection:update"),
        # Unlinks the collection from the catalog so the collection is not deleted.
        ("DELETE", "stac:catalog:update"),
    ],
}


def oidc_auth_enabled() -> bool:
    """Checks whether OIDC auth (stac-auth-proxy) is configured"""
    return bool(OidcAuthSettings().oidc_discovery_url)


def configure_oidc_auth(app: FastAPI, api_settings: Settings) -> FastAPI:
    """Requires a valid OIDC bearer token on every route when auth is enabled.

    It uses ``default_public=False`` so reads are protected as well: the landing
    page and conformance need a token. Only the ``/_mgmt/ping`` health check and
    the OpenAPI spec and docs pages (``/api``, ``/api.html``) are public. Write
    routes additionally require the scopes in ``WRITE_ENDPOINTS``.
    """
    settings = OidcAuthSettings()
    if not settings.oidc_discovery_url:
        return app

    public_endpoints = {
        HEALTH_CHECK_PATH: ["GET"],
        rf"^{re.escape(api_settings.openapi_url)}$": ["GET"],
        rf"^{re.escape(api_settings.docs_url)}$": ["GET"],
    }

    configure_app(
        app,
        upstream_url=settings.upstream_url or "",
        oidc_discovery_url=settings.oidc_discovery_url,
        allowed_jwt_audiences=settings.allowed_jwt_audiences,
        default_public=False,
        public_endpoints=public_endpoints,
        private_endpoints=WRITE_ENDPOINTS,
        openapi_spec_endpoint=api_settings.openapi_url,
        swagger_ui_endpoint=api_settings.docs_url,
        enable_compression=False,
    )

    if settings.swagger_client_id:
        app.swagger_ui_init_oauth = {
            "clientId": settings.swagger_client_id,
            "usePkceWithAuthorizationCodeGrant": True,
        }

    allow_headers = list(dict.fromkeys([*api_settings.cors_headers, "Authorization"]))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=api_settings.cors_origins,
        allow_origin_regex=api_settings.cors_origin_regex,
        allow_methods=api_settings.cors_methods,
        allow_credentials=api_settings.cors_credentials,
        allow_headers=allow_headers,
    )
    return app
