#!/bin/bash

# pulls output variables from other stacks and exports them to the github action environment as outputs

AUTH_STACK_NAME=$1

export JWKS_URL=$(aws cloudformation describe-stacks --stack-name $AUTH_STACK_NAME --query 'Stacks[0].Outputs[?OutputKey==`jwksurl`].OutputValue' --output text)
echo "JWKS_URL=$JWKS_URL" >> $GITHUB_OUTPUT
