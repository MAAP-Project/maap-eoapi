import {
  aws_ec2 as ec2,
  aws_iam as iam,
  aws_secretsmanager as secretsmanager,
  Stack,
} from "aws-cdk-lib";
import { Construct } from "constructs";

import * as fs from "fs";
import * as path from "path";

export interface PgBouncerProps {
  /**
   * Name for the pgbouncer instance
   */
  instanceName: string;

  /**
   * VPC to deploy PgBouncer into
   */
  vpc: ec2.IVpc;

  /**
   * The RDS instance to connect to
   */
  database: {
    connections: ec2.Connections;
    secret: secretsmanager.ISecret;
  };

  /**
   * Security groups that need access to PgBouncer
   */
  clientSecurityGroups: ec2.ISecurityGroup[];

  /**
   * Whether to deploy in public subnet
   * @default false
   */
  usePublicSubnet?: boolean;

  /**
   * Instance type for PgBouncer
   * @default t3.small
   */
  instanceType?: ec2.InstanceType;

  /**
   * PgBouncer configuration options
   */
  pgBouncerConfig: {
    poolMode: "transaction" | "session" | "statement";
    maxClientConn: number;
    defaultPoolSize: number;
    minPoolSize: number;
    reservePoolSize: number;
    reservePoolTimeout: number;
    maxDbConnections: number;
    maxUserConnections: number;
  };
}

export class PgBouncer extends Construct {
  public readonly securityGroup: ec2.ISecurityGroup;
  public readonly instance: ec2.Instance;
  public readonly endpoint: string;

  constructor(scope: Construct, id: string, props: PgBouncerProps) {
    super(scope, id);

    const {
      vpc,
      database,
      clientSecurityGroups,
      usePublicSubnet = false,
      instanceType = ec2.InstanceType.of(
        ec2.InstanceClass.T3,
        ec2.InstanceSize.MICRO,
      ),
      pgBouncerConfig = {
        poolMode: "transaction",
        maxClientConn: 200,
        defaultPoolSize: 5,
        minPoolSize: 0,
        reservePoolSize: 5,
        reservePoolTimeout: 5,
        maxDbConnections: 40,
        maxUserConnections: 40,
      },
    } = props;

    // Create security group for PgBouncer
    this.securityGroup = new ec2.SecurityGroup(this, "SecurityGroup", {
      vpc,
      description: "Security group for PgBouncer instance",
      allowAllOutbound: true,
    });

    // Allow incoming PostgreSQL traffic from client security groups
    clientSecurityGroups.forEach((clientSg, index) => {
      this.securityGroup.addIngressRule(
        clientSg,
        ec2.Port.tcp(5432),
        `Allow PostgreSQL access from client security group ${index + 1}`,
      );
    });

    // Allow PgBouncer to connect to RDS
    database.connections.allowFrom(
      this.securityGroup,
      ec2.Port.tcp(5432),
      "Allow PgBouncer to connect to RDS",
    );

    // Create role for PgBouncer instance
    const role = new iam.Role(this, "InstanceRole", {
      assumedBy: new iam.ServicePrincipal("ec2.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          "AmazonSSMManagedInstanceCore",
        ),
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          "CloudWatchAgentServerPolicy",
        ),
      ],
    });

    // Add policy to allow reading RDS credentials from Secrets Manager
    role.addToPolicy(
      new iam.PolicyStatement({
        actions: ["secretsmanager:GetSecretValue"],
        resources: [database.secret.secretArn],
      }),
    );

    // Create PgBouncer instance
    this.instance = new ec2.Instance(this, "Instance", {
      vpc,
      vpcSubnets: {
        subnetType: usePublicSubnet
          ? ec2.SubnetType.PUBLIC
          : ec2.SubnetType.PRIVATE_WITH_EGRESS,
      },
      instanceType,
      instanceName: props.instanceName,
      machineImage: ec2.MachineImage.fromSsmParameter(
        "/aws/service/canonical/ubuntu/server/jammy/stable/current/amd64/hvm/ebs-gp2/ami-id",
        { os: ec2.OperatingSystemType.LINUX },
      ),
      securityGroup: this.securityGroup,
      role,
      detailedMonitoring: true,
      blockDevices: [
        {
          deviceName: "/dev/xvda",
          volume: ec2.BlockDeviceVolume.ebs(20, {
            volumeType: ec2.EbsDeviceVolumeType.GP3,
            encrypted: true,
            deleteOnTermination: true,
          }),
        },
      ],
      userData: this.loadUserDataScript(pgBouncerConfig, database),
      userDataCausesReplacement: true,
    });

    // Set the endpoint
    this.endpoint = this.instance.instancePrivateIp;
  }

  private loadUserDataScript(
    pgBouncerConfig: Required<NonNullable<PgBouncerProps["pgBouncerConfig"]>>,
    database: { secret: secretsmanager.ISecret },
  ): ec2.UserData {
    const userDataScript = ec2.UserData.forLinux();

    // Set environment variables with configuration parameters
    userDataScript.addCommands(
      'export SECRET_ARN="' + database.secret.secretArn + '"',
      'export REGION="' + Stack.of(this).region + '"',
      'export POOL_MODE="' + pgBouncerConfig.poolMode + '"',
      'export MAX_CLIENT_CONN="' + pgBouncerConfig.maxClientConn + '"',
      'export DEFAULT_POOL_SIZE="' + pgBouncerConfig.defaultPoolSize + '"',
      'export MIN_POOL_SIZE="' + pgBouncerConfig.minPoolSize + '"',
      'export RESERVE_POOL_SIZE="' + pgBouncerConfig.reservePoolSize + '"',
      'export RESERVE_POOL_TIMEOUT="' +
        pgBouncerConfig.reservePoolTimeout +
        '"',
      'export MAX_DB_CONNECTIONS="' + pgBouncerConfig.maxDbConnections + '"',
      'export MAX_USER_CONNECTIONS="' +
        pgBouncerConfig.maxUserConnections +
        '"',
    );

    // Load the startup script
    const scriptPath = path.join(__dirname, "./scripts/pgbouncer-setup.sh");
    let script = fs.readFileSync(scriptPath, "utf8");

    userDataScript.addCommands(script);

    return userDataScript;
  }
}
