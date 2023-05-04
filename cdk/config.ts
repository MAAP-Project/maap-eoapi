export class Config {
  readonly stage: string;
  readonly version: string;
  readonly tags: Record<string, string>;
  readonly jwksUrl: string;
  readonly dataAccessRoleArn: string;
  readonly browserCloudFrontDistrbutionArn: string;

  constructor() {
    if (!process.env.STAGE) throw Error("Must provide STAGE");
    this.stage = process.env.STAGE;
    this.version = process.env.npm_package_version!; // Set by node.js
    this.tags = {
      created_by: process.env.USER!,
      version: this.version,
      stage: this.stage,
    };
    if (!process.env.JWKS_URL) throw Error("Must provide JWKS_URL");
    this.jwksUrl = process.env.JWKS_URL;
    if (!process.env.DATA_ACCESS_ROLE_ARN) throw Error("Must provide DATA_ACCESS_ROLE_ARN");
    this.dataAccessRoleArn = process.env.DATA_ACCESS_ROLE_ARN!;
    if (!process.env.BROWSER_CLOUDFRONT_DISTRIBUTION_ARN) throw Error("Must provide BROWSER_CLOUDFRONT_DISTRIBUTION_ARN");
    this.browserCloudFrontDistrbutionArn = process.env.BROWSER_CLOUDFRONT_DISTRIBUTION_ARN!;
  }

  /**
   * Helper to generate id of stack
   * @param serviceId Identifier of service
   * @returns Full id of stack
   */
  buildStackName = (serviceId: string): string =>
    `MAAP-STAC-${this.stage}-${serviceId}`;
}
