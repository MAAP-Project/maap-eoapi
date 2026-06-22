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

    @field_validator("db_instance_type", mode="before")
    @classmethod
    def parse_instance_type(cls, v: object) -> ec2.InstanceType:
        if isinstance(v, ec2.InstanceType):
            return v
        try:
            return ec2.InstanceType(str(v))
        except Exception as e:
            raise ValueError(f"Invalid DB_INSTANCE_TYPE: {v!r}") from e

    @model_validator(mode="after")
    def assemble_collection_transactions(self) -> Config:
        if self.user_stac_collection_transactions_enabled is not True:
            return self
        if not self.user_stac_collection_transactions_auth_mode:
            raise ValueError(
                "Must provide USER_STAC_COLLECTION_TRANSACTIONS_AUTH_MODE "
                "when USER_STAC_COLLECTION_TRANSACTIONS_ENABLED=true"
            )
        self.user_stac_collection_transactions = CollectionTransactionsConfig(
            auth_mode=self.user_stac_collection_transactions_auth_mode,
            auth_secret_arn=self.user_stac_collection_transactions_auth_secret_arn,
        )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tags(self) -> dict[str, str]:
        return {"project": "MAAP", "version": self.version, "stage": self.stage}

    def build_stack_name(self, service_id: str) -> str:
        return f"MAAP-STAC-{self.stage}-{service_id}"
