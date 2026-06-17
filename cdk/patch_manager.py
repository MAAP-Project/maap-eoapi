from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk.aws_ssm import (
    CfnMaintenanceWindow,
    CfnMaintenanceWindowTarget,
    CfnMaintenanceWindowTask,
    StringParameter,
)
from constructs import Construct

# Aliases for long nested types
_TaskInvocationParams = CfnMaintenanceWindowTask.TaskInvocationParametersProperty
_RunCmdParams = CfnMaintenanceWindowTask.MaintenanceWindowRunCommandParametersProperty


class PatchManagerStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        pgbouncer_param_names: list[str],
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        # IAM role used by the maintenance window
        maintenance_role = iam.Role(
            self,
            "MaintenanceWindowRole",
            assumed_by=iam.ServicePrincipal("ssm.amazonaws.com"),
        )

        maintenance_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ssm:SendCommand",
                    "ssm:ListCommands",
                    "ssm:ListCommandInvocations",
                ],
                resources=["*"],
            )
        )

        # Maintenance Window
        maintenance_window = CfnMaintenanceWindow(
            self,
            "PatchMaintenanceWindow",
            name="patch-maintenance-window",
            description="Weekly patching using AWS default patch baseline",
            schedule="cron(0 7 ? * WED *)",  # Wednesdays 07:00 UTC
            duration=3,
            cutoff=1,
            allow_unassociated_targets=False,
        )

        instance_ids = [
            StringParameter.value_for_string_parameter(self, param_name)
            for param_name in pgbouncer_param_names
        ]

        # Target EC2 instances by instance ID
        target = CfnMaintenanceWindowTarget(
            self,
            "PatchTarget",
            window_id=maintenance_window.ref,
            resource_type="INSTANCE",
            targets=[
                CfnMaintenanceWindowTarget.TargetsProperty(
                    key="InstanceIds",
                    values=instance_ids,
                )
            ],
        )

        # Patch task (Install)
        CfnMaintenanceWindowTask(
            self,
            "PatchInstallTask",
            window_id=maintenance_window.ref,
            task_arn="AWS-RunPatchBaseline",
            task_type="RUN_COMMAND",
            priority=1,
            max_concurrency="2",
            max_errors="1",
            service_role_arn=maintenance_role.role_arn,
            targets=[
                CfnMaintenanceWindowTask.TargetProperty(
                    key="WindowTargetIds",
                    values=[target.ref],
                )
            ],
            task_invocation_parameters=_TaskInvocationParams(
                maintenance_window_run_command_parameters=_RunCmdParams(
                    parameters={"Operation": ["Install"]},
                )
            ),
        )
