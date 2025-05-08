import {
  aws_apigatewayv2 as apigatewayv2,
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
      certificateArn,
      webAclArn,
      pgstacDbConfig,
      titilerPgstacConfig,
      stacApiConfig,
      stacBrowserConfig,
      ingestorConfig,
    } = props;

    // Pgstac Database
    const pgstacDb = new PgStacDatabase(this, "pgstac-db", {
      vpc,
      allowMajorVersionUpgrade: true,
      engine: rds.DatabaseInstanceEngine.postgres({
        version: rds.PostgresEngineVersion.VER_14,
      }),
      vpcSubnets: {
        subnetType: pgstacDbConfig.subnetPublic
          ? ec2.SubnetType.PUBLIC
          : ec2.SubnetType.PRIVATE_ISOLATED,
      },
      allocatedStorage: pgstacDbConfig.allocatedStorage,
      instanceType: pgstacDbConfig.instanceType,
      addPgbouncer: true,
      pgstacVersion: pgstacDbConfig.pgstacVersion,
    });

    const apiSubnetSelection: ec2.SubnetSelection = {
      subnetType: pgstacDbConfig.subnetPublic
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
        stacApiConfig.customDomainName && certificateArn
          ? new apigatewayv2.DomainName(this, "stac-api-domain-name", {
              domainName: stacApiConfig.customDomainName,
              certificate: acm.Certificate.fromCertificateArn(
                this,
                "stacApiCustomDomainNameCertificate",
                certificateArn,
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
      sourceArn: stacApiConfig.integrationApiArn,
    });

    // titiler-pgstac
    const titilerDataAccessRole = iam.Role.fromRoleArn(
      this,
      "titiler-data-access-role",
      titilerPgstacConfig.dataAccessRoleArn,
    );

    const fileContents = readFileSync(titilerPgstacConfig.bucketsPath, "utf8");
    const buckets = load(fileContents) as string[];

    const titilerPgstacLambdaOptions: CustomLambdaFunctionProps = {
      code: lambda.Code.fromDockerBuild(__dirname, {
        file: "dockerfiles/Dockerfile.raster",
        buildArgs: { PYTHON_VERSION: "3.11" },
      }),
      role: titilerDataAccessRole,
    };

    const titilerPgstacApiEnv: Record<string, string> = {
      NAME: `MAAP titiler pgstac API (${stage})`,
      VERSION: version,
      DESCRIPTION: "titiler pgstac API for the MAAP STAC system.",
    };

    // Only add mosaic configuration if mosaicHost is provided
    if (titilerPgstacConfig.mosaicHost) {
      titilerPgstacApiEnv.MOSAIC_BACKEND = "dynamodb://";
      titilerPgstacApiEnv.MOSAIC_HOST = titilerPgstacConfig.mosaicHost;
    }

    const titilerPgstacApi = new TitilerPgstacApiLambda(
      this,
      "titiler-pgstac-api",
      {
        apiEnv: titilerPgstacApiEnv,
        vpc,
        db: pgstacDb.connectionTarget,
        dbSecret: pgstacDb.pgstacSecret,
        subnetSelection: apiSubnetSelection,
        buckets: buckets,
        titilerPgstacApiDomainName:
          titilerPgstacConfig.customDomainName && certificateArn
            ? new apigatewayv2.DomainName(
                this,
                "titiler-pgstac-api-domain-name",
                {
                  domainName: titilerPgstacConfig.customDomainName,
                  certificate: acm.Certificate.fromCertificateArn(
                    this,
                    "titilerPgStacCustomDomainNameCertificate",
                    certificateArn,
                  ),
                },
              )
            : undefined,
        lambdaFunctionOptions: titilerPgstacLambdaOptions,
      },
    );

    if (titilerPgstacConfig.mosaicHost) {
      // Add dynamodb permissions to the titiler-pgstac Lambda for mosaicjson support
      const tableName = titilerPgstacConfig.mosaicHost.split("/", 2)[1];

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
        titilerPgstacApi.titilerPgstacLambdaFunction.addToRolePolicy(
          permission,
        );
      });
    }

    // Configure titiler-pgstac for pgbouncer
    titilerPgstacApi.titilerPgstacLambdaFunction.connections.allowTo(
      pgstacDb.connectionTarget,
      ec2.Port.tcp(5432),
      "allow connections from titiler",
    );

    // API logging dashboard

    const eoapiDashboard = new cloudwatch.Dashboard(this, "eoAPIDashboard", {
      dashboardName: `eoAPI-${stage}`,
    });

    // widget showing count by application route
    const titilerRouteLogWidget = new cloudwatch.LogQueryWidget({
      logGroupNames: [
        titilerPgstacApi.titilerPgstacLambdaFunction.logGroup.logGroupName,
      ],
      title: "titiler requests by route",
      width: 12,
      height: 8,
      view: cloudwatch.LogQueryVisualizationType.TABLE,
      queryLines: [
        "fields @timestamp, @message",
        'filter @message like "Request:"',
        'parse @message \'"route": "*",\' as route',
        "stats count(*) as count by route",
        "sort count desc",
        "limit 20",
      ],
    });

    // widget showing count by referer
    const titilerRefererAnalysisWidget = new cloudwatch.LogQueryWidget({
      logGroupNames: [
        titilerPgstacApi.titilerPgstacLambdaFunction.logGroup.logGroupName,
      ],
      title: "titiler requests by request referer",
      width: 6,
      height: 8,
      view: cloudwatch.LogQueryVisualizationType.TABLE,
      queryLines: [
        "fields @timestamp, @message",
        'filter @message like "Request:"',
        'parse @message \'"referer": "*"\' as referer',
        "stats count(*) as count by referer",
        "sort count desc",
        "limit 20",
      ],
    });

    // widget showing count by scheme/netloc for routes with url parameter
    const titilerUrlAnalysisWidget = new cloudwatch.LogQueryWidget({
      logGroupNames: [
        titilerPgstacApi.titilerPgstacLambdaFunction.logGroup.logGroupName,
      ],
      title: "titiler /cog requests by url scheme and netloc",
      width: 6,
      height: 8,
      view: cloudwatch.LogQueryVisualizationType.TABLE,
      queryLines: [
        "fields @timestamp, @message",
        'filter @message like "Request:"',
        'parse @message \'"url_scheme": "*"\' as url_scheme',
        'parse @message \'"url_netloc": "*"\' as url_netloc',
        "filter ispresent(url_scheme)",
        "stats count(*) as count by url_scheme, url_netloc",
        "sort count desc",
        "limit 20",
      ],
    });

    // widget showing count by collection_id for /collections requests
    const titilerCollectionAnalysisWidget = new cloudwatch.LogQueryWidget({
      logGroupNames: [
        titilerPgstacApi.titilerPgstacLambdaFunction.logGroup.logGroupName,
      ],
      title: "titiler /collections requests by collection id",
      width: 6,
      height: 8,
      view: cloudwatch.LogQueryVisualizationType.TABLE,
      queryLines: [
        "fields @timestamp, @message",
        'filter @message like "Request:"',
        'parse @message \'"route": "*"\' as route',
        'filter route like "/collections/"',
        "parse @message '\"path_params\": {*}' as path_params",
        "stats count(*) as count by path_params.collection_id as collection_id",
        "sort count desc",
        "limit 20",
      ],
    });

    // widget showing count by collection_id for /collections requests
    const titilerSearchesAnalysisWidget = new cloudwatch.LogQueryWidget({
      logGroupNames: [
        titilerPgstacApi.titilerPgstacLambdaFunction.logGroup.logGroupName,
      ],
      title: "titiler /searches requests by search id",
      width: 6,
      height: 8,
      view: cloudwatch.LogQueryVisualizationType.TABLE,
      queryLines: [
        "fields @timestamp, @message",
        'filter @message like "Request:"',
        'parse @message \'"route": "*"\' as route',
        'filter route like "/searches/"',
        "parse @message '\"path_params\": {*}' as path_params",
        "stats count(*) as count by path_params.search_id as search_id",
        "sort count desc",
        "limit 20",
      ],
    });
    eoapiDashboard.addWidgets(
      titilerRouteLogWidget,
      titilerCollectionAnalysisWidget,
      titilerSearchesAnalysisWidget,
      titilerUrlAnalysisWidget,
      titilerRefererAnalysisWidget,
    );

    // STAC Ingestor
    if (ingestorConfig) {
      const ingestorDataAccessRole = iam.Role.fromRoleArn(
        this,
        "ingestor-data-access-role",
        ingestorConfig.dataAccessRoleArn,
      );

      new BastionHost(this, "bastion-host", {
        vpc,
        db: pgstacDb.db,
        ipv4Allowlist: ingestorConfig.ipv4AllowList,
        userData: ec2.UserData.custom(
          readFileSync(ingestorConfig.userDataPath, { encoding: "utf-8" }),
        ),
        createElasticIp: ingestorConfig.createElasticIp,
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
          JWKS_URL: ingestorConfig.jwksUrl,
          REQUESTER_PAYS: "true",
        },
        pgstacVersion: pgstacDbConfig.pgstacVersion,
        ingestorDomainNameOptions:
          ingestorConfig.domainName && certificateArn
            ? {
                domainName: ingestorConfig.domainName,
                certificate: acm.Certificate.fromCertificateArn(
                  this,
                  "ingestorCustomDomainNameCertificate",
                  certificateArn,
                ),
              }
            : undefined,
      });
    }

    // STAC Browser Infrastructure
    const rootPath = "index.html";

    const stacBrowserBucket = new s3.Bucket(this, "stacBrowserBucket", {
      accessControl: s3.BucketAccessControl.PRIVATE,
      removalPolicy: RemovalPolicy.DESTROY,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      bucketName: `maap-stac-browser-${stage}`,
      enforceSSL: true,
    });

    const loggingBucket = new s3.Bucket(this, "maapLoggingBucket", {
      accessControl: s3.BucketAccessControl.LOG_DELIVERY_WRITE,
      removalPolicy: RemovalPolicy.RETAIN,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      bucketName: `maap-logging-${stage}`,
      enforceSSL: true,
      lifecycleRules: [
        {
          enabled: true,
          expiration: Duration.days(395),
        },
      ],
    });

    const stacBrowserOrigin = new cloudfront.Distribution(
      this,
      "stacBrowserDistro",
      {
        defaultBehavior: { origin: new origins.S3Origin(stacBrowserBucket) },
        defaultRootObject: rootPath,
        domainNames: [stacBrowserConfig.customDomainName],
        certificate: acm.Certificate.fromCertificateArn(
          this,
          "stacBrowserCustomDomainNameCertificate",
          stacBrowserConfig.certificateArn,
        ),
        enableLogging: true,
        logBucket: loggingBucket,
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
        webAclId: webAclArn,
      },
    );

    new StacBrowser(this, "stac-browser", {
      bucketArn: stacBrowserBucket.bucketArn,
      stacCatalogUrl: stacApiConfig.customDomainName
        ? stacApiConfig.customDomainName.startsWith("https://")
          ? stacApiConfig.customDomainName
          : `https://${stacApiConfig.customDomainName}/`
        : stacApiLambda.url,
      githubRepoTag: stacBrowserConfig.repoTag,
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

    loggingBucket.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: "AllowCloudFrontServicePrincipal",
        effect: iam.Effect.ALLOW,
        actions: ["s3:PutObject"],
        resources: [loggingBucket.arnForObjects("AWSLogs/*")],
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
   * ARN of ACM certificate to use for eoAPI custom domains
   * Example: "arn:aws:acm:us-west-2:123456789012:certificate/12345678-1234-1234-1234-123456789012"
   */
  certificateArn?: string | undefined;

  /**
   * ARN of WAF Web ACL to use for eoAPI custom domains
   * Example: "arn:aws:wafv2:us-west-2:123456789012:webacl/12345678-1234-1234-1234-123456789012"
   */
  webAclArn: string;

  pgstacDbConfig: {
    /**
     * RDS Instance type
     */
    instanceType: ec2.InstanceType;

    /**
     * Flag to control whether database should be deployed into a
     * public subnet.
     */
    subnetPublic: boolean;

    /**
     * allocated storage for pgstac database
     */
    allocatedStorage: number;

    /**
     * version of pgstac to install on the database
     */
    pgstacVersion: string;
  };

  titilerPgstacConfig: {
    /**
     * mosaicjson dynamodb host for titiler in form of aws-region/table-name
     */
    mosaicHost?: string | undefined;

    /**
     * yaml file containing the list of buckets the titiler lambda should be granted access to
     */
    bucketsPath: string;

    /**
     * ARN of IAM role that will be assumed by the titiler Lambda
     */
    dataAccessRoleArn: string;

    /**
     * Domain name to use for titiler pgstac api. If defined, a new custom domain name will be created.
     * Example: "titiler-pgstac-api.dit.maap-project.org"
     */
    customDomainName?: string | undefined;
  };

  stacApiConfig: {
    /**
     * Domain name to use for stac api. If defined, a new CDN will be created.
     * Example: "stac-api.dit.maap-project.org""
     */
    customDomainName?: string;

    /**
     * STAC API api gateway source ARN to be granted STAC API lambda invoke permission.
     */
    integrationApiArn: string;
  };

  /**
   * Configuration for the STAC Browser
   */
  stacBrowserConfig: {
    /**
     * Tag of the stac-browser repo from https://github.com/radiantearth/stac-browser
     * Example: "v3.2.0"
     */
    repoTag: string;

    /**
     * Domain name for use in cloudfront distribution for stac-browser
     * Example: "stac-browser.maap-project.org"
     */
    customDomainName: string;

    /**
     * ARN of ACM certificate to use for Cloudfront Distribution (Must be us-east-1).
     * Example: "arn:aws:acm:us-west-2:123456789012:certificate/12345678-1234-1234-1234-123456789012"
     */
    certificateArn: string;
  };

  // === OPTIONAL COMPONENTS ===
  /**
   * Configuration for the STAC Ingestor. If not provided, STAC Ingestor will not be created.
   */
  ingestorConfig?: {
    /**
     * URL of JWKS endpoint, provided as output from ASDI-Auth.
     *
     * Example: "https://cognito-idp.{region}.amazonaws.com/{region}_{userpool_id}/.well-known/jwks.json"
     */
    jwksUrl: string;
    /**
     * ARN of IAM role that will be assumed by the STAC Ingestor.
     */
    dataAccessRoleArn: string;

    /**
     * Domain name to use for CDN. If defined, a new CDN will be created
     * Example: "stac.maap.xyz"
     */
    domainName?: string | undefined;

    /**
     * Where userdata.yaml is found.
     */
    userDataPath: string;

    /**
     * Which IPs to allow to access bastion host.
     */
    ipv4AllowList: string[];

    /**
     * Flag to control whether the Bastion Host should make a non-dynamic elastic IP.
     */
    createElasticIp?: boolean;
  };
}
