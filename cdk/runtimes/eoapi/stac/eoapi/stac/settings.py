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
