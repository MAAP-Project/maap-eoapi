from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from aws_cdk import (
    CfnOutput,
    Duration,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_lambda_event_sources as lambda_event_sources,
    aws_logs as logs,
    aws_sns as sns,
    aws_sns_subscriptions as sns_subscriptions,
    aws_sqs as sqs,
)
from constructs import Construct

_CONSTRUCT_DIR = Path(__file__).parent / "DpsStacItemGenerator"


@dataclass
class DpsStacItemGeneratorProps:
    item_load_topic_arn: str
    role_arn: str
    vpc: Optional[ec2.IVpc] = None
    subnet_selection: Optional[ec2.SubnetSelection] = None
    lambda_runtime: Optional[lambda_.Runtime] = None
    lambda_timeout_seconds: Optional[int] = None
    memory_size: Optional[int] = None
    max_concurrency: Optional[int] = None
    batch_size: Optional[int] = None
    environment: Optional[dict[str, str]] = None
    inbound_topic_arns: Optional[list[str]] = None
    user_stac_collection_id_registry: Optional[dict[str, list[str]]] = None
    stage: Optional[str] = None


class DpsStacItemGenerator(Construct):
    queue: sqs.Queue
    dead_letter_queue: sqs.Queue
    lambda_function: lambda_.Function

    def __init__(
        self,
        scope: Construct,
        id: str,
        props: DpsStacItemGeneratorProps,
    ) -> None:
        super().__init__(scope, id)

        timeout_seconds = props.lambda_timeout_seconds or 120
        batch_size = props.batch_size or 10
        lambda_runtime = props.lambda_runtime or lambda_.Runtime.PYTHON_3_12
        stage = props.stage or "default"

        # Dead letter queue
        self.dead_letter_queue = sqs.Queue(
            self,
            "DeadLetterQueue",
            retention_period=Duration.days(14),
        )

        # Main queue
        self.queue = sqs.Queue(
            self,
            "Queue",
            delivery_delay=Duration.minutes(1),
            visibility_timeout=Duration.minutes(5),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=5,
                queue=self.dead_letter_queue,
            ),
        )

        # Subscribe to each externally-managed inbound topic
        for i, topic_arn in enumerate(props.inbound_topic_arns or []):
            topic = sns.Topic.from_topic_arn(self, f"InboundTopic{i}", topic_arn)
            topic.add_subscription(sns_subscriptions.SqsSubscription(self.queue))

        python_version = lambda_runtime.to_string().replace("python", "")

        self.lambda_function = lambda_.Function(
            self,
            "Function",
            runtime=lambda_runtime,
            role=iam.Role.from_role_arn(self, "dps-item-gen-role", props.role_arn),
            handler="dps_stac_item_generator.handler.handler",
            code=lambda_.Code.from_docker_build(
                str(_CONSTRUCT_DIR),
                file="runtime/Dockerfile",
                platform="linux/amd64",
                build_args={"PYTHON_VERSION": python_version},
            ),
            memory_size=props.memory_size or 1024,
            timeout=Duration.seconds(timeout_seconds),
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                "ITEM_LOAD_TOPIC_ARN": props.item_load_topic_arn,
                "LOG_LEVEL": "INFO",
                **(
                    {
                        "USER_STAC_COLLECTION_ID_REGISTRY": json.dumps(
                            props.user_stac_collection_id_registry
                        )
                    }
                    if props.user_stac_collection_id_registry
                    else {}
                ),
                **(props.environment or {}),
            },
            vpc=props.vpc,
            vpc_subnets=props.subnet_selection,
        )

        # SQS event source
        self.lambda_function.add_event_source(
            lambda_event_sources.SqsEventSource(
                self.queue,
                batch_size=batch_size,
                report_batch_item_failures=True,
                max_concurrency=props.max_concurrency or 100,
            )
        )

        # CloudFormation outputs
        CfnOutput(
            self,
            "QueueUrl",
            value=self.queue.queue_url,
            description="URL of the DpsStacItemGenerator SQS Queue",
            export_name=f"dps-stac-item-generator-queue-url-{stage}",
        )

        CfnOutput(
            self,
            "DeadLetterQueueUrl",
            value=self.dead_letter_queue.queue_url,
            description="URL of the DpsStacItemGenerator Dead Letter Queue",
            export_name=f"dps-stac-item-generator-deadletter-queue-url-{stage}",
        )

        CfnOutput(
            self,
            "FunctionName",
            value=self.lambda_function.function_name,
            description="Name of the DpsStacItemGenerator Lambda Function",
            export_name=f"dps-stac-item-generator-function-name-{stage}",
        )
