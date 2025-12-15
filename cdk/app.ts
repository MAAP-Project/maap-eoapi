#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";

import { Vpc } from "./Vpc";
import { Config } from "./config";
import { PgStacInfra } from "./PgStacInfra";
import { MaapEoapiCommon } from "./MaapEoapiCommon";

const {
  buildStackName,
  certificateArn,
  dbAllocatedStorage,
  dbInstanceType,
  ingestorDataAccessRoleArn,
  ingestorDomainName,
  jwksUrl,
  mosaicHost,
  pgstacVersion,
  stacApiCustomDomainName,
  stacApiIntegrationApiArn,
  stacBrowserCertificateArn,
  stacBrowserCustomDomainName,
  stacBrowserRepoTag,
  stage,
  tags,
  titilerDataAccessRoleArn,
  titilerPgStacApiCustomDomainName,
  userStacAllowedPublisherAccountBucketPairs,
  userStacItemGenRoleArn,
  userStacStacApiCustomDomainName,
  userStacTitilerPgStacApiCustomDomainName,
  version,
  webAclArn,
} = new Config();

export const app = new cdk.App({});

const { vpc } = new Vpc(app, buildStackName("vpc"), {
  terminationProtection: false,
  tags,
  natGatewayCount: stage === "prod" ? undefined : 1,
});

// Create common resources to be shared by pgSTAC and userSTAC stacks
const common = new MaapEoapiCommon(app, buildStackName("common"), {
  tags,
  stage,
  terminationProtection: false,
});

new PgStacInfra(app, buildStackName("pgSTAC"), {
  vpc,
  tags,
  stage,
  type: "public",
  version,
  certificateArn,
  webAclArn,
  loggingBucketArn: common.loggingBucket.bucketArn,
  pgstacDbConfig: {
    instanceType: dbInstanceType,
    pgstacVersion: pgstacVersion,
    allocatedStorage: dbAllocatedStorage,
    subnetPublic: false,
  },
  stacApiConfig: {
    customDomainName: stacApiCustomDomainName,
    integrationApiArn: stacApiIntegrationApiArn,
  },
  titilerPgstacConfig: {
    mosaicHost,
    bucketsPath: "./titiler_buckets.yaml",
    customDomainName: titilerPgStacApiCustomDomainName,
    dataAccessRoleArn: titilerDataAccessRoleArn,
  },
  stacBrowserConfig: {
    repoTag: stacBrowserRepoTag,
    customDomainName: stacBrowserCustomDomainName,
    certificateArn: stacBrowserCertificateArn,
  },
  ingestorConfig: {
    jwksUrl,
    dataAccessRoleArn: ingestorDataAccessRoleArn,
    domainName: ingestorDomainName,
    userDataPath: "./userdata.yaml"
  },
  addStactoolsItemGenerator: true,
  terminationProtection: false,
});

new PgStacInfra(app, buildStackName("userSTAC"), {
  vpc,
  tags,
  stage,
  type: "internal",
  version,
  certificateArn,
  webAclArn,
  loggingBucketArn: common.loggingBucket.bucketArn,
  pgstacDbConfig: {
    instanceType: dbInstanceType,
    pgstacVersion: pgstacVersion,
    allocatedStorage: dbAllocatedStorage,
    subnetPublic: false,
  },
  stacApiConfig: {
    customDomainName: userStacStacApiCustomDomainName,
  },
  titilerPgstacConfig: {
    mosaicHost,
    bucketsPath: "./titiler_buckets.yaml",
    customDomainName: userStacTitilerPgStacApiCustomDomainName,
    dataAccessRoleArn: titilerDataAccessRoleArn,
  },
  // stacBrowserConfig: {
  //   repoTag: stacBrowserRepoTag,
  //   customDomainName: stacBrowserCustomDomainName,
  //   certificateArn: stacBrowserCertificateArn,
  // },
  addStactoolsItemGenerator: false,
  ...(userStacItemGenRoleArn && {
    dpsStacItemGenConfig: {
      itemGenRoleArn: userStacItemGenRoleArn,
      allowedAccountBucketPairs: userStacAllowedPublisherAccountBucketPairs,
    },
  }),
  terminationProtection: false,
});
