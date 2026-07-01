from __future__ import annotations

from typing import Literal, Optional
from dataclasses import dataclass
from aws_cdk import aws_ec2 as ec2
from pydantic import (
    AliasChoices,
    Field,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass
class PgStacDbConfig:
    instance_type: ec2.InstanceType
    pgstac_version: str
    allocated_storage: int
    subnet_public: bool


@dataclass
class TitilerPgstacConfig:
    buckets_path: str
    data_access_role_arn: str
    mosaic_host: Optional[str] = None
    custom_domain_name: Optional[str] = None


@dataclass
class CollectionTransactionsConfig:
    auth_mode: Literal["basic", "jwt"]
    auth_secret_arn: Optional[str] = None


@dataclass
class StacApiConfig:
    custom_domain_name: Optional[str] = None
    integration_api_arn: Optional[str] = None
    transactions: Optional[CollectionTransactionsConfig] = None
    catalogs: Optional["StacCatalogsConfig"] = None


@dataclass
class StacBrowserConfig:
    repo_tag: str
    custom_domain_name: str
    certificate_arn: str


@dataclass
class IngestorConfig:
    jwks_url: str
    data_access_role_arn: str
    user_data_path: str
    domain_name: Optional[str] = None


@dataclass
class DpsStacItemGenConfig:
    item_gen_role_arn: str
    inbound_topic_arns: Optional[list[str]] = None
    user_stac_collection_id_registry: Optional[dict[str, list[str]]] = None


@dataclass
class StacCatalogsConfig:
    enabled: bool
    hide_alternate_parents: Optional[bool] = None
    transactions: Optional[CollectionTransactionsConfig] = None


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_ignore_empty=True,
        arbitrary_types_allowed=True,
        populate_by_name=True,
    )

    # --- Required ---
    stage: str
    db_instance_type: ec2.InstanceType
    jwks_url: str
    titiler_data_access_role_arn: str
    ingestor_data_access_role_arn: str
    stac_api_integration_api_arn: str
    db_allocated_storage: int
    mosaic_host: str
    stac_browser_repo_tag: str
    stac_browser_custom_domain_name: str
    stac_browser_certificate_arn: str
    stac_api_custom_domain_name: str
    pgstac_version: str
    web_acl_arn: str

    # --- Optional ---
    version: str = "0.1.1"
    certificate_arn: Optional[str] = None
    ingestor_domain_name: Optional[str] = None
    # env var is TITILER_PGSTAC_API_CUSTOM_DOMAIN_NAME (no underscore between pg/stac)
    titiler_pg_stac_api_custom_domain_name: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "titiler_pgstac_api_custom_domain_name",
            "titiler_pg_stac_api_custom_domain_name",
        ),
    )
    user_stac_item_gen_role_arn: Optional[str] = None
    user_stac_stac_api_custom_domain_name: Optional[str] = None
    user_stac_titiler_pgstac_api_custom_domain_name: Optional[str] = None
    user_stac_inbound_topic_arns: Optional[list[str]] = None
    user_stac_collection_id_registry: Optional[dict[str, list[str]]] = None

    # --- Collection transactions sub-fields (assembled by model_validator below) ---
    user_stac_collection_transactions_enabled: Optional[bool] = None
    user_stac_collection_transactions_auth_mode: Optional[str] = None
    user_stac_collection_transactions_auth_secret_arn: Optional[str] = None
    user_stac_collection_transactions: Optional[CollectionTransactionsConfig] = None
    user_stac_catalogs_enabled: Optional[bool] = None
    user_stac_catalogs_hide_alternate_parents: Optional[bool] = None
    user_stac_catalog_transactions_enabled: Optional[bool] = None
    user_stac_catalog_transactions_auth_mode: Optional[str] = None
    user_stac_catalog_transactions_auth_secret_arn: Optional[str] = None
    user_stac_catalogs: Optional[StacCatalogsConfig] = None

    @field_validator("db_instance_type", mode="before")
    @classmethod
    def parse_instance_type(cls, v: object) -> ec2.InstanceType:
        if isinstance(v, ec2.InstanceType):
            return v
        try:
            return ec2.InstanceType(str(v))
        except Exception as e:
            raise ValueError(f"Invalid DB_INSTANCE_TYPE: {v!r}") from e

    @field_validator(
        "user_stac_collection_transactions_enabled",
        "user_stac_catalogs_enabled",
        "user_stac_catalogs_hide_alternate_parents",
        "user_stac_catalog_transactions_enabled",
        mode="before",
    )
    @classmethod
    def parse_optional_bool_env(cls, v: object) -> Optional[bool]:
        if v is None:
            return None
        if isinstance(v, bool):
            return v

        normalized = str(v).strip().lower()
        if not normalized:
            return None
        if normalized == "true":
            return True
        if normalized == "false":
            return False

        raise ValueError(
            f"Invalid boolean value: {v!r}. Expected 'true' or 'false'."
        )

    @model_validator(mode="after")
    def assemble_collection_transactions(self) -> Config:
        if self.user_stac_collection_transactions_enabled is not True:
            self.user_stac_collection_transactions = None
        else:
            if not self.user_stac_collection_transactions_auth_mode:
                raise ValueError(
                    "Must provide USER_STAC_COLLECTION_TRANSACTIONS_AUTH_MODE "
                    "when USER_STAC_COLLECTION_TRANSACTIONS_ENABLED=true"
                )
            if self.user_stac_collection_transactions_auth_mode != "basic":
                raise ValueError(
                    "Unsupported USER_STAC_COLLECTION_TRANSACTIONS_AUTH_MODE: "
                    f"{self.user_stac_collection_transactions_auth_mode}. "
                    'Expected "basic".'
                )
            self.user_stac_collection_transactions = CollectionTransactionsConfig(
                auth_mode=self.user_stac_collection_transactions_auth_mode,
                auth_secret_arn=self.user_stac_collection_transactions_auth_secret_arn,
            )

        catalogs_enabled = self.user_stac_catalogs_enabled
        if catalogs_enabled is None:
            catalogs_enabled = True

        catalog_transactions_enabled = self.user_stac_catalog_transactions_enabled is True
        if catalog_transactions_enabled and not catalogs_enabled:
            raise ValueError(
                "USER_STAC_CATALOG_TRANSACTIONS_ENABLED=true requires "
                "USER_STAC_CATALOGS_ENABLED=true"
            )

        catalogs_transactions: Optional[CollectionTransactionsConfig] = None
        if catalog_transactions_enabled:
            if not self.user_stac_catalog_transactions_auth_mode:
                raise ValueError(
                    "Must provide USER_STAC_CATALOG_TRANSACTIONS_AUTH_MODE when "
                    "USER_STAC_CATALOG_TRANSACTIONS_ENABLED=true"
                )
            if self.user_stac_catalog_transactions_auth_mode != "basic":
                raise ValueError(
                    "Unsupported USER_STAC_CATALOG_TRANSACTIONS_AUTH_MODE: "
                    f"{self.user_stac_catalog_transactions_auth_mode}. "
                    'Expected "basic".'
                )
            catalogs_transactions = CollectionTransactionsConfig(
                auth_mode=self.user_stac_catalog_transactions_auth_mode,
                auth_secret_arn=self.user_stac_catalog_transactions_auth_secret_arn,
            )

        self.user_stac_catalogs = StacCatalogsConfig(
            enabled=catalogs_enabled,
            hide_alternate_parents=self.user_stac_catalogs_hide_alternate_parents,
            transactions=catalogs_transactions,
        )

        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tags(self) -> dict[str, str]:
        return {"project": "MAAP", "version": self.version, "stage": self.stage}

    def build_stack_name(self, service_id: str) -> str:
        return f"MAAP-STAC-{self.stage}-{service_id}"

    def pgstac_db(self) -> PgStacDbConfig:
        return PgStacDbConfig(
            instance_type=self.db_instance_type,
            pgstac_version=self.pgstac_version,
            allocated_storage=self.db_allocated_storage,
            subnet_public=False,
        )

    def stac_api(self, *, user_stac: bool = False) -> StacApiConfig:
        if user_stac:
            return StacApiConfig(
                custom_domain_name=self.user_stac_stac_api_custom_domain_name,
                transactions=self.user_stac_collection_transactions,
                catalogs=self.user_stac_catalogs,
            )

        return StacApiConfig(
            custom_domain_name=self.stac_api_custom_domain_name,
            integration_api_arn=self.stac_api_integration_api_arn,
            catalogs=StacCatalogsConfig(enabled=True),
        )

    def titiler_pgstac(self, *, user_stac: bool = False) -> TitilerPgstacConfig:
        return TitilerPgstacConfig(
            mosaic_host=self.mosaic_host,
            buckets_path="./titiler_buckets.yaml",
            custom_domain_name=(
                self.user_stac_titiler_pgstac_api_custom_domain_name
                if user_stac
                else self.titiler_pg_stac_api_custom_domain_name
            ),
            data_access_role_arn=self.titiler_data_access_role_arn,
        )

    def stac_browser(self) -> StacBrowserConfig:
        return StacBrowserConfig(
            repo_tag=self.stac_browser_repo_tag,
            custom_domain_name=self.stac_browser_custom_domain_name,
            certificate_arn=self.stac_browser_certificate_arn,
        )

    def ingestor(self) -> IngestorConfig:
        return IngestorConfig(
            jwks_url=self.jwks_url,
            data_access_role_arn=self.ingestor_data_access_role_arn,
            domain_name=self.ingestor_domain_name,
            user_data_path="./userdata.yaml",
        )

    def dps_stac_item_gen(self) -> Optional[DpsStacItemGenConfig]:
        if not self.user_stac_item_gen_role_arn:
            return None
        return DpsStacItemGenConfig(
            item_gen_role_arn=self.user_stac_item_gen_role_arn,
            inbound_topic_arns=self.user_stac_inbound_topic_arns,
            user_stac_collection_id_registry=self.user_stac_collection_id_registry,
        )
