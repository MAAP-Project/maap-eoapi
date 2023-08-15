export class Config {
  readonly stage: string;
  readonly version: string;
  readonly tags: Record<string, string>;
  readonly jwksUrl: string;
  readonly dataAccessRoleArn: string;
  readonly stacApiIntegrationApiArn: string;
  readonly dbAllocatedStorage: number;
  readonly certificateArn: string | undefined;
  readonly ingestorDomainName: string | undefined;
  readonly stacApiCustomDomainName: string | undefined;
  readonly titilerPgStacApiCustomDomainName: string | undefined;

  constructor() {
    if (!process.env.STAGE) throw Error("Must provide STAGE");
    this.stage = process.env.STAGE;
    this.version = process.env.npm_package_version!; // Set by node.js
    this.tags = {
      project: "MAAP",
      author: String(process.env.AUTHOR),
      gitCommit : String(process.env.COMMIT_SHA),
      gitRepository: String(process.env.GIT_REPOSITORY),
      version: String(process.env.VERSION),
      stage: this.stage,
    };
    if (!process.env.JWKS_URL) throw Error("Must provide JWKS_URL");
    this.jwksUrl = process.env.JWKS_URL;
    if (!process.env.DATA_ACCESS_ROLE_ARN) throw Error("Must provide DATA_ACCESS_ROLE_ARN");
    this.dataAccessRoleArn = process.env.DATA_ACCESS_ROLE_ARN!;
    if (!process.env.STAC_API_INTEGRATION_API_ARN) throw Error("Must provide STAC_API_INTEGRATION_API_ARN");
    this.stacApiIntegrationApiArn = process.env.STAC_API_INTEGRATION_API_ARN!;
    if (!process.env.DB_ALLOCATED_STORAGE) throw Error("Must provide DB_ALLOCATED_STORAGE");
    this.dbAllocatedStorage = Number(process.env.DB_ALLOCATED_STORAGE!);

    this.certificateArn = process.env.CERTIFICATE_ARN;
    this.ingestorDomainName = process.env.INGESTOR_DOMAIN_NAME;
    this.titilerPgStacApiCustomDomainName = process.env.TITILER_PGSTAC_API_CUSTOM_DOMAIN_NAME;
    this.stacApiCustomDomainName = process.env.STAC_API_CUSTOM_DOMAIN_NAME;
  }

  /**
   * Helper to generate id of stack
   * @param serviceId Identifier of service
   * @returns Full id of stack
   */
  buildStackName = (serviceId: string): string =>
    `MAAP-STAC-${this.stage}-${serviceId}`;
}
