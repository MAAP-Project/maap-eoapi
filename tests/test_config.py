from __future__ import annotations

import pytest

from cdk.config import Config


@pytest.fixture
def required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAGE", "test")
    monkeypatch.setenv("DB_INSTANCE_TYPE", "t3.micro")
    monkeypatch.setenv("JWKS_URL", "https://example.com/jwks")
    monkeypatch.setenv(
        "TITILER_DATA_ACCESS_ROLE_ARN", "arn:aws:iam::123456789012:role/test-role"
    )
    monkeypatch.setenv(
        "INGESTOR_DATA_ACCESS_ROLE_ARN", "arn:aws:iam::123456789012:role/test-role"
    )
    monkeypatch.setenv(
        "STAC_API_INTEGRATION_API_ARN", "arn:aws:iam::123456789012:role/test-role"
    )
    monkeypatch.setenv("DB_ALLOCATED_STORAGE", "20")
    monkeypatch.setenv("MOSAIC_HOST", "example.com")
    monkeypatch.setenv("STAC_BROWSER_REPO_TAG", "latest")
    monkeypatch.setenv("STAC_BROWSER_CUSTOM_DOMAIN_NAME", "stac-browser.example.com")
    monkeypatch.setenv(
        "STAC_BROWSER_CERTIFICATE_ARN",
        "arn:aws:acm:us-east-1:123456789012:certificate/test-cert",
    )
    monkeypatch.setenv("STAC_API_CUSTOM_DOMAIN_NAME", "stac-api.example.com")
    monkeypatch.setenv("PGSTAC_VERSION", "0.9.5")
    monkeypatch.setenv(
        "WEB_ACL_ARN", "arn:aws:wafv2:us-east-1:123456789012:global/webacl/test-acl"
    )
    monkeypatch.setenv("VERSION", "1.0.0")


def test_creates_valid_config_with_required_env(required_env: None) -> None:
    config = Config()

    assert config.stage == "test"
    assert config.jwks_url == "https://example.com/jwks"
    assert config.titiler_data_access_role_arn == "arn:aws:iam::123456789012:role/test-role"
    assert config.ingestor_data_access_role_arn == "arn:aws:iam::123456789012:role/test-role"
    assert config.stac_api_integration_api_arn == "arn:aws:iam::123456789012:role/test-role"
    assert config.mosaic_host == "example.com"
    assert config.stac_browser_repo_tag == "latest"
    assert config.stac_browser_custom_domain_name == "stac-browser.example.com"
    assert config.stac_browser_certificate_arn == (
        "arn:aws:acm:us-east-1:123456789012:certificate/test-cert"
    )
    assert config.stac_api_custom_domain_name == "stac-api.example.com"
    assert config.pgstac_version == "0.9.5"
    assert config.web_acl_arn == "arn:aws:wafv2:us-east-1:123456789012:global/webacl/test-acl"
    assert config.user_stac_collection_transactions is None
    assert config.user_stac_catalogs is not None
    assert config.user_stac_catalogs.enabled is True

    assert config.db_allocated_storage == 20
    assert config.db_instance_type.to_string() == "t3.micro"

    assert config.tags["project"] == "MAAP"
    assert config.tags["version"] == "1.0.0"
    assert config.tags["stage"] == "test"


def test_missing_required_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAGE", raising=False)
    with pytest.raises(Exception):
        Config()


def test_optional_env_vars(required_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "CERTIFICATE_ARN", "arn:aws:acm:us-east-1:123456789012:certificate/optional-cert"
    )
    monkeypatch.setenv("INGESTOR_DOMAIN_NAME", "ingestor.example.com")
    monkeypatch.setenv("TITILER_PGSTAC_API_CUSTOM_DOMAIN_NAME", "titiler.example.com")

    config = Config()

    assert config.certificate_arn == (
        "arn:aws:acm:us-east-1:123456789012:certificate/optional-cert"
    )
    assert config.ingestor_domain_name == "ingestor.example.com"
    assert config.titiler_pg_stac_api_custom_domain_name == "titiler.example.com"


def test_user_stac_collection_transactions_defaults(required_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USER_STAC_COLLECTION_TRANSACTIONS_ENABLED", "true")
    monkeypatch.setenv("USER_STAC_COLLECTION_TRANSACTIONS_AUTH_MODE", "basic")

    config = Config()
    assert config.user_stac_collection_transactions is not None
    assert config.user_stac_collection_transactions.auth_mode == "basic"
    assert config.user_stac_collection_transactions.auth_secret_arn is None


def test_user_stac_collection_transactions_accepts_secret(required_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USER_STAC_COLLECTION_TRANSACTIONS_ENABLED", "true")
    monkeypatch.setenv("USER_STAC_COLLECTION_TRANSACTIONS_AUTH_MODE", "basic")
    monkeypatch.setenv(
        "USER_STAC_COLLECTION_TRANSACTIONS_AUTH_SECRET_ARN",
        "arn:aws:secretsmanager:us-west-2:123456789012:secret:user-stac-auth",
    )

    config = Config()
    assert config.user_stac_collection_transactions is not None
    assert config.user_stac_collection_transactions.auth_mode == "basic"
    assert config.user_stac_collection_transactions.auth_secret_arn == (
        "arn:aws:secretsmanager:us-west-2:123456789012:secret:user-stac-auth"
    )


def test_user_stac_collection_transactions_rejects_unsupported_auth_mode(
    required_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USER_STAC_COLLECTION_TRANSACTIONS_ENABLED", "true")
    monkeypatch.setenv("USER_STAC_COLLECTION_TRANSACTIONS_AUTH_MODE", "jwt")

    with pytest.raises(ValueError, match='Expected "basic"'):
        Config()


def test_user_stac_collection_transactions_rejects_invalid_boolean(
    required_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USER_STAC_COLLECTION_TRANSACTIONS_ENABLED", "yes")

    with pytest.raises(ValueError, match="Expected 'true' or 'false'"):
        Config()


def test_user_stac_catalogs_and_catalog_transactions(required_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USER_STAC_CATALOGS_HIDE_ALTERNATE_PARENTS", "true")
    monkeypatch.setenv("USER_STAC_CATALOG_TRANSACTIONS_ENABLED", "true")
    monkeypatch.setenv("USER_STAC_CATALOG_TRANSACTIONS_AUTH_MODE", "basic")
    monkeypatch.setenv(
        "USER_STAC_CATALOG_TRANSACTIONS_AUTH_SECRET_ARN",
        "arn:aws:secretsmanager:us-west-2:123456789012:secret:user-stac-catalog-auth",
    )

    config = Config()
    assert config.user_stac_catalogs is not None
    assert config.user_stac_catalogs.enabled is True
    assert config.user_stac_catalogs.hide_alternate_parents is True
    assert config.user_stac_catalogs.transactions is not None
    assert config.user_stac_catalogs.transactions.auth_mode == "basic"
    assert config.user_stac_catalogs.transactions.auth_secret_arn == (
        "arn:aws:secretsmanager:us-west-2:123456789012:secret:user-stac-catalog-auth"
    )


def test_rejects_catalog_transactions_when_catalogs_disabled(
    required_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USER_STAC_CATALOGS_ENABLED", "false")
    monkeypatch.setenv("USER_STAC_CATALOG_TRANSACTIONS_ENABLED", "true")
    monkeypatch.setenv("USER_STAC_CATALOG_TRANSACTIONS_AUTH_MODE", "basic")

    with pytest.raises(ValueError, match="requires USER_STAC_CATALOGS_ENABLED"):
        Config()


def test_build_stack_name(required_env: None) -> None:
    config = Config()
    assert config.build_stack_name("TestService") == "MAAP-STAC-test-TestService"
