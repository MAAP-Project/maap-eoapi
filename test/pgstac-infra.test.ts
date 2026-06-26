import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Match, Template } from "aws-cdk-lib/assertions";
import { PgStacInfra, Props } from "../cdk/PgStacInfra";

function buildTemplate(overrides: Partial<Props> = {}): Template {
  const app = new cdk.App();
  const networkStack = new cdk.Stack(app, "NetworkStack");
  const vpc = new ec2.Vpc(networkStack, "Vpc", {
    maxAzs: 2,
    natGateways: 1,
    subnetConfiguration: [
      {
        name: "public",
        subnetType: ec2.SubnetType.PUBLIC,
      },
      {
        name: "private",
        subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
      },
      {
        name: "isolated",
        subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
      },
    ],
  });

  const stack = new PgStacInfra(app, "TestPgStacInfra", {
    vpc,
    stage: "test",
    type: "internal",
    version: "1.0.0",
    webAclArn:
      "arn:aws:wafv2:us-east-1:123456789012:global/webacl/test-acl",
    loggingBucketArn: "arn:aws:s3:::test-logging-bucket",
    pgstacDbConfig: {
      instanceType: new ec2.InstanceType("t3.micro"),
      subnetPublic: false,
      allocatedStorage: 20,
      pgstacVersion: "0.9.5",
    },
    stacApiConfig: {
      customDomainName: "stac-api.example.com",
    },
    titilerPgstacConfig: {
      mosaicHost: "example.com/table-name",
      bucketsPath: "./titiler_buckets.yaml",
      dataAccessRoleArn: "arn:aws:iam::123456789012:role/test-role",
      customDomainName: "titiler.example.com",
    },
    ...overrides,
  });

  return Template.fromStack(stack);
}

describe("PgStacInfra STAC runtime wiring", () => {
  beforeAll(() => {
    jest
      .spyOn(lambda.Code, "fromDockerBuild")
      .mockImplementation(() => lambda.Code.fromAsset("test"));
  });

  afterAll(() => {
    jest.restoreAllMocks();
  });

  test("uses the custom STAC handler and keeps transactions disabled by default", () => {
    const template = buildTemplate({
      type: "public",
      stacApiConfig: {
        customDomainName: "public-stac.example.com",
        integrationApiArn:
          "arn:aws:execute-api:us-west-2:123456789012:api-id/stage/GET/",
      },
    });

    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "eoapi.stac.handler.handler",
      Environment: {
        Variables: Match.objectLike({
          STAC_FASTAPI_TITLE: "MAAP public STAC API (test)",
          STAC_FASTAPI_LANDING_ID: "maap-public-stac-api-test",
          ENABLED_EXTENSIONS:
            "query,sort,fields,filter,free_text,pagination,collection_search,catalogs",
          ENABLE_CATALOGS_EXTENSION: "true",
          HIDE_ALTERNATE_PARENTS: "false",
        }),
      },
    });

    expect(
      Object.keys(
        template.findResources("AWS::SecretsManager::Secret", {
          Properties: {
            Name:
              "/maap-eoapi/test/public/stac-collection-transaction-basic-auth",
          },
        }),
      ),
    ).toHaveLength(0);
    template.resourceCountIs("AWS::SSM::Parameter", 1);
  });

  test("enables collection transactions with a stack-managed secret by default", () => {
    const template = buildTemplate({
      stacApiConfig: {
        customDomainName: "internal-stac.example.com",
        transactions: {
          authMode: "basic",
        },
      },
    });

    template.hasResourceProperties("AWS::SecretsManager::Secret", {
      Description:
        "Basic auth secret for MAAP internal STAC collection transactions (test)",
      Name: "/maap-eoapi/test/internal/stac-collection-transaction-basic-auth",
      GenerateSecretString: Match.objectLike({
        GenerateStringKey: "password",
        SecretStringTemplate: '{"username":"maap-internal-stac-writer"}',
      }),
    });

    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "eoapi.stac.handler.handler",
      Environment: {
        Variables: Match.objectLike({
          ENABLED_EXTENSIONS:
            "query,sort,fields,filter,free_text,pagination,collection_search,catalogs,collection_transaction",
          MAAP_TRANSACTION_AUTH_MODE: "basic",
          MAAP_TRANSACTION_AUTH_SECRET_ARN: {
            Ref: Match.stringLikeRegexp(
              "staccollectiontransactionauthsecret",
            ),
          },
        }),
      },
    });

    template.hasResourceProperties("AWS::SSM::Parameter", {
      Name:
        "/maap-eoapi/test/internal/stac-collection-transaction-auth-secret-arn",
    });
  });

  test("enables catalog transactions with a stack-managed secret", () => {
    const template = buildTemplate({
      stacApiConfig: {
        customDomainName: "internal-stac.example.com",
        catalogs: {
          enabled: true,
          hideAlternateParents: true,
          transactions: {
            authMode: "basic",
          },
        },
      },
    });

    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "eoapi.stac.handler.handler",
      Environment: {
        Variables: Match.objectLike({
          ENABLED_EXTENSIONS:
            "query,sort,fields,filter,free_text,pagination,collection_search,catalogs,catalog_transaction",
          ENABLE_CATALOGS_EXTENSION: "true",
          HIDE_ALTERNATE_PARENTS: "true",
          MAAP_TRANSACTION_AUTH_MODE: "basic",
          MAAP_TRANSACTION_AUTH_SECRET_ARN: {
            Ref: Match.stringLikeRegexp(
              "staccollectiontransactionauthsecret",
            ),
          },
        }),
      },
    });
  });

  test("supports catalog transactions without collection transactions", () => {
    const template = buildTemplate({
      stacApiConfig: {
        customDomainName: "internal-stac.example.com",
        catalogs: {
          enabled: true,
          transactions: {
            authMode: "basic",
          },
        },
      },
    });

    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "eoapi.stac.handler.handler",
      Environment: {
        Variables: Match.objectLike({
          ENABLED_EXTENSIONS:
            "query,sort,fields,filter,free_text,pagination,collection_search,catalogs,catalog_transaction",
          MAAP_TRANSACTION_AUTH_MODE: "basic",
        }),
      },
    });
    template.hasResourceProperties("AWS::SSM::Parameter", {
      Name:
        "/maap-eoapi/test/internal/stac-collection-transaction-auth-secret-arn",
    });
  });

  test("rejects catalog transactions when catalogs are disabled", () => {
    expect(() =>
      buildTemplate({
        stacApiConfig: {
          customDomainName: "internal-stac.example.com",
          catalogs: {
            enabled: false,
            transactions: {
              authMode: "basic",
            },
          },
        },
      }),
    ).toThrow(/catalog transactions require catalogs/);
  });

  test("uses an explicit transaction auth secret ARN override when provided", () => {
    const template = buildTemplate({
      stacApiConfig: {
        customDomainName: "internal-stac.example.com",
        transactions: {
          authMode: "basic",
          authSecretArn:
            "arn:aws:secretsmanager:us-west-2:123456789012:secret:existing-auth-abcdef",
        },
      },
    });

    expect(
      Object.keys(
        template.findResources("AWS::SecretsManager::Secret", {
          Properties: {
            Name:
              "/maap-eoapi/test/internal/stac-collection-transaction-basic-auth",
          },
        }),
      ),
    ).toHaveLength(0);
    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "eoapi.stac.handler.handler",
      Environment: {
        Variables: Match.objectLike({
          MAAP_TRANSACTION_AUTH_SECRET_ARN:
            "arn:aws:secretsmanager:us-west-2:123456789012:secret:existing-auth-abcdef",
        }),
      },
    });
  });
});
