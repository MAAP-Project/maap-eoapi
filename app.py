#!/usr/bin/env python3
import aws_cdk as cdk

from cdk.config import Config
from cdk.maap_eoapi_common import MaapEoapiCommon
from cdk.patch_manager import PatchManagerStack
from cdk.pgstac_infra import (
    DpsStacItemGenConfig,
    IngestorConfig,
    PgStacDbConfig,
    PgStacInfra,
    StacApiConfig,
    StacBrowserConfig,
    TitilerPgstacConfig,
)
from cdk.vpc import VpcStack

config = Config()

app = cdk.App()

vpc_stack = VpcStack(
    app,
    config.build_stack_name("vpc"),
    termination_protection=False,
    tags=config.tags,
    nat_gateway_count=None if config.stage == "prod" else 1,
)

# Create common resources to be shared by pgSTAC and userSTAC stacks
common = MaapEoapiCommon(
    app,
    config.build_stack_name("common"),
    tags=config.tags,
    stage=config.stage,
    termination_protection=False,
)

core_infrastructure = PgStacInfra(
    app,
    config.build_stack_name("pgSTAC"),
    vpc=vpc_stack.vpc,
    tags=config.tags,
    stage=config.stage,
    type="public",
    version=config.version,
    certificate_arn=config.certificate_arn,
    web_acl_arn=config.web_acl_arn,
    logging_bucket_arn=common.logging_bucket.bucket_arn,
    pgstac_db_config=PgStacDbConfig(
        instance_type=config.db_instance_type,
        pgstac_version=config.pgstac_version,
        allocated_storage=config.db_allocated_storage,
        subnet_public=False,
    ),
    stac_api_config=StacApiConfig(
        custom_domain_name=config.stac_api_custom_domain_name,
        integration_api_arn=config.stac_api_integration_api_arn,
    ),
    titiler_pgstac_config=TitilerPgstacConfig(
        mosaic_host=config.mosaic_host,
        buckets_path="./titiler_buckets.yaml",
        custom_domain_name=config.titiler_pg_stac_api_custom_domain_name,
        data_access_role_arn=config.titiler_data_access_role_arn,
    ),
    stac_browser_config=StacBrowserConfig(
        repo_tag=config.stac_browser_repo_tag,
        custom_domain_name=config.stac_browser_custom_domain_name,
        certificate_arn=config.stac_browser_certificate_arn,
    ),
    ingestor_config=IngestorConfig(
        jwks_url=config.jwks_url,
        data_access_role_arn=config.ingestor_data_access_role_arn,
        domain_name=config.ingestor_domain_name,
        user_data_path="./userdata.yaml",
    ),
    add_stactools_item_generator=True,
    termination_protection=False,
)

user_infrastructure = PgStacInfra(
    app,
    config.build_stack_name("userSTAC"),
    vpc=vpc_stack.vpc,
    tags=config.tags,
    stage=config.stage,
    type="internal",
    version=config.version,
    certificate_arn=config.certificate_arn,
    web_acl_arn=config.web_acl_arn,
    logging_bucket_arn=common.logging_bucket.bucket_arn,
    pgstac_db_config=PgStacDbConfig(
        instance_type=config.db_instance_type,
        pgstac_version=config.pgstac_version,
        allocated_storage=config.db_allocated_storage,
        subnet_public=False,
    ),
    stac_api_config=StacApiConfig(
        custom_domain_name=config.user_stac_stac_api_custom_domain_name,
        transactions=(
            config.user_stac_collection_transactions  # type: ignore[arg-type]
        ),
    ),
    titiler_pgstac_config=TitilerPgstacConfig(
        mosaic_host=config.mosaic_host,
        buckets_path="./titiler_buckets.yaml",
        custom_domain_name=config.user_stac_titiler_pgstac_api_custom_domain_name,
        data_access_role_arn=config.titiler_data_access_role_arn,
    ),
    add_stactools_item_generator=False,
    **(
        {
            "dps_stac_item_gen_config": DpsStacItemGenConfig(
                item_gen_role_arn=config.user_stac_item_gen_role_arn,
                inbound_topic_arns=config.user_stac_inbound_topic_arns,
                user_stac_collection_id_registry=config.user_stac_collection_id_registry,
            )
        }
        if config.user_stac_item_gen_role_arn
        else {}
    ),
    termination_protection=False,
)

patch_manager = PatchManagerStack(
    app,
    config.build_stack_name("patch-manager"),
    pgbouncer_param_names=[
        f"/maap-eoapi/{config.stage}/public/pgbouncer-instance-id",
        f"/maap-eoapi/{config.stage}/internal/pgbouncer-instance-id",
    ],
    termination_protection=False,
)
patch_manager.add_dependency(core_infrastructure)
patch_manager.add_dependency(user_infrastructure)

app.synth()
