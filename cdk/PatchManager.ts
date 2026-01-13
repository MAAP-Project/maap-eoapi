import { Stack, StackProps } from 'aws-cdk-lib';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export class PatchManagerStack extends Stack {
  constructor(scope: Construct, id: string, props?: Props) {
    super(scope, id, props);

    // IAM role used by the maintenance window
    const maintenanceRole = new iam.Role(this, 'MaintenanceWindowRole', {
      assumedBy: new iam.ServicePrincipal('ssm.amazonaws.com'),
    });

    maintenanceRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          'ssm:SendCommand',
          'ssm:ListCommands',
          'ssm:ListCommandInvocations',
        ],
        resources: ['*'],
      }),
    );

    // Maintenance Window
    const maintenanceWindow = new ssm.CfnMaintenanceWindow(
      this,
      'PatchMaintenanceWindow',
      {
        name: 'patch-maintenance-window',
        description: 'Weekly patching using AWS default patch baseline',
        schedule: 'cron(0 3 ? * SUN *)', // Sundays 03:00 UTC
        duration: 3,
        cutoff: 1,
        allowUnassociatedTargets: false,
      },
    );

    // Target EC2 instances by Name tag
    const target = new ssm.CfnMaintenanceWindowTarget(
      this,
      'PatchTarget',
      {
        windowId: maintenanceWindow.ref,
        resourceType: 'INSTANCE',
        targets: [
          {
            key: 'tag:Name',
            values: [
              `MAAP-STAC-${props?.stage}-pgSTAC-pgbouncer`,
              `MAAP-STAC-${props?.stage}-userSTAC-pgbouncer`,
            ],
          },
        ],
      },
    );

    // Patch task (Install)
    new ssm.CfnMaintenanceWindowTask(this, 'PatchInstallTask', {
      windowId: maintenanceWindow.ref,
      taskArn: 'AWS-RunPatchBaseline',
      taskType: 'RUN_COMMAND',
      priority: 1,
      maxConcurrency: '2',
      maxErrors: '1',
      serviceRoleArn: maintenanceRole.roleArn,
      targets: [
        {
          key: 'WindowTargetIds',
          values: [target.ref],
        },
      ],
      taskInvocationParameters: {
        maintenanceWindowRunCommandParameters: {
          parameters: {
            Operation: ['Install'],
          },
        },
      },
    });
  }
}

export interface Props extends StackProps {
  /**
   * Stage of this stack. Used for naming resources.
   */
  stage: string;
}
