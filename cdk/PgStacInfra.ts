import {
  aws_certificatemanager as acm,
  aws_iam as iam,
  aws_ec2 as ec2,
  aws_lambda as lambda,
  aws_rds as rds,
  aws_s3 as s3,
  aws_cloudfront as cloudfront,
  aws_cloudfront_origins as origins,
  aws_cloudwatch as cloudwatch,
} from "aws-cdk-lib";
import { Aws, Duration, RemovalPolicy, Stack, StackProps } from "aws-cdk-lib";
import { Construct } from "constructs";
import {
  BastionHost,
  CustomLambdaFunctionProps,
  PgStacApiLambda,
  PgStacDatabase,
  StacIngestor,
  TitilerPgstacApiLambda,
  StacBrowser,
} from "eoapi-cdk";
import { DomainName } from "@aws-cdk/aws-apigatewayv2-alpha";
import { readFileSync } from "fs";
import { load } from "js-yaml";

export class PgStacInfra extends Stack {
  constructor(scope: Construct, id: string, props: Props) {
    super(scope, id, props);

    const stack = Stack.of(this);

    const {
      vpc,
      stage,
      version,
      dbInstanceType,
      jwksUrl,
      titilerDataAccessRoleArn,
      ingestorDataAccessRoleArn,
      allocatedStorage,
      mosaicHost,
      titilerBucketsPath,
      pgstacVersion,
    } = props;

    const maapLoggingBucket = new s3.Bucket(this, "maapLoggingBucket", {
      accessControl: s3.BucketAccessControl.LOG_DELIVERY_WRITE,
      removalPolicy: RemovalPolicy.DESTROY,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      bucketName: `maap-logging-${stage}`,
      enforceSSL: true,
      lifecycleRules: [
        {
          enabled: true,
          expiration: Duration.days(90),
        },
      ],
    });

    // Pgstac Database
    const pgstacDb = new PgStacDatabase(this, "pgstac-db", {
      vpc,
      allowMajorVersionUpgrade: true,
      engine: rds.DatabaseInstanceEngine.postgres({
        version: rds.PostgresEngineVersion.VER_14,
      }),
      vpcSubnets: {
        subnetType: props.dbSubnetPublic
          ? ec2.SubnetType.PUBLIC
          : ec2.SubnetType.PRIVATE_ISOLATED,
      },
      allocatedStorage: allocatedStorage,
      instanceType: dbInstanceType,
      addPgbouncer: true,
      pgstacVersion: pgstacVersion,
    });

    const apiSubnetSelection: ec2.SubnetSelection = {
      subnetType: props.dbSubnetPublic
        ? ec2.SubnetType.PUBLIC
        : ec2.SubnetType.PRIVATE_WITH_EGRESS,
    };

    // STAC API
    const stacApiLambda = new PgStacApiLambda(this, "pgstac-api", {
      apiEnv: {
        NAME: `MAAP STAC API (${stage})`,
        VERSION: version,
        DESCRIPTION: "STAC API for the MAAP STAC system.",
      },
      vpc,
      db: pgstacDb.connectionTarget,
      dbSecret: pgstacDb.pgstacSecret,
      subnetSelection: apiSubnetSelection,
      stacApiDomainName:
        props.stacApiCustomDomainName && props.certificateArn
          ? new DomainName(this, "stac-api-domain-name", {
              domainName: props.stacApiCustomDomainName,
              certificate: acm.Certificate.fromCertificateArn(
                this,
                "stacApiCustomDomainNameCertificate",
                props.certificateArn,
              ),
            })
          : undefined,
    });

    stacApiLambda.stacApiLambdaFunction.connections.allowTo(
      pgstacDb.connectionTarget,
      ec2.Port.tcp(5432),
      "allow connections from stac-fastapi-pgstac",
    );

    stacApiLambda.stacApiLambdaFunction.addPermission("ApiGatewayInvoke", {
      principal: new iam.ServicePrincipal("apigateway.amazonaws.com"),
      sourceArn: props.stacApiIntegrationApiArn,
    });

    // titiler-pgstac
    const titilerDataAccessRole = iam.Role.fromRoleArn(
      this,
      "titiler-data-access-role",
      titilerDataAccessRoleArn,
    );

    const fileContents = readFileSync(titilerBucketsPath, "utf8");
    const buckets = load(fileContents) as string[];

    const titilerPgstacLambdaOptions: CustomLambdaFunctionProps = {
      code: lambda.Code.fromDockerBuild(__dirname, {
        file: "dockerfiles/Dockerfile.raster",
        buildArgs: { PYTHON_VERSION: "3.11" },
      }),
      role: titilerDataAccessRole,
    };

    const titilerPgstacApi = new TitilerPgstacApiLambda(
      this,
      "titiler-pgstac-api",
      {
        apiEnv: {
          NAME: `MAAP titiler pgstac API (${stage})`,
          VERSION: version,
          DESCRIPTION: "titiler pgstac API for the MAAP STAC system.",
          MOSAIC_BACKEND: "dynamodb://",
          MOSAIC_HOST: mosaicHost,
        },
        vpc,
        db: pgstacDb.connectionTarget,
        dbSecret: pgstacDb.pgstacSecret,
        subnetSelection: apiSubnetSelection,
        buckets: buckets,
        titilerPgstacApiDomainName:
          props.titilerPgStacApiCustomDomainName && props.certificateArn
            ? new DomainName(this, "titiler-pgstac-api-domain-name", {
                domainName: props.titilerPgStacApiCustomDomainName,
                certificate: acm.Certificate.fromCertificateArn(
                  this,
                  "titilerPgStacCustomDomainNameCertificate",
                  props.certificateArn,
                ),
              })
            : undefined,
        lambdaFunctionOptions: titilerPgstacLambdaOptions,
      },
    );

    // Add dynamodb permissions to the titiler-pgstac Lambda for mosaicjson support
    const tableName = mosaicHost.split("/", 2)[1];

    const mosaicPerms = [
      new iam.PolicyStatement({
        actions: ["dynamodb:CreateTable", "dynamodb:DescribeTable"],
        resources: [
          `arn:aws:dynamodb:${stack.region}:${stack.account}:table/*`,
        ],
      }),
      new iam.PolicyStatement({
        actions: [
          "dynamodb:Query",
          "dynamodb:GetItem",
          "dynamodb:Scan",
          "dynamodb:PutItem",
          "dynamodb:BatchWriteItem",
        ],
        resources: [
          `arn:aws:dynamodb:${stack.region}:${stack.account}:table/${tableName}`,
        ],
      }),
    ];

    mosaicPerms.forEach((permission) => {
      titilerPgstacApi.titilerPgstacLambdaFunction.addToRolePolicy(permission);
    });

    // Configure titiler-pgstac for pgbouncer
    titilerPgstacApi.titilerPgstacLambdaFunction.connections.allowTo(
      pgstacDb.connectionTarget,
      ec2.Port.tcp(5432),
      "allow connections from titiler",
    );

    // titiler logging dashboard

    const titilerDashboard = new cloudwatch.Dashboard(
      this,
      "TitilerDashboard",
      {
        dashboardName: `titiler-${stage}`,
      },
    );

    // widget showing count by application route
    const titilerLogWidget = new cloudwatch.LogQueryWidget({
      logGroupNames: [
        titilerPgstacApi.titilerPgstacLambdaFunction.logGroup.logGroupName,
      ],
      title: "Titiler Lambda Logs",
      width: 24,
      height: 6,
      view: cloudwatch.LogQueryVisualizationType.TABLE,
      queryLines: [
        "fields @timestamp, @message",
        'filter @message like "Request:"',
        'parse @message \'"path_template": "*",\' as path_template',
        "stats count(*) as count by path_template",
        "sort count desc",
        "limit 20",
      ],
    });

    // widget showing count by scheme/netloc for routes with url parameter
    const schemeNetlocAnalysisWidget = new cloudwatch.LogQueryWidget({
      logGroupNames: [
        titilerPgstacApi.titilerPgstacLambdaFunction.logGroup.logGroupName,
      ],
      title: "URL Pattern Analysis (Scheme + Domain)",
      width: 24,
      height: 8,
      view: cloudwatch.LogQueryVisualizationType.TABLE,
      queryLines: [
        "fields @timestamp, @message",
        'filter @message like "Request:"',
        'parse @message \'"url_scheme":"*"\' as url_scheme',
        'parse @message \'"url_netloc":"*"\' as url_netloc',
        "filter ispresent(url_scheme)",
        "stats count(*) as count by url_scheme, url_netloc",
        "sort count desc",
        "limit 20",
      ],
    });
    titilerDashboard.addWidgets(titilerLogWidget, schemeNetlocAnalysisWidget);

    // STAC Ingestor
    const ingestorDataAccessRole = iam.Role.fromRoleArn(
      this,
      "ingestor-data-access-role",
      ingestorDataAccessRoleArn,
    );

    new BastionHost(this, "bastion-host", {
      vpc,
      db: pgstacDb.db,
      ipv4Allowlist: props.bastionIpv4AllowList,
      userData: ec2.UserData.custom(
        readFileSync(props.bastionUserDataPath, { encoding: "utf-8" }),
      ),
      createElasticIp: props.bastionHostCreateElasticIp,
    });

    new StacIngestor(this, "stac-ingestor", {
      vpc,
      stacUrl: stacApiLambda.url,
      dataAccessRole: ingestorDataAccessRole,
      stage,
      stacDbSecret: pgstacDb.pgstacSecret,
      stacDbSecurityGroup: pgstacDb.securityGroup!,
      subnetSelection: {
        subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
      },
      apiEnv: {
        JWKS_URL: jwksUrl,
        REQUESTER_PAYS: "true",
      },
      pgstacVersion,
      ingestorDomainNameOptions:
        props.IngestorDomainName && props.certificateArn
          ? {
              domainName: props.IngestorDomainName,
              certificate: acm.Certificate.fromCertificateArn(
                this,
                "ingestorCustomDomainNameCertificate",
                props.certificateArn,
              ),
            }
          : undefined,
    });

    // STAC Browser Infrastructure
    const rootPath = "index.html";

    const stacBrowserBucket = new s3.Bucket(this, "stacBrowserBucket", {
      accessControl: s3.BucketAccessControl.PRIVATE,
      removalPolicy: RemovalPolicy.DESTROY,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      bucketName: `maap-stac-browser-${stage}`,
      enforceSSL: true,
    });

    const stacBrowserOrigin = new cloudfront.Distribution(
      this,
      "stacBrowserDistro",
      {
        defaultBehavior: { origin: new origins.S3Origin(stacBrowserBucket) },
        defaultRootObject: rootPath,
        domainNames: [props.stacBrowserCustomDomainName],
        certificate: acm.Certificate.fromCertificateArn(
          this,
          "stacBrowserCustomDomainNameCertificate",
          props.stacBrowserCertificateArn,
        ),
        enableLogging: true,
        logBucket: maapLoggingBucket,
        logFilePrefix: "stac-browser",
        errorResponses: [
          {
            httpStatus: 403,
            responseHttpStatus: 200,
            responsePagePath: `/${rootPath}`,
            ttl: Duration.seconds(0),
          },
          {
            httpStatus: 404,
            responseHttpStatus: 200,
            responsePagePath: `/${rootPath}`,
            ttl: Duration.seconds(0),
          },
        ],
      },
    );

    new StacBrowser(this, "stac-browser", {
      bucketArn: stacBrowserBucket.bucketArn,
      stacCatalogUrl: props.stacApiCustomDomainName.startsWith("https://")
        ? props.stacApiCustomDomainName
        : `https://${props.stacApiCustomDomainName}/`,
      githubRepoTag: props.stacBrowserRepoTag,
      websiteIndexDocument: rootPath,
    });

    const accountId = Aws.ACCOUNT_ID;
    const distributionArn = `arn:aws:cloudfront::${accountId}:distribution/${stacBrowserOrigin.distributionId}`;

    stacBrowserBucket.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: "AllowCloudFrontServicePrincipal",
        effect: iam.Effect.ALLOW,
        actions: ["s3:GetObject"],
        principals: [new iam.ServicePrincipal("cloudfront.amazonaws.com")],
        resources: [stacBrowserBucket.arnForObjects("*")],
        conditions: {
          StringEquals: {
            "aws:SourceArn": distributionArn,
          },
        },
      }),
    );

    maapLoggingBucket.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: "AllowCloudFrontServicePrincipal",
        effect: iam.Effect.ALLOW,
        actions: ["s3:PutObject"],
        resources: [maapLoggingBucket.arnForObjects("AWSLogs/*")],
        principals: [new iam.ServicePrincipal("cloudfront.amazonaws.com")],
        conditions: {
          StringEquals: {
            "aws:SourceArn": distributionArn,
          },
        },
      }),
    );
  }
}

export interface Props extends StackProps {
  vpc: ec2.Vpc;

  /**
   * Stage this stack. Used for naming resources.
   */
  stage: string;

  /**
   * Version of this stack. Used to correlate codebase versions
   * to services running.
   */
  version: string;

  /**
   * RDS Instance type
   */
  dbInstanceType: ec2.InstanceType;

  /**
   * Flag to control whether database should be deployed into a
   * public subnet.
   */
  dbSubnetPublic?: boolean;

  /**
   * Where userdata.yaml is found.
   */
  bastionUserDataPath: string;

  /**
   * Which IPs to allow to access bastion host.
   */
  bastionIpv4AllowList: string[];

  /**
   * Flag to control whether the Bastion Host should make a non-dynamic elastic IP.
   */
  bastionHostCreateElasticIp?: boolean;

  /**
   * URL of JWKS endpoint, provided as output from ASDI-Auth.
   *
   * Example: "https://cognito-idp.{region}.amazonaws.com/{region}_{userpool_id}/.well-known/jwks.json"
   */
  jwksUrl: string;

  /**
   * ARN of IAM role that will be assumed by the titiler Lambda
   */
  titilerDataAccessRoleArn: string;

  /**
   * ARN of IAM role that will be assumed by the STAC Ingestor.
   */
  ingestorDataAccessRoleArn: string;
  /**
   * STAC API api gateway source ARN to be granted STAC API lambda invoke permission.
   */
  stacApiIntegrationApiArn: string;

  /**
   * allocated storage for pgstac database
   */
  allocatedStorage: number;

  /**
   * mosaicjson dynamodb host for titiler in form of aws-region/table-name
   */
  mosaicHost: string;

  /**
   * yaml file containing the list of buckets the titiler lambda should be granted access to
   */
  titilerBucketsPath: string;

  /**
   * ARN of ACM certificate to use for CDN.
   * Example: "arn:aws:acm:us-west-2:123456789012:certificate/12345678-1234-1234-1234-123456789012"
   */
  certificateArn?: string | undefined;

  /**
   * Domain name to use for CDN. If defined, a new CDN will be created
   * Example: "stac.maap.xyz"
   */
  IngestorDomainName?: string | undefined;

  /**
   * Domain name to use for titiler pgstac api. If defined, a new CDN will be created.
   * Example: "titiler-pgstac-api.dit.maap-project.org"
   */
  titilerPgStacApiCustomDomainName?: string | undefined;

  /**
   * Domain name to use for stac api. If defined, a new CDN will be created.
   * Example: "stac-api.dit.maap-project.org""
   */
  stacApiCustomDomainName: string;

  /**
   * Tag of the stac-browser repo from https://github.com/radiantearth/stac-browser
   * Example: "v3.2.0"
   */
  stacBrowserRepoTag: string;

  /**
   * Domain name for use in cloudfront distribution for stac-browser
   * Example: "stac-browser.maap-project.org"
   */
  stacBrowserCustomDomainName: string;

  /**
   * ARN of ACM certificate to use for Cloudfront Distribution (Must be us-east-1).
   * Example: "arn:aws:acm:us-west-2:123456789012:certificate/12345678-1234-1234-1234-123456789012"
   */
  stacBrowserCertificateArn: string;

  /**
   * version of pgstac to install on the database
   */
  pgstacVersion: string;
}
