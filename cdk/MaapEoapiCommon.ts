import { aws_s3 as s3 } from "aws-cdk-lib";
import { Duration, RemovalPolicy, Stack, StackProps } from "aws-cdk-lib";
import { Construct } from "constructs";

export interface MaapEoapiCommonProps extends StackProps {
  /**
   * Stage for this stack. Used for naming resources.
   */
  stage: string;
}

/**
 * MaapEoapiCommon Stack
 *
 * This stack contains shared resources that are used by both the pgSTAC and userSTAC stacks.
 * Any resources that need to be accessed or referenced by multiple stacks should be placed here
 * to avoid circular dependencies and ensure proper resource sharing.
 *
 * Examples of shared resources include:
 * - Logging buckets for centralized log collection
 * - Monitoring resources
 * - IAM roles or policies that are used across stacks
 *
 * This pattern ensures clean separation of concerns while enabling resource reuse
 * across the MAAP eoAPI infrastructure.
 */
export class MaapEoapiCommon extends Stack {
  /**
   * S3 bucket for centralized logging across all MAAP eoAPI stacks.
   * This bucket is used by both pgSTAC and userSTAC stacks for storing access logs
   * and other operational logs.
   */
  public readonly loggingBucket: s3.Bucket;

  /**
   * Constructs the MaapEoapiCommon stack with shared resources.
   *
   * @param scope - The scope in which to define this construct
   * @param id - The scoped construct ID. Must be unique amongst siblings
   * @param props - Stack properties including the deployment stage
   */
  constructor(scope: Construct, id: string, props: MaapEoapiCommonProps) {
    super(scope, id, props);

    const { stage } = props;

    this.loggingBucket = new s3.Bucket(this, "maapLoggingBucket", {
      accessControl: s3.BucketAccessControl.LOG_DELIVERY_WRITE,
      removalPolicy: RemovalPolicy.RETAIN,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      bucketName: `maap-service-logging-${stage}`,
      enforceSSL: true,
      lifecycleRules: [
        {
          enabled: true,
          expiration: Duration.days(395),
        },
      ],
    });
  }
}
