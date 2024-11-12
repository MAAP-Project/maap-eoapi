import {
  aws_certificatemanager as acm,
  aws_iam as iam,
  aws_ec2 as ec2,
  aws_lambda as lambda,
  aws_rds as rds,
  aws_s3 as s3,
  aws_cloudfront as cloudfront,
  aws_cloudfront_origins as origins,
} from "aws-cdk-lib";
import { Aws, Duration, RemovalPolicy, Stack, StackProps, Tags } from "aws-cdk-lib";
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
      jwksUrl,
      dataAccessRoleArn,
      allocatedStorage,
      mosaicHost,
      titilerBucketsPath,
    } = props;

    const maapLoggingBucket = new s3.Bucket(this, 'maapLoggingBucket', {
      accessControl: s3.BucketAccessControl.PRIVATE,
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
    })
    Tags.of(maapLoggingBucket).add("Name", maapLoggingBucket.bucketName);
    Tags.of(maapLoggingBucket).add("Owner", "MAAP Data Team");

    const { db, pgstacSecret } = new PgStacDatabase(this, "pgstac-db", {
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
      // set instance type to t3.micro if stage is test, otherwise t3.small
      instanceType:
        stage === "test"
          ? ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MICRO)
          : ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.SMALL),
    });

    const apiSubnetSelection: ec2.SubnetSelection = {
      subnetType: props.dbSubnetPublic
        ? ec2.SubnetType.PUBLIC
        : ec2.SubnetType.PRIVATE_WITH_EGRESS,
    };

    const stacApiLambda = new PgStacApiLambda(this, "pgstac-api", {
      apiEnv: {
        NAME: `MAAP STAC API (${stage})`,
        VERSION: version,
        DESCRIPTION: "STAC API for the MAAP STAC system.",
      },
      vpc,
      db,
      dbSecret: pgstacSecret,
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

    stacApiLambda.stacApiLambdaFunction.addPermission("ApiGatewayInvoke", {
      principal: new iam.ServicePrincipal("apigateway.amazonaws.com"),
      sourceArn: props.stacApiIntegrationApiArn,
    });

    const fileContents = readFileSync(titilerBucketsPath, "utf8");
    const buckets = load(fileContents) as string[];

    const titilerPgstacLambdaOptions: CustomLambdaFunctionProps = {
      code: lambda.Code.fromDockerBuild(__dirname, {
        file: "dockerfiles/Dockerfile.raster",
        buildArgs: { PYTHON_VERSION: "3.11" },
      }),
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
        db,
        dbSecret: pgstacSecret,
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

    new BastionHost(this, "bastion-host", {
      vpc,
      db,
      ipv4Allowlist: props.bastionIpv4AllowList,
      userData: ec2.UserData.custom(
        readFileSync(props.bastionUserDataPath, { encoding: "utf-8" }),
      ),
      createElasticIp: props.bastionHostCreateElasticIp,
    });

    const dataAccessRole = iam.Role.fromRoleArn(
      this,
      "data-access-role",
      dataAccessRoleArn,
    );

    new StacIngestor(this, "stac-ingestor", {
      vpc,
      stacUrl: stacApiLambda.url,
      dataAccessRole,
      stage,
      stacDbSecret: pgstacSecret,
      stacDbSecurityGroup: db.connections.securityGroups[0],
      subnetSelection: {
        subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
      },
      apiEnv: {
        JWKS_URL: jwksUrl,
        REQUESTER_PAYS: "true",
      },
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
    const stacBrowserBucket = new s3.Bucket(this, 'stacBrowserBucket', {
      accessControl: s3.BucketAccessControl.PRIVATE,
      removalPolicy: RemovalPolicy.DESTROY,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      bucketName: `maap-stac-browser-${stage}`,
      enforceSSL: true,
    })
    Tags.of(stacBrowserBucket).add("Name", stacBrowserBucket.bucketName);
    Tags.of(stacBrowserBucket).add("Owner", "MAAP Data Team");

    const stacBrowserOrigin = new cloudfront.Distribution(this, 'stacBrowserDistro', {
      defaultBehavior: { origin: new origins.S3Origin(stacBrowserBucket) },
      defaultRootObject: 'index.html',
      domainNames: [props.stacBrowserCustomDomainName],
      certificate: acm.Certificate.fromCertificateArn(
        this,
        "stacBrowserCustomDomainNameCertificate",
        props.stacBrowserCertificateArn,
      ),
      enableLogging: true,
      logBucket: maapLoggingBucket,
      logFilePrefix: 'stac-browser',
    });
    // Add dependencies for the Cloudfront distribution
    stacBrowserOrigin.node.addDependency(stacBrowserBucket);
    stacBrowserOrigin.node.addDependency(maapLoggingBucket);

    new StacBrowser(this, "stac-browser", {
      bucketArn: stacBrowserBucket.bucketArn,
      stacCatalogUrl: props.stacApiCustomDomainName,
      githubRepoTag: props.stacBrowserRepoTag,
      websiteIndexDocument: "index.html",
    })

    const accountId = Aws.ACCOUNT_ID;
    const distributionArn = `arn:aws:cloudfront::${accountId}:distribution/${stacBrowserOrigin.distributionId}`;

    stacBrowserBucket.addToResourcePolicy(new iam.PolicyStatement({
      sid: 'AllowCloudFrontServicePrincipal',
      effect: iam.Effect.ALLOW, 
      actions: ['s3:GetObject'],
      principals: [new iam.ServicePrincipal('cloudfront.amazonaws.com')],
      resources: [stacBrowserBucket.arnForObjects('*')],
      conditions: {
          'StringEquals': {
              'aws:SourceArn': distributionArn,
          }
      }
    }));
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
   * ARN of IAM role that will be assumed by the STAC Ingestor.
   */
  dataAccessRoleArn: string;

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
}
