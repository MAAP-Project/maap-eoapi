#!/bin/bash

# pulls output variables from other stacks and exports them to the github action environment as outputs

AUTH_STACK_NAME=$1
ROLES_STACK_NAME=$2

export JWKS_URL=$(aws cloudformation describe-stacks --stack-name $AUTH_STACK_NAME --query 'Stacks[0].Outputs[?OutputKey==`jwksurl`].OutputValue' --output text)
export INGESTOR_DATA_ACCESS_ROLE_ARN=$(aws cloudformation describe-stacks --stack-name "$ROLES_STACK_NAME" --query 'Stacks[0].Outputs[?ExportName==`data-access-role-arn`].OutputValue' --output text)
echo "JWKS_URL=$JWKS_URL" >> $GITHUB_OUTPUT
echo "INGESTOR_DATA_ACCESS_ROLE_ARN=$INGESTOR_DATA_ACCESS_ROLE_ARN" >> $GITHUB_OUTPUT
