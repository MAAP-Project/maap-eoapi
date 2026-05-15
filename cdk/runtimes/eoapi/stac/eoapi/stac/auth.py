"""Authentication helpers for collection transaction routes."""

from __future__ import annotations

import json
from functools import lru_cache
from hmac import compare_digest
from typing import Annotated, Any

from fastapi import HTTPException, Security, status
from fastapi.params import Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from eoapi.stac.settings import (
    MAAP_TRANSACTION_AUTH_MODE_ENV,
    MAAP_TRANSACTION_AUTH_PASSWORD_ENV,
    MAAP_TRANSACTION_AUTH_SECRET_ARN_ENV,
    MAAP_TRANSACTION_AUTH_USERNAME_ENV,
    TransactionAuthSettings,
)

_BASIC_AUTH_CHALLENGE_HEADERS = {"WWW-Authenticate": "Basic"}
basic_auth_scheme = HTTPBasic(
    auto_error=False,
    description="HTTP Basic authentication for collection transaction routes.",
)
transaction_auth_settings = TransactionAuthSettings()


@lru_cache(maxsize=None)
def load_secret_dict(secret_arn: str) -> dict[str, Any]:
    """Load and parse a JSON secret from AWS Secrets Manager."""
    try:
        import boto3
    except ImportError as error:
        raise RuntimeError(
            "boto3 is required to load Secrets Manager credentials"
        ) from error

    response = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
    secret_string = response.get("SecretString")
    if not secret_string:
        raise RuntimeError(f"Secret {secret_arn} did not contain SecretString")

    try:
        secret_dict = json.loads(secret_string)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Secret {secret_arn} did not contain valid JSON") from error

    if not isinstance(secret_dict, dict):
        raise RuntimeError(f"Secret {secret_arn} must decode to a JSON object")

    return secret_dict


def _unauthorized_basic_auth() -> HTTPException:
    """Build the standard HTTP Basic auth challenge response."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing basic authentication credentials",
        headers=_BASIC_AUTH_CHALLENGE_HEADERS,
    )


def get_basic_auth_credentials() -> tuple[str, str]:
    """Load basic-auth credentials from Secrets Manager or local env vars."""
    if transaction_auth_settings.secret_arn:
        secret = load_secret_dict(transaction_auth_settings.secret_arn)
        if not isinstance(secret.get("username"), str) or not isinstance(
            secret.get("password"), str
        ):
            raise RuntimeError(
                "Transaction auth secret must contain string fields 'username' and "
                "'password'"
            )
        return secret["username"], secret["password"]

    if transaction_auth_settings.username and transaction_auth_settings.password:
        return transaction_auth_settings.username, transaction_auth_settings.password

    raise RuntimeError(
        "Collection transactions require either MAAP_TRANSACTION_AUTH_SECRET_ARN or "
        "both MAAP_TRANSACTION_AUTH_USERNAME and MAAP_TRANSACTION_AUTH_PASSWORD when "
        "MAAP_TRANSACTION_AUTH_MODE=basic"
    )


def validate_transaction_auth_config() -> str:
    """Validate transaction auth configuration and return the selected mode."""
    auth_mode = transaction_auth_settings.mode
    if auth_mode != "basic":
        raise RuntimeError(
            "Collection transactions require MAAP_TRANSACTION_AUTH_MODE=basic for "
            "this runtime version"
        )

    get_basic_auth_credentials()

    return auth_mode


async def require_transaction_auth(
    credentials: Annotated[
        HTTPBasicCredentials | None,
        Security(basic_auth_scheme),
    ],
) -> None:
    """Require valid transaction auth for collection write routes."""
    auth_mode = validate_transaction_auth_config()
    if auth_mode != "basic":
        raise RuntimeError(f"Unsupported transaction auth mode: {auth_mode}")

    if credentials is None:
        raise _unauthorized_basic_auth()

    expected_username, expected_password = get_basic_auth_credentials()
    if not compare_digest(credentials.username, expected_username) or not compare_digest(
        credentials.password,
        expected_password,
    ):
        raise _unauthorized_basic_auth()


def build_transaction_route_dependencies() -> list[Depends]:
    """Build validated route dependencies for transaction routes."""
    validate_transaction_auth_config()
    return [Depends(require_transaction_auth)]
