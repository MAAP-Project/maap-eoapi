import * as aws_ec2 from "aws-cdk-lib/aws-ec2";

export class Config {
  readonly stage: string;
  readonly version: string;
  readonly dbInstanceType: aws_ec2.InstanceType;
  readonly tags: Record<string, string>;
  readonly jwksUrl: string;
  readonly titilerDataAccessRoleArn: string;
  readonly ingestorDataAccessRoleArn: string;
  readonly stacApiIntegrationApiArn: string;
  readonly dbAllocatedStorage: number;
  readonly mosaicHost: string;
  readonly certificateArn: string | undefined;
  readonly ingestorDomainName: string | undefined;
  readonly stacApiCustomDomainName: string;
  readonly titilerPgStacApiCustomDomainName: string | undefined;
  readonly stacBrowserRepoTag: string;
  readonly stacBrowserCustomDomainName: string;
  readonly stacBrowserCertificateArn: string;
  readonly pgstacVersion: string;
  readonly webAclArn: string;
  readonly bastionHostIpv4AllowList: string[];
  readonly userStacItemGenRoleArn: string;

  constructor() {
    // These are required environment variables and cannot be undefined
    const requiredVariables = [
      { name: "STAGE", value: process.env.STAGE },
      { name: "DB_INSTANCE_TYPE", value: process.env.DB_INSTANCE_TYPE },
      { name: "JWKS_URL", value: process.env.JWKS_URL },
      {
        name: "TITILER_DATA_ACCESS_ROLE_ARN",
        value: process.env.TITILER_DATA_ACCESS_ROLE_ARN,
      },
      {
        name: "INGESTOR_DATA_ACCESS_ROLE_ARN",
        value: process.env.INGESTOR_DATA_ACCESS_ROLE_ARN,
      },
      {
        name: "STAC_API_INTEGRATION_API_ARN",
        value: process.env.STAC_API_INTEGRATION_API_ARN,
      },
      { name: "DB_ALLOCATED_STORAGE", value: process.env.DB_ALLOCATED_STORAGE },
      { name: "MOSAIC_HOST", value: process.env.MOSAIC_HOST },
      {
        name: "STAC_BROWSER_REPO_TAG",
        value: process.env.STAC_BROWSER_REPO_TAG,
      },
      {
        name: "STAC_BROWSER_CUSTOM_DOMAIN_NAME",
        value: process.env.STAC_BROWSER_CUSTOM_DOMAIN_NAME,
      },
      {
        name: "STAC_BROWSER_CERTIFICATE_ARN",
        value: process.env.STAC_BROWSER_CERTIFICATE_ARN,
      },
      {
        name: "STAC_API_CUSTOM_DOMAIN_NAME",
        value: process.env.STAC_API_CUSTOM_DOMAIN_NAME,
      },
      {
        name: "PGSTAC_VERSION",
        value: process.env.PGSTAC_VERSION,
      },
      {
        name: "WEB_ACL_ARN",
        value: process.env.WEB_ACL_ARN,
      },
    ];

    for (const variable of requiredVariables) {
      if (!variable.value) {
        throw new Error(`Must provide ${variable.name}`);
      }
    }

    this.stage = process.env.STAGE!;

    this.jwksUrl = process.env.JWKS_URL!;
    this.titilerDataAccessRoleArn = process.env.TITILER_DATA_ACCESS_ROLE_ARN!;
    this.ingestorDataAccessRoleArn = process.env.INGESTOR_DATA_ACCESS_ROLE_ARN!;
    this.stacApiIntegrationApiArn = process.env.STAC_API_INTEGRATION_API_ARN!;

    try {
      this.dbInstanceType = new aws_ec2.InstanceType(
        process.env.DB_INSTANCE_TYPE!,
      );
    } catch (error) {
      throw new Error(
        `Invalid DB_INSTANCE_TYPE: ${process.env.DB_INSTANCE_TYPE!}. Error: ${error}`,
      );
    }

    this.dbAllocatedStorage = Number(process.env.DB_ALLOCATED_STORAGE!);
    this.mosaicHost = process.env.MOSAIC_HOST!;
    this.stacBrowserRepoTag = process.env.STAC_BROWSER_REPO_TAG!;
    this.stacBrowserCustomDomainName =
      process.env.STAC_BROWSER_CUSTOM_DOMAIN_NAME!;
    this.stacBrowserCertificateArn = process.env.STAC_BROWSER_CERTIFICATE_ARN!;
    this.stacApiCustomDomainName = process.env.STAC_API_CUSTOM_DOMAIN_NAME!;

    this.version = process.env.npm_package_version!; // Set by node.js
    this.tags = {
      project: "MAAP",
      author: String(process.env.AUTHOR),
      gitCommit: String(process.env.COMMIT_SHA),
      gitRepository: String(process.env.GIT_REPOSITORY),
      version: String(process.env.VERSION),
      stage: this.stage,
    };

    this.certificateArn = process.env.CERTIFICATE_ARN;
    this.ingestorDomainName = process.env.INGESTOR_DOMAIN_NAME;
    this.titilerPgStacApiCustomDomainName =
      process.env.TITILER_PGSTAC_API_CUSTOM_DOMAIN_NAME;
    this.pgstacVersion = process.env.PGSTAC_VERSION!;
    this.webAclArn = process.env.WEB_ACL_ARN!;

    this.bastionHostIpv4AllowList = [];

    // Parse IP config from environment variable
    // Format: JSON with label-IP pairs
    // Example: '{"office":"192.168.1.1", "vpn":"10.0.0.1"}'
    if (process.env.BASTION_HOST_IPV4_ALLOW_LIST) {
      const parsedConfig = JSON.parse(process.env.BASTION_HOST_IPV4_ALLOW_LIST);

      this.bastionHostIpv4AllowList = Object.values(parsedConfig);
    }

    this.userStacItemGenRoleArn = process.env.USER_STAC_ITEM_GEN_ROLE_ARN!;
  }

  /**
   * Helper to generate id of stack
   * @param serviceId Identifier of service
   * @returns Full id of stack
   */
  buildStackName = (serviceId: string): string =>
    `MAAP-STAC-${this.stage}-${serviceId}`;
}
