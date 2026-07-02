from __future__ import annotations

from typing import Optional

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from constructs import Construct


class VpcStack(cdk.Stack):
    vpc: ec2.Vpc

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        nat_gateway_count: Optional[int] = None,  # Default: one per availability zone.
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        self.vpc = ec2.Vpc(
            self,
            "vpc",
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    cidr_mask=24,
                    name="ingress",
                    subnet_type=ec2.SubnetType.PUBLIC,
                ),
                ec2.SubnetConfiguration(
                    cidr_mask=24,
                    name="application",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                ),
                ec2.SubnetConfiguration(
                    cidr_mask=28,
                    name="rds",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                ),
            ],
            nat_gateways=nat_gateway_count,
        )

        self.vpc.add_gateway_endpoint(
            "DynamoDbEndpoint",
            service=ec2.GatewayVpcEndpointAwsService.DYNAMODB,
        )

        self.vpc.add_interface_endpoint(
            "SecretsManagerEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
        )

        self.vpc.add_gateway_endpoint(
            "S3Endpoint",
            service=ec2.GatewayVpcEndpointAwsService.S3,
        )

        public_subnets = self.vpc.select_subnets(
            subnet_type=ec2.SubnetType.PUBLIC
        ).subnets
        self.export_value(public_subnets[0].subnet_id)
        self.export_value(public_subnets[1].subnet_id)
