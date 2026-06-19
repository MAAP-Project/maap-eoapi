from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_s3 as s3
from constructs import Construct


class MaapEoapiCommon(cdk.Stack):
    """
    MaapEoapiCommon Stack

    This stack contains shared resources that are used by both the pgSTAC and userSTAC stacks.
    Any resources that need to be accessed or referenced by multiple stacks should be placed here
    to avoid circular dependencies and ensure proper resource sharing.

    Examples of shared resources include:
    - Logging buckets for centralized log collection
    - Monitoring resources
    - IAM roles or policies that are used across stacks

    This pattern ensures clean separation of concerns while enabling resource reuse
    across the MAAP eoAPI infrastructure.
    """

    logging_bucket: s3.Bucket
    """S3 bucket for centralized logging across all MAAP eoAPI stacks.
    Used by both pgSTAC and userSTAC stacks for storing access logs and other operational logs.
    """

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        stage: str,  # Used for naming resources.
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        self.logging_bucket = s3.Bucket(
            self,
            "maapLoggingBucket",
            access_control=s3.BucketAccessControl.LOG_DELIVERY_WRITE,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            bucket_name=f"maap-service-logging-{stage}",
            enforce_ssl=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    enabled=True,
                    expiration=cdk.Duration.days(395),
                )
            ],
        )
