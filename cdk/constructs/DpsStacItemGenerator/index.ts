import {
  aws_lambda as lambda,
  aws_sqs as sqs,
  aws_sns as sns,
  aws_sns_subscriptions as snsSubscriptions,
  aws_lambda_event_sources as lambdaEventSources,
  aws_logs as logs,
  Duration,
  CfnOutput,
} from "aws-cdk-lib";
import { Construct } from "constructs";
import * as path from "path";
import { Role } from "aws-cdk-lib/aws-iam";

export interface DpsStacItemGeneratorProps {
  /**
   * The lambda runtime to use for the item generation function.
   *
   * The function is containerized using Docker and can accommodate various
   * stactools packages. The runtime version should be compatible with the
   * packages you plan to use for STAC item generation.
   *
   * @default lambda.Runtime.PYTHON_3_11
   */
  readonly lambdaRuntime?: lambda.Runtime;

  /**
   * The timeout for the item generation lambda in seconds.
   *
   * This should accommodate the time needed to:
   * - Install stactools packages using uvx
   * - Download and process source data
   * - Generate STAC metadata
   * - Publish results to SNS
   *
   * The SQS visibility timeout will be set to this value plus 10 seconds.
   *
   * @default 120
   */
  readonly lambdaTimeoutSeconds?: number;

  /**
   * Memory size for the lambda function in MB.
   *
   * Higher memory allocation may be needed for processing large geospatial
   * datasets or when stactools packages have high memory requirements.
   * More memory also provides proportionally more CPU power.
   *
   * @default 1024
   */
  readonly memorySize?: number;

  /**
   * Maximum number of concurrent executions.
   *
   * This controls how many item generation tasks can run simultaneously.
   * Higher concurrency enables faster processing of large batches but
   * may strain downstream systems or external data sources.
   *
   * @default 100
   */
  readonly maxConcurrency?: number;

  /**
   * SQS batch size for lambda event source.
   *
   * This determines how many generation requests are processed together
   * in a single lambda invocation. Unlike the loader, generation typically
   * processes items individually, so smaller batch sizes are common.
   *
   * @default 10
   */
  readonly batchSize?: number;

  /**
   * Additional environment variables for the lambda function.
   *
   * These will be merged with default environment variables including
   * ITEM_LOAD_TOPIC_ARN and LOG_LEVEL. Use this for custom configuration
   * or to pass credentials for external data sources.
   */
  readonly environment?: { [key: string]: string };

  /**
   * ARN of the SNS topic to publish generated items to.
   *
   * This is typically the topic from a StacItemLoader construct.
   * Generated STAC items will be published here for downstream
   * processing and database insertion.
   */
  readonly itemLoadTopicArn: string;

  readonly roleArn: string;
}

export class DpsStacItemGenerator extends Construct {
  /**
   * The SQS queue that buffers item generation requests.
   *
   * This queue receives messages from the SNS topic containing ItemRequest
   * payloads. It's configured with a visibility timeout that matches the
   * Lambda timeout plus buffer time to prevent duplicate processing.
   */
  public readonly queue: sqs.Queue;

  /**
   * Dead letter queue for failed item generation attempts.
   *
   * Messages that fail processing after 5 attempts are sent here for
   * inspection and potential replay. This helps with debugging stactools
   * package issues, network failures, or malformed requests.
   */
  public readonly deadLetterQueue: sqs.Queue;

  /**
   * The SNS topic that receives item generation requests.
   *
   * External systems publish ItemRequest messages to this topic to trigger
   * STAC item generation. The topic fans out to the SQS queue for processing.
   */
  public readonly topic: sns.Topic;

  /**
   * The Lambda function that generates STAC items
   */
  public readonly lambdaFunction: lambda.Function;

  constructor(scope: Construct, id: string, props: DpsStacItemGeneratorProps) {
    super(scope, id);

    const timeoutSeconds = props.lambdaTimeoutSeconds ?? 120;
    const batchSize = props.batchSize ?? 10;
    const lambdaRuntime = props.lambdaRuntime ?? lambda.Runtime.PYTHON_3_11;

    // Create dead letter queue
    this.deadLetterQueue = new sqs.Queue(this, "DeadLetterQueue", {
      retentionPeriod: Duration.days(14),
    });

    // Create main queue
    this.queue = new sqs.Queue(this, "Queue", {
      visibilityTimeout: Duration.seconds(timeoutSeconds + 10),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      deadLetterQueue: {
        maxReceiveCount: 5,
        queue: this.deadLetterQueue,
      },
    });

    // Create SNS topic
    this.topic = new sns.Topic(this, "Topic", {
      displayName: `${id}-ItemGenTopic`,
    });

    // Subscribe the queue to the topic
    this.topic.addSubscription(
      new snsSubscriptions.SqsSubscription(this.queue),
    );

    this.lambdaFunction = new lambda.Function(this, "Function", {
      runtime: lambdaRuntime,
      role: Role.fromRoleArn(this, "dps-item-gen-role", props.roleArn),
      handler: "handler.handler",
      code: lambda.Code.fromDockerBuild(__dirname, {
        file: "runtime/Dockerfile",
        platform: "linux/amd64",
        buildArgs: {
          PYTHON_VERSION: lambdaRuntime.toString().replace("python", ""),
        },
      }),
      memorySize: props.memorySize ?? 1024,
      timeout: Duration.seconds(timeoutSeconds),
      reservedConcurrentExecutions: batchSize,
      logRetention: logs.RetentionDays.ONE_WEEK,
      environment: {
        ITEM_LOAD_TOPIC_ARN: props.itemLoadTopicArn,
        LOG_LEVEL: "INFO",
        ...props.environment,
      },
    });

    // Add SQS event source to the lambda
    this.lambdaFunction.addEventSource(
      new lambdaEventSources.SqsEventSource(this.queue, {
        batchSize: batchSize,
        reportBatchItemFailures: true,
        maxConcurrency: props.maxConcurrency ?? 100,
      }),
    );

    // Grant permissions to publish to the item load topic
    // Note: This will be granted externally since we only have the ARN
    // The consuming construct should handle this permission

    // Create outputs
    new CfnOutput(this, "TopicArn", {
      value: this.topic.topicArn,
      description: "ARN of the StactoolsItemGenerator SNS Topic",
      exportName: "stactools-item-generator-topic-arn",
    });

    new CfnOutput(this, "QueueUrl", {
      value: this.queue.queueUrl,
      description: "URL of the StactoolsItemGenerator SQS Queue",
      exportName: "stactools-item-generator-queue-url",
    });

    new CfnOutput(this, "DeadLetterQueueUrl", {
      value: this.deadLetterQueue.queueUrl,
      description: "URL of the StactoolsItemGenerator Dead Letter Queue",
      exportName: "stactools-item-generator-deadletter-queue-url",
    });

    new CfnOutput(this, "FunctionName", {
      value: this.lambdaFunction.functionName,
      description: "Name of the StactoolsItemGenerator Lambda Function",
      exportName: "stactools-item-generator-function-name",
    });
  }
}
