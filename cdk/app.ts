#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";

import { Vpc } from "./Vpc";
import { Config } from "./config";
import { PgStacInfra } from "./PgStacInfra";
const {
  stage,
  version,
  dbInstanceType,
  buildStackName,
  tags,
  jwksUrl,
  titilerDataAccessRoleArn,
  ingestorDataAccessRoleArn,
  stacApiIntegrationApiArn,
  dbAllocatedStorage,
  mosaicHost,
  certificateArn,
  ingestorDomainName,
  stacApiCustomDomainName,
  titilerPgStacApiCustomDomainName,
  stacBrowserRepoTag,
  stacBrowserCustomDomainName,
  stacBrowserCertificateArn,
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
  jwksUrl,
  dbInstanceType,
  terminationProtection: false,
  bastionIpv4AllowList: [
    "66.17.119.38/32", // Jamison
    "131.215.220.32/32", // Aimee's home
    "104.9.124.28/32", // Sean
    "75.134.157.176/32", // Henry
  ],
  bastionUserDataPath: "./userdata.yaml",
  bastionHostCreateElasticIp: stage === "prod",
  titilerDataAccessRoleArn: titilerDataAccessRoleArn,
  ingestorDataAccessRoleArn: ingestorDataAccessRoleArn,
  stacApiIntegrationApiArn: stacApiIntegrationApiArn,
  allocatedStorage: dbAllocatedStorage,
  mosaicHost: mosaicHost,
  titilerBucketsPath: "./titiler_buckets.yaml",
  certificateArn: certificateArn,
  IngestorDomainName: ingestorDomainName,
  stacApiCustomDomainName: stacApiCustomDomainName,
  titilerPgStacApiCustomDomainName: titilerPgStacApiCustomDomainName,
  stacBrowserRepoTag: stacBrowserRepoTag,
  stacBrowserCustomDomainName: stacBrowserCustomDomainName,
  stacBrowserCertificateArn: stacBrowserCertificateArn,
});
