"""Settings for the MAAP STAC runtime."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class TransactionAuthSettings(BaseSettings):
    """Configuration for collection transaction authentication."""

    mode: Literal["basic"] | None = None
    secret_arn: str | None = None
    username: str | None = None
    password: str | None = None

    model_config = SettingsConfigDict(
        env_prefix="MAAP_TRANSACTION_AUTH_",
        extra="ignore",
    )


class OidcAuthSettings(BaseSettings):
    """Configuration for protecting the API with stac-auth-proxy (Keycloak/OIDC)
    Auth is enabled when MAAP_AUTH_OIDC_DISCOVERY_URL is set.
    """

    oidc_discovery_url: str | None = None
    upstream_url: str | None = None
    allowed_jwt_audiences: str | None = None
    swagger_client_id: str | None = None

    model_config = SettingsConfigDict(
        env_prefix="MAAP_AUTH_",
        extra="ignore",
    )
