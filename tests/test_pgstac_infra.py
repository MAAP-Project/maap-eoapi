"""Tests for PgStacInfra stack - Python equivalent of test/pgstac-infra.test.ts"""
from __future__ import annotations

from unittest.mock import patch

import aws_cdk as cdk
from aws_cdk import assertions, aws_ec2 as ec2

from cdk.pgstac_infra import (
    CollectionTransactionsConfig,
    PgStacDbConfig,
    PgStacInfra,
    StacApiConfig,
    TitilerPgstacConfig,
)

# Minimal required props for test builds
BASE_TITILER_CONFIG = TitilerPgstacConfig(
    mosaic_host="example.com/table-name",
    buckets_path="./titiler_buckets.yaml",
    data_access_role_arn="arn:aws:iam::123456789012:role/test-role",
    custom_domain_name="titiler.example.com",
)

BASE_PGSTAC_DB_CONFIG = PgStacDbConfig(
    instance_type=ec2.InstanceType("t3.micro"),
    subnet_public=False,
    allocated_storage=20,
    pgstac_version="0.9.5",
)


def build_template(overrides: dict | None = None) -> assertions.Template:
    app = cdk.App()
    network_stack = cdk.Stack(app, "NetworkStack")
    vpc = ec2.Vpc(
        network_stack,
        "Vpc",
        max_azs=2,
        nat_gateways=1,
        subnet_configuration=[
            ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC),
            ec2.SubnetConfiguration(
                name="private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            ec2.SubnetConfiguration(
                name="isolated", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
        ],
    )

    props = dict(
        vpc=vpc,
        stage="test",
        type="internal",
        version="1.0.0",
        web_acl_arn="arn:aws:wafv2:us-east-1:123456789012:global/webacl/test-acl",
        logging_bucket_arn="arn:aws:s3:::test-logging-bucket",
        pgstac_db_config=BASE_PGSTAC_DB_CONFIG,
        stac_api_config=StacApiConfig(custom_domain_name="stac-api.example.com"),
        titiler_pgstac_config=BASE_TITILER_CONFIG,
    )
    props |= (overrides or {})

    # Mock lambda Code.from_docker_build so tests don't need Docker
    mock_code = cdk.aws_lambda.Code.from_asset("test")
    with patch("aws_cdk.aws_lambda.Code.from_docker_build", return_value=mock_code):
        stack = PgStacInfra(app, "TestPgStacInfra", **props)

    return assertions.Template.from_stack(stack)


class TestPgStacInfraStacRuntimeWiring:
    def test_uses_custom_stac_handler_and_keeps_transactions_disabled_by_default(self):
        template = build_template(
            {
                "type": "public",
                "stac_api_config": StacApiConfig(
                    custom_domain_name="public-stac.example.com",
                    integration_api_arn=(
                        "arn:aws:execute-api:us-west-2:123456789012:api-id/stage/GET/"
                    ),
                ),
            }
        )

        template.has_resource_properties(
            "AWS::Lambda::Function",
            {
                "Handler": "eoapi.stac.handler.handler",
                "Environment": {
                    "Variables": assertions.Match.object_like(
                        {
                            "STAC_FASTAPI_TITLE": "MAAP public STAC API (test)",
                            "STAC_FASTAPI_LANDING_ID": "maap-public-stac-api-test",
                            "ENABLED_EXTENSIONS": (
                                "query,sort,fields,filter,free_text,"
                                "pagination,collection_search"
                            ),
                        }
                    )
                },
            },
        )

        # No transaction secret should exist
        secrets = template.find_resources(
            "AWS::SecretsManager::Secret",
            {
                "Properties": {
                    "Name": "/maap-eoapi/test/public/stac-collection-transaction-basic-auth"
                }
            },
        )
        assert len(secrets) == 0
        template.resource_count_is("AWS::SSM::Parameter", 1)

    def test_enables_collection_transactions_with_stack_managed_secret(self):
        template = build_template(
            {
                "stac_api_config": StacApiConfig(
                    custom_domain_name="internal-stac.example.com",
                    transactions=CollectionTransactionsConfig(auth_mode="basic"),
                ),
            }
        )

        template.has_resource_properties(
            "AWS::SecretsManager::Secret",
            {
                "Description": (
                    "Basic auth secret for MAAP internal STAC collection transactions (test)"
                ),
                "Name": (
                    "/maap-eoapi/test/internal/stac-collection-transaction-basic-auth"
                ),
                "GenerateSecretString": assertions.Match.object_like(
                    {
                        "GenerateStringKey": "password",
                        "SecretStringTemplate": '{"username":"maap-internal-stac-writer"}',
                    }
                ),
            },
        )

        template.has_resource_properties(
            "AWS::Lambda::Function",
            {
                "Handler": "eoapi.stac.handler.handler",
                "Environment": {
                    "Variables": assertions.Match.object_like(
                        {
                            "ENABLED_EXTENSIONS": (
                                "query,sort,fields,filter,free_text,pagination,"
                                "collection_search,collection_transaction"
                            ),
                            "MAAP_TRANSACTION_AUTH_MODE": "basic",
                        }
                    )
                },
            },
        )

        template.has_resource_properties(
            "AWS::SSM::Parameter",
            {
                "Name": (
                    "/maap-eoapi/test/internal/stac-collection-transaction-auth-secret-arn"
                )
            },
        )

    def test_uses_explicit_transaction_auth_secret_arn_override(self):
        template = build_template(
            {
                "stac_api_config": StacApiConfig(
                    custom_domain_name="internal-stac.example.com",
                    transactions=CollectionTransactionsConfig(
                        auth_mode="basic",
                        auth_secret_arn=(
                            "arn:aws:secretsmanager:us-west-2:123456789012:"
                            "secret:existing-auth-abcdef"
                        ),
                    ),
                ),
            }
        )

        # No managed secret should be created
        secrets = template.find_resources(
            "AWS::SecretsManager::Secret",
            {
                "Properties": {
                    "Name": (
                        "/maap-eoapi/test/internal/stac-collection-transaction-basic-auth"
                    )
                }
            },
        )
        assert len(secrets) == 0

        template.has_resource_properties(
            "AWS::Lambda::Function",
            {
                "Handler": "eoapi.stac.handler.handler",
                "Environment": {
                    "Variables": assertions.Match.object_like(
                        {
                            "MAAP_TRANSACTION_AUTH_SECRET_ARN": (
                                "arn:aws:secretsmanager:us-west-2:123456789012:"
                                "secret:existing-auth-abcdef"
                            )
                        }
                    )
                },
            },
        )
