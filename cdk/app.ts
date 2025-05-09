#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";

import { Vpc } from "./Vpc";
import { Config } from "./config";
import { PgStacInfra } from "./PgStacInfra";

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
  version,
  webAclArn,
} = new Config();

export const app = new cdk.App({});

const { vpc } = new Vpc(app, buildStackName("vpc"), {
  terminationProtection: false,
  tags,
  natGatewayCount: stage === "prod" ? undefined : 1,
});

new PgStacInfra(app, buildStackName("pgSTAC"), {
  vpc,
  tags,
  stage,
  version,
  certificateArn,
  webAclArn,
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
    userDataPath: "./userdata.yaml",
    ipv4AllowList: [
      "66.17.119.38/32", // Jamison
      "131.215.220.32/32", // Aimee's home
      "104.9.124.28/32", // Sean
      "75.134.157.176/32", // Henry
    ],
    createElasticIp: stage === "prod",
  },
  terminationProtection: false,
});
