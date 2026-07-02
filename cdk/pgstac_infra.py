from __future__ import annotations

import json

from pathlib import Path
from typing import Optional

import yaml
from aws_cdk import (
    Aws,
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigateway as apigateway,
    aws_apigatewayv2 as apigatewayv2,
    aws_certificatemanager as acm,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_cloudwatch as cloudwatch,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_rds as rds,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
    aws_ssm as ssm,
)

from .config import (
    PgStacDbConfig,
    TitilerPgstacConfig,
    StacApiConfig,
    StacBrowserConfig,
    IngestorConfig,
    DpsStacItemGenConfig,
    StacCatalogsConfig,
)

from constructs import Construct

import eoapi_cdk

from .constructs.dps_stac_item_generator import (
    DpsStacItemGenerator,
    DpsStacItemGeneratorProps,
)

_CDK_DIR = Path(__file__).parent


class PgStacInfra(Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        vpc: ec2.Vpc,
        stage: str,  # Used for naming resources.
        type: str,  # Type of deployment, e.g. "public" or "internal".
        version: str,  # Used to correlate codebase versions to running services.
        web_acl_arn: str,  # ARN of WAF Web ACL to use for eoAPI custom domains.
        logging_bucket_arn: str,  # ARN of S3 bucket for logging.
        pgstac_db_config: PgStacDbConfig,
        titiler_pgstac_config: TitilerPgstacConfig,
        stac_api_config: StacApiConfig,
        certificate_arn: Optional[
            str
        ] = None,  # ARN of ACM certificate for eoAPI custom domains.
        stac_browser_config: Optional[
            StacBrowserConfig
        ] = None,  # Omit to skip STAC Browser.
        ingestor_config: Optional[IngestorConfig] = None,  # Omit to skip STAC Ingestor.
        dps_stac_item_gen_config: Optional[DpsStacItemGenConfig] = None,
        add_stactools_item_generator: Optional[bool] = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        stack = Stack.of(self)

        # ── pgSTAC Database ────────────────────────────────────────────────
        pgstac_db = eoapi_cdk.PgStacDatabase(
            self,
            "pgstac-db",
            vpc=vpc,
            allow_major_version_upgrade=True,
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_17
            ),
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=(
                    ec2.SubnetType.PUBLIC
                    if pgstac_db_config.subnet_public
                    else ec2.SubnetType.PRIVATE_ISOLATED
                )
            ),
            allocated_storage=pgstac_db_config.allocated_storage,
            instance_type=pgstac_db_config.instance_type,
            add_pgbouncer=True,
            add_patch_manager=False,
            pgstac_version=pgstac_db_config.pgstac_version,
            custom_resource_properties={
                "context": True,
                "update_collection_extent": True,
                "use_queue": True,
            },
            bootstrapper_lambda_function_options={"timeout": Duration.minutes(15)},
            parameters={"shared_preload_libraries": "pg_cron"},
        )

        if pgstac_db.pgbouncer_instance_id:
            ssm.StringParameter(
                self,
                "pgbouncer-instance-id-param",
                parameter_name=f"/maap-eoapi/{stage}/{type}/pgbouncer-instance-id",
                string_value=pgstac_db.pgbouncer_instance_id,
                description=f"PgBouncer EC2 instance ID for MAAP eoAPI {type} stack ({stage})",
            )

        api_subnet_selection = ec2.SubnetSelection(
            subnet_type=(
                ec2.SubnetType.PUBLIC
                if pgstac_db_config.subnet_public
                else ec2.SubnetType.PRIVATE_WITH_EGRESS
            )
        )

        # ── Collection transactions config ─────────────────────────────────
        transactions_config = stac_api_config.transactions
        catalogs_config = stac_api_config.catalogs or StacCatalogsConfig(enabled=True)
        catalogs_enabled = catalogs_config.enabled is not False
        catalog_transactions_config = catalogs_config.transactions

        if catalog_transactions_config and not catalogs_enabled:
            raise ValueError("STAC catalog transactions require catalogs to be enabled")

        if transactions_config and transactions_config.auth_mode != "basic":
            raise ValueError(
                f"Unsupported STAC collection transaction auth mode: "
                f"{transactions_config.auth_mode}"
            )
        if (
            catalog_transactions_config
            and catalog_transactions_config.auth_mode != "basic"
        ):
            raise ValueError(
                f"Unsupported STAC catalog transaction auth mode: "
                f"{catalog_transactions_config.auth_mode}"
            )
        if (
            transactions_config
            and catalog_transactions_config
            and transactions_config.auth_secret_arn
            != catalog_transactions_config.auth_secret_arn
        ):
            raise ValueError(
                "STAC collection and catalog transactions must use the same auth "
                "secret ARN"
            )

        write_transactions_config = transactions_config or catalog_transactions_config

        if write_transactions_config:
            if write_transactions_config.auth_secret_arn:
                transaction_auth_secret: Optional[secretsmanager.ISecret] = (
                    secretsmanager.Secret.from_secret_complete_arn(
                        self,
                        "stac-collection-transaction-auth-secret",
                        write_transactions_config.auth_secret_arn,
                    )
                )
            else:
                transaction_auth_secret = secretsmanager.Secret(
                    self,
                    "stac-collection-transaction-auth-secret",
                    description=(
                        f"Basic auth secret for MAAP {type} STAC collection "
                        f"transactions ({stage})"
                    ),
                    secret_name=(
                        f"/maap-eoapi/{stage}/{type}"
                        "/stac-collection-transaction-basic-auth"
                    ),
                    generate_secret_string=secretsmanager.SecretStringGenerator(
                        secret_string_template=json.dumps(
                            {"username": f"maap-{type}-stac-writer"}
                        ),
                        generate_string_key="password",
                        exclude_punctuation=True,
                    ),
                )
        else:
            transaction_auth_secret = None

        stac_enabled_extensions = [
            "query",
            "sort",
            "fields",
            "filter",
            "free_text",
            "pagination",
            "collection_search",
            *(["catalogs"] if catalogs_enabled else []),
            *(["collection_transaction"] if transactions_config else []),
            *(["catalog_transaction"] if catalog_transactions_config else []),
        ]

        stac_api_env: dict[str, str] = {
            "STAC_FASTAPI_TITLE": f"MAAP {type} STAC API ({stage})",
            "STAC_FASTAPI_LANDING_ID": f"maap-{type}-stac-api-{stage}",
            "STAC_FASTAPI_DESCRIPTION": (
                f"The {type} STAC API for the [MAAP project](https://maap-project.org)"
            ),
            "STAC_FASTAPI_VERSION": version,
            "ENABLED_EXTENSIONS": ",".join(stac_enabled_extensions),
            "ENABLE_CATALOGS_EXTENSION": "true" if catalogs_enabled else "false",
            "HIDE_ALTERNATE_PARENTS": (
                "true" if catalogs_config.hide_alternate_parents else "false"
            ),
            **(
                {
                    "MAAP_TRANSACTION_AUTH_MODE": write_transactions_config.auth_mode,
                    "MAAP_TRANSACTION_AUTH_SECRET_ARN": transaction_auth_secret.secret_arn,  # type: ignore[union-attr]
                }
                if write_transactions_config
                else {}
            ),
        }

        stac_api_lambda_options = {
            "code": lambda_.Code.from_docker_build(
                str(_CDK_DIR),
                file="dockerfiles/Dockerfile.stac",
                target_stage="lambda",
                build_args={"PYTHON_VERSION": "3.12"},
            ),
            "handler": "eoapi.stac.handler.handler",
        }

        # ── STAC API ───────────────────────────────────────────────────────
        stac_api_domain_name = (
            apigatewayv2.DomainName(
                self,
                "stac-api-domain-name",
                domain_name=stac_api_config.custom_domain_name,
                certificate=acm.Certificate.from_certificate_arn(
                    self,
                    "stacApiCustomDomainNameCertificate",
                    certificate_arn,
                ),
            )
            if stac_api_config.custom_domain_name and certificate_arn
            else None
        )

        stac_api_lambda = eoapi_cdk.PgStacApiLambda(
            self,
            "pgstac-api",
            api_env=stac_api_env,
            vpc=vpc,
            db=pgstac_db.connection_target,
            db_secret=pgstac_db.pgstac_secret,
            subnet_selection=api_subnet_selection,
            stac_api_domain_name=stac_api_domain_name,
            enable_snap_start=True,
            lambda_function_options=stac_api_lambda_options,
        )

        stac_api_lambda.lambda_function.connections.allow_to(
            pgstac_db.connection_target,
            ec2.Port.tcp(5432),
            "allow connections from stac-fastapi-pgstac",
        )

        if stac_api_config.integration_api_arn:
            stac_api_lambda.lambda_function.add_permission(
                "ApiGatewayInvoke",
                principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
                source_arn=stac_api_config.integration_api_arn,
            )

        if transaction_auth_secret:
            transaction_auth_secret.grant_read(stac_api_lambda.lambda_function)

            ssm.StringParameter(
                self,
                "stac-collection-transaction-auth-secret-param",
                parameter_name=(
                    f"/maap-eoapi/{stage}/{type}"
                    "/stac-collection-transaction-auth-secret-arn"
                ),
                string_value=transaction_auth_secret.secret_arn,
                description=(
                    f"Secrets Manager ARN for MAAP {type} STAC transaction auth "
                    f"({stage})"
                ),
            )

        # ── titiler-pgstac ─────────────────────────────────────────────────
        titiler_data_access_role = iam.Role.from_role_arn(
            self,
            "titiler-data-access-role",
            titiler_pgstac_config.data_access_role_arn,
        )

        with open(titiler_pgstac_config.buckets_path, "r") as f:
            buckets: list[str] = yaml.safe_load(f)

        titiler_pgstac_lambda_options = {
            "code": lambda_.Code.from_docker_build(
                str(_CDK_DIR),
                file="dockerfiles/Dockerfile.raster",
                target_stage="lambda",
                build_args={"PYTHON_VERSION": "3.12"},
            ),
            "handler": "handler.handler",
            "role": titiler_data_access_role,
        }

        titiler_pgstac_api_env: dict[str, str] = {
            "NAME": f"MAAP titiler pgstac API ({stage})",
            "VERSION": version,
            "DESCRIPTION": "titiler pgstac API for the MAAP STAC system.",
        }

        if titiler_pgstac_config.mosaic_host:
            titiler_pgstac_api_env["MOSAIC_BACKEND"] = "dynamodb://"
            titiler_pgstac_api_env["MOSAIC_HOST"] = titiler_pgstac_config.mosaic_host

        titiler_pgstac_domain_name = (
            apigatewayv2.DomainName(
                self,
                "titiler-pgstac-api-domain-name",
                domain_name=titiler_pgstac_config.custom_domain_name,
                certificate=acm.Certificate.from_certificate_arn(
                    self,
                    "titilerPgStacCustomDomainNameCertificate",
                    certificate_arn,
                ),
            )
            if titiler_pgstac_config.custom_domain_name and certificate_arn
            else None
        )

        titiler_pgstac_api = eoapi_cdk.TitilerPgstacApiLambda(
            self,
            "titiler-pgstac-api",
            api_env=titiler_pgstac_api_env,
            vpc=vpc,
            db=pgstac_db.connection_target,
            db_secret=pgstac_db.pgstac_secret,
            subnet_selection=api_subnet_selection,
            buckets=buckets,
            titiler_pgstac_api_domain_name=titiler_pgstac_domain_name,
            lambda_function_options=titiler_pgstac_lambda_options,
            enable_snap_start=True,
        )

        if titiler_pgstac_config.mosaic_host:
            table_name = titiler_pgstac_config.mosaic_host.split("/", 2)[1]

            mosaic_perms = [
                iam.PolicyStatement(
                    actions=[
                        "dynamodb:CreateTable",
                        "dynamodb:DescribeTable",
                    ],
                    resources=[
                        f"arn:aws:dynamodb:{stack.region}:{stack.account}:table/*"
                    ],
                ),
                iam.PolicyStatement(
                    actions=[
                        "dynamodb:Query",
                        "dynamodb:GetItem",
                        "dynamodb:Scan",
                        "dynamodb:PutItem",
                        "dynamodb:BatchWriteItem",
                    ],
                    resources=[
                        f"arn:aws:dynamodb:{stack.region}:{stack.account}:table/{table_name}"
                    ],
                ),
            ]

            for permission in mosaic_perms:
                titiler_pgstac_api.lambda_function.add_to_role_policy(permission)

        titiler_pgstac_api.lambda_function.connections.allow_to(
            pgstac_db.connection_target,
            ec2.Port.tcp(5432),
            "allow connections from titiler",
        )

        # ── CloudWatch dashboard ───────────────────────────────────────────
        eoapi_dashboard = cloudwatch.Dashboard(
            self,
            "eoAPIDashboard",
            dashboard_name=f"eoAPI-{stage}-{type}",
        )

        titiler_log_group = titiler_pgstac_api.lambda_function.log_group.log_group_name

        titiler_route_log_widget = cloudwatch.LogQueryWidget(
            log_group_names=[titiler_log_group],
            title="titiler requests by route",
            width=12,
            height=8,
            view=cloudwatch.LogQueryVisualizationType.TABLE,
            query_lines=[
                "fields @timestamp, @message",
                'filter @message like "Request:"',
                'parse @message \'"route": "*",\' as route',
                "stats count(*) as count by route",
                "sort count desc",
                "limit 20",
            ],
        )

        titiler_referer_analysis_widget = cloudwatch.LogQueryWidget(
            log_group_names=[titiler_log_group],
            title="titiler requests by request referer",
            width=6,
            height=8,
            view=cloudwatch.LogQueryVisualizationType.TABLE,
            query_lines=[
                "fields @timestamp, @message",
                'filter @message like "Request:"',
                'parse @message \'"referer": "*"\' as referer',
                "stats count(*) as count by referer",
                "sort count desc",
                "limit 20",
            ],
        )

        titiler_url_analysis_widget = cloudwatch.LogQueryWidget(
            log_group_names=[titiler_log_group],
            title="titiler /cog requests by url scheme and netloc",
            width=6,
            height=8,
            view=cloudwatch.LogQueryVisualizationType.TABLE,
            query_lines=[
                "fields @timestamp, @message",
                'filter @message like "Request:"',
                'parse @message \'"url_scheme": "*"\' as url_scheme',
                'parse @message \'"url_netloc": "*"\' as url_netloc',
                "filter ispresent(url_scheme)",
                "stats count(*) as count by url_scheme, url_netloc",
                "sort count desc",
                "limit 20",
            ],
        )

        titiler_collection_analysis_widget = cloudwatch.LogQueryWidget(
            log_group_names=[titiler_log_group],
            title="titiler /collections requests by collection id",
            width=6,
            height=8,
            view=cloudwatch.LogQueryVisualizationType.TABLE,
            query_lines=[
                "fields @timestamp, @message",
                'filter @message like "Request:"',
                'parse @message \'"route": "*"\' as route',
                'filter route like "/collections/"',
                "parse @message '\"path_params\": {*}' as path_params",
                "stats count(*) as count by path_params.collection_id as collection_id",
                "sort count desc",
                "limit 20",
            ],
        )

        titiler_searches_analysis_widget = cloudwatch.LogQueryWidget(
            log_group_names=[titiler_log_group],
            title="titiler /searches requests by search id",
            width=6,
            height=8,
            view=cloudwatch.LogQueryVisualizationType.TABLE,
            query_lines=[
                "fields @timestamp, @message",
                'filter @message like "Request:"',
                'parse @message \'"route": "*"\' as route',
                'filter route like "/searches/"',
                "parse @message '\"path_params\": {*}' as path_params",
                "stats count(*) as count by path_params.search_id as search_id",
                "sort count desc",
                "limit 20",
            ],
        )

        eoapi_dashboard.add_widgets(
            titiler_route_log_widget,
            titiler_collection_analysis_widget,
            titiler_searches_analysis_widget,
            titiler_url_analysis_widget,
            titiler_referer_analysis_widget,
        )

        # ── STAC Ingestor ──────────────────────────────────────────────────
        if ingestor_config:
            ingestor_data_access_role = iam.Role.from_role_arn(
                self,
                "ingestor-data-access-role",
                ingestor_config.data_access_role_arn,
            )

            ingestor_domain_name_options = (
                apigateway.DomainNameOptions(
                    domain_name=ingestor_config.domain_name,
                    certificate=acm.Certificate.from_certificate_arn(
                        self,
                        "ingestorCustomDomainNameCertificate",
                        certificate_arn,
                    ),
                )
                if ingestor_config.domain_name and certificate_arn
                else None
            )

            eoapi_cdk.StacIngestor(
                self,
                "stac-ingestor",
                vpc=vpc,
                stac_url=stac_api_lambda.url,
                data_access_role=ingestor_data_access_role,
                stage=stage,
                stac_db_secret=pgstac_db.pgstac_secret,
                stac_db_security_group=pgstac_db.security_group,
                subnet_selection=ec2.SubnetSelection(
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
                ),
                api_env={
                    "JWKS_URL": ingestor_config.jwks_url,
                    "REQUESTER_PAYS": "true",
                },
                pgstac_version=pgstac_db_config.pgstac_version,
                ingestor_domain_name_options=ingestor_domain_name_options,
            )

        # ── STAC Browser ───────────────────────────────────────────────────
        log_bucket = s3.Bucket.from_bucket_attributes(
            self, "LoggingBucket", bucket_arn=logging_bucket_arn
        )

        if stac_browser_config:
            root_path = "index.html"

            stac_browser_bucket = s3.Bucket(
                self,
                "stacBrowserBucket",
                access_control=s3.BucketAccessControl.PRIVATE,
                removal_policy=RemovalPolicy.DESTROY,
                block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                bucket_name=f"maap-stac-browser-{stage}",
                enforce_ssl=True,
            )

            stac_browser_origin = cloudfront.Distribution(
                self,
                "stacBrowserDistro",
                default_behavior=cloudfront.BehaviorOptions(
                    origin=origins.S3Origin(stac_browser_bucket)
                ),
                default_root_object=root_path,
                domain_names=[stac_browser_config.custom_domain_name],
                certificate=acm.Certificate.from_certificate_arn(
                    self,
                    "stacBrowserCustomDomainNameCertificate",
                    stac_browser_config.certificate_arn,
                ),
                enable_logging=True,
                log_bucket=log_bucket,
                log_file_prefix=f"stac-browser-{type}",
                error_responses=[
                    cloudfront.ErrorResponse(
                        http_status=403,
                        response_http_status=200,
                        response_page_path=f"/{root_path}",
                        ttl=Duration.seconds(0),
                    ),
                    cloudfront.ErrorResponse(
                        http_status=404,
                        response_http_status=200,
                        response_page_path=f"/{root_path}",
                        ttl=Duration.seconds(0),
                    ),
                ],
                web_acl_id=web_acl_arn,
            )

            stac_catalog_url = (
                stac_api_config.custom_domain_name
                if stac_api_config.custom_domain_name
                and stac_api_config.custom_domain_name.startswith("https://")
                else (
                    f"https://{stac_api_config.custom_domain_name}/"
                    if stac_api_config.custom_domain_name
                    else stac_api_lambda.url
                )
            )

            eoapi_cdk.StacBrowser(
                self,
                "stac-browser",
                bucket_arn=stac_browser_bucket.bucket_arn,
                stac_catalog_url=stac_catalog_url,
                github_repo_tag=stac_browser_config.repo_tag,
                website_index_document=root_path,
            )

            account_id = Aws.ACCOUNT_ID
            distribution_arn = (
                f"arn:aws:cloudfront::{account_id}:distribution/"
                f"{stac_browser_origin.distribution_id}"
            )

            stac_browser_bucket.add_to_resource_policy(
                iam.PolicyStatement(
                    sid="AllowCloudFrontServicePrincipal",
                    effect=iam.Effect.ALLOW,
                    actions=["s3:GetObject"],
                    principals=[iam.ServicePrincipal("cloudfront.amazonaws.com")],
                    resources=[stac_browser_bucket.arn_for_objects("*")],
                    conditions={"StringEquals": {"aws:SourceArn": distribution_arn}},
                )
            )

            log_bucket.add_to_resource_policy(
                iam.PolicyStatement(
                    sid="AllowCloudFrontServicePrincipal",
                    effect=iam.Effect.ALLOW,
                    actions=["s3:PutObject"],
                    resources=[log_bucket.arn_for_objects("AWSLogs/*")],
                    principals=[iam.ServicePrincipal("cloudfront.amazonaws.com")],
                    conditions={"StringEquals": {"aws:SourceArn": distribution_arn}},
                )
            )

        # ── STAC item loader ───────────────────────────────────────────────
        stac_loader = eoapi_cdk.StacLoader(
            self,
            "stac-item-loader",
            pgstac_db=pgstac_db,
            vpc=vpc,
            subnet_selection=api_subnet_selection,
            batch_size=500,
            lambda_timeout_seconds=300,
            max_batching_window_minutes=5,
            environment={"CREATE_COLLECTIONS_IF_MISSING": "TRUE"},
        )

        pgstac_db.pgstac_secret.grant_read(stac_loader.lambda_function)

        stac_loader.lambda_function.connections.allow_to(
            pgstac_db.connection_target,
            ec2.Port.tcp(5432),
            "allow connections from stac-item-loader",
        )

        # ── Item generators ────────────────────────────────────────────────
        if add_stactools_item_generator:
            stactools_item_generator = eoapi_cdk.StactoolsItemGenerator(
                self,
                "stactools-item-generator",
                item_load_topic_arn=stac_loader.topic.topic_arn,
                vpc=vpc,
                subnet_selection=api_subnet_selection,
            )
            stactools_item_generator.lambda_function.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["s3:GetObject"],
                    resources=["arn:aws:s3:::*/*"],
                )
            )
            stac_loader.topic.grant_publish(stactools_item_generator.lambda_function)

        if dps_stac_item_gen_config:
            dps_stac_item_generator = DpsStacItemGenerator(
                self,
                "dps-item-generator",
                DpsStacItemGeneratorProps(
                    item_load_topic_arn=stac_loader.topic.topic_arn,
                    role_arn=dps_stac_item_gen_config.item_gen_role_arn,
                    inbound_topic_arns=dps_stac_item_gen_config.inbound_topic_arns,
                    user_stac_collection_id_registry=(
                        dps_stac_item_gen_config.user_stac_collection_id_registry
                    ),
                    vpc=vpc,
                    subnet_selection=api_subnet_selection,
                    stage=stage,
                ),
            )

            stac_loader.topic.grant_publish(dps_stac_item_generator.lambda_function)
