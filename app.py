#!/usr/bin/env python3
import aws_cdk as cdk

from cdk.config import Config
from cdk.maap_eoapi_common import MaapEoapiCommon
from cdk.patch_manager import PatchManagerStack
from cdk.pgstac_infra import PgStacInfra
from cdk.vpc import VpcStack

config = Config()

app = cdk.App()
dps_stac_item_gen_config = config.dps_stac_item_gen()

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
    pgstac_db_config=config.pgstac_db(),
    stac_api_config=config.stac_api(),
    titiler_pgstac_config=config.titiler_pgstac(),
    stac_browser_config=config.stac_browser(),
    ingestor_config=config.ingestor(),
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
    pgstac_db_config=config.pgstac_db(),
    stac_api_config=config.stac_api(user_stac=True),
    titiler_pgstac_config=config.titiler_pgstac(user_stac=True),
    add_stactools_item_generator=False,
    **({"dps_stac_item_gen_config": dps_stac_item_gen_config} if dps_stac_item_gen_config else {}),
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
