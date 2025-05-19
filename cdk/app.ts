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
  bastionHostIpv4AllowList,
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
    webAclArn,
  },
  ingestorConfig: {
    jwksUrl,
    dataAccessRoleArn: ingestorDataAccessRoleArn,
    domainName: ingestorDomainName,
    userDataPath: "./userdata.yaml",
    ipv4AllowList: bastionHostIpv4AllowList,
    createElasticIp: stage === "prod",
  },
  terminationProtection: false,
});
