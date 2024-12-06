import {
  aws_ec2 as ec2,
  aws_iam as iam,
  aws_secretsmanager as secretsmanager,
  Stack,
} from "aws-cdk-lib";
import { Construct } from "constructs";

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
  pgBouncerConfig?: {
    poolMode?: "transaction" | "session" | "statement";
    maxClientConn?: number;
    defaultPoolSize?: number;
    minPoolSize?: number;
    reservePoolSize?: number;
    reservePoolTimeout?: number;
    maxDbConnections?: number;
    maxUserConnections?: number;
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
        ec2.InstanceSize.NANO,
      ),
      pgBouncerConfig = {
        poolMode: "transaction",
        maxClientConn: 1000,
        defaultPoolSize: 20,
        minPoolSize: 10,
        reservePoolSize: 5,
        reservePoolTimeout: 5,
        maxDbConnections: 50,
        maxUserConnections: 50,
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

    // Create user data script
    const userDataScript = ec2.UserData.forLinux();
    userDataScript.addCommands(
      "set -euxo pipefail", // Add error handling and debugging

      // add the postgres repository
      "curl https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -",
      "sudo sh -c 'echo \"deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main\" > /etc/apt/sources.list.d/pgdg.list'",

      // Install required packages
      "apt-get update",
      "sleep 5",
      "DEBIAN_FRONTEND=noninteractive apt-get install -y pgbouncer jq awscli",

      `SECRET_ARN=${database.secret.secretArn}`,
      `REGION=${Stack.of(this).region}`,

      "echo 'Fetching secret from ARN: ' ${SECRET_ARN}",
      "SECRET=$(aws secretsmanager get-secret-value --secret-id $SECRET_ARN --region $REGION --query SecretString --output text)",

      "# Parse database credentials",
      "DB_HOST=$(echo $SECRET | jq -r '.host')",
      "DB_PORT=$(echo $SECRET | jq -r '.port')",
      "DB_NAME=$(echo $SECRET | jq -r '.dbname')",
      "DB_USER=$(echo $SECRET | jq -r '.username')",
      "DB_PASSWORD=$(echo $SECRET | jq -r '.password')",

      "echo 'Creating PgBouncer configuration...'",

      "# Create pgbouncer.ini",
      "cat <<EOC > /etc/pgbouncer/pgbouncer.ini",
      "[databases]",
      "* = host=$DB_HOST port=$DB_PORT dbname=$DB_NAME",
      "",
      "[pgbouncer]",
      "listen_addr = 0.0.0.0",
      "listen_port = 5432",
      "auth_type = md5",
      "auth_file = /etc/pgbouncer/userlist.txt",
      `pool_mode = ${pgBouncerConfig.poolMode}`,
      `max_client_conn = ${pgBouncerConfig.maxClientConn}`,
      `default_pool_size = ${pgBouncerConfig.defaultPoolSize}`,
      `min_pool_size = ${pgBouncerConfig.minPoolSize}`,
      `reserve_pool_size = ${pgBouncerConfig.reservePoolSize}`,
      `reserve_pool_timeout = ${pgBouncerConfig.reservePoolTimeout}`,
      `max_db_connections = ${pgBouncerConfig.maxDbConnections}`,
      `max_user_connections = ${pgBouncerConfig.maxUserConnections}`,
      "ignore_startup_parameters = application_name,search_path",
      "EOC",

      "# Create userlist.txt",
      'echo "\\"$DB_USER\\" \\"$DB_PASSWORD\\"" > /etc/pgbouncer/userlist.txt',

      "# Set correct permissions",
      "chown postgres:postgres /etc/pgbouncer/pgbouncer.ini /etc/pgbouncer/userlist.txt",
      "chmod 600 /etc/pgbouncer/pgbouncer.ini /etc/pgbouncer/userlist.txt",

      // Enable and start pgbouncer service
      "systemctl enable pgbouncer",
      "systemctl restart pgbouncer",

      // Health check
      "# Create health check script",
      "cat <<EOC > /usr/local/bin/check-pgbouncer.sh",
      "#!/bin/bash",
      "if ! pgrep pgbouncer > /dev/null; then",
      "    systemctl start pgbouncer",
      "    echo 'PgBouncer was down, restarted'",
      "fi",
      "EOC",
      "chmod +x /usr/local/bin/check-pgbouncer.sh",

      "# Add to crontab",
      "(crontab -l 2>/dev/null; echo '* * * * * /usr/local/bin/check-pgbouncer.sh') | crontab -",

      // CloudWatch
      "# Install CloudWatch agent",
      "wget https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb",
      "dpkg -i amazon-cloudwatch-agent.deb",

      "# Create CloudWatch agent configuration",
      "cat <<EOC > /opt/aws/amazon-cloudwatch-agent/bin/config.json",
      "{",
      '  "agent": {',
      '    "metrics_collection_interval": 60,',
      '    "run_as_user": "root"',
      "  },",
      '  "logs": {',
      '    "logs_collected": {',
      '      "files": {',
      '        "collect_list": [',
      "          {",
      '            "file_path": "/var/log/pgbouncer/pgbouncer.log",',
      '            "log_group_name": "/pgbouncer/logs",',
      '            "log_stream_name": "{instance_id}",',
      '            "timestamp_format": "%Y-%m-%d %H:%M:%S"',
      "          }",
      "        ]",
      "      }",
      "    }",
      "  },",
      '  "metrics": {',
      '    "metrics_collected": {',
      '      "procstat": [',
      "        {",
      '          "pattern": "pgbouncer",',
      '          "measurement": [',
      '            "cpu_usage",',
      '            "memory_rss",',
      '            "read_bytes",',
      '            "write_bytes"',
      "          ]",
      "        }",
      "      ]",
      "    }",
      "  }",
      "}",
      "EOC",

      "# Start CloudWatch agent",
      "/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -s -c file:/opt/aws/amazon-cloudwatch-agent/bin/config.json",
      "systemctl enable amazon-cloudwatch-agent",
      "systemctl start amazon-cloudwatch-agent",

      // PgBouncer metrics
      "# Create PgBouncer metrics script",
      "cat <<EOC > /usr/local/bin/pgbouncer-metrics.sh",
      "#!/bin/bash",
      "PGPASSWORD=$DB_PASSWORD psql -h localhost -p 5432 -U $DB_USER pgbouncer -c 'SHOW POOLS;' | \\",
      'awk \'NR>2 {print "pgbouncer_pool,database=" $1 " cl_active=" $3 ",cl_waiting=" $4 ",sv_active=" $5 ",sv_idle=" $6 ",sv_used=" $7 ",sv_tested=" $8 ",sv_login=" $9 ",maxwait=" $10}\' | \\',
      "while IFS= read -r line; do",
      '    aws cloudwatch put-metric-data --namespace PgBouncer --metric-name "$line" --region $REGION',
      "done",
      "EOC",
      "chmod +x /usr/local/bin/pgbouncer-metrics.sh",

      "# Add to crontab",
      "(crontab -l 2>/dev/null; echo '* * * * * /usr/local/bin/pgbouncer-metrics.sh') | crontab -",
    );

    // ensure the init script gets run on every boot
    userDataScript.addCommands(
      // Create a per-boot script
      "mkdir -p /var/lib/cloud/scripts/per-boot",

      "cat <<'EOF' > /var/lib/cloud/scripts/per-boot/00-run-config.sh",
      "#!/bin/bash",
      "# Stop existing services",
      "systemctl stop pgbouncer",

      "# Re-run configuration",
      "curl -o /tmp/user-data http://169.254.169.254/latest/user-data",
      "chmod +x /tmp/user-data",
      "/tmp/user-data",
      "EOF",

      "chmod +x /var/lib/cloud/scripts/per-boot/00-run-config.sh",
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
      instanceName: `pgbouncer-${Date.now()}`,
      machineImage: ec2.MachineImage.fromSsmParameter(
        "/aws/service/canonical/ubuntu/server/jammy/stable/current/amd64/hvm/ebs-gp2/ami-id",
        { os: ec2.OperatingSystemType.LINUX },
      ),
      securityGroup: this.securityGroup,
      role,
      detailedMonitoring: true, // Enable detailed CloudWatch monitoring
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
      userData: userDataScript,
      userDataCausesReplacement: true,
    });

    // Set the endpoint
    this.endpoint = this.instance.instancePrivateIp;
  }
}
