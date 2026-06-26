#!/usr/bin/env bash
# Exercise + evidence capture for Lab 01 (VPC networking).
# Receives the evidence dir as $1. Reads terraform outputs from $OUTPUTS_JSON.
set -euo pipefail
EVID="${1:?evidence dir required}"
VPC_ID="$(jq -r '.vpc_id.value' "$OUTPUTS_JSON")"
REGION="${AWS_REGION:?}"

echo "Verifying VPC topology for $VPC_ID in $REGION"

echo "--- VPC ---"
aws ec2 describe-vpcs --vpc-ids "$VPC_ID" --region "$REGION" \
  --query 'Vpcs[0].{VpcId:VpcId,Cidr:CidrBlock,DnsHostnames:EnableDnsHostnames}' --output table \
  | tee "$EVID/vpc.txt"

echo "--- Subnets (public vs private) ---"
aws ec2 describe-subnets --region "$REGION" \
  --filters "Name=vpc-id,Values=$VPC_ID" \
  --query 'Subnets[].{Subnet:SubnetId,AZ:AvailabilityZone,Cidr:CidrBlock,Tier:Tags[?Key==`tier`]|[0].Value}' \
  --output table | tee "$EVID/subnets.txt"

echo "--- Route tables (proves public->IGW, private->NAT) ---"
aws ec2 describe-route-tables --region "$REGION" \
  --filters "Name=vpc-id,Values=$VPC_ID" \
  --query 'RouteTables[].{RT:RouteTableId,Routes:Routes[].{Dest:DestinationCidrBlock,GW:GatewayId,NAT:NatGatewayId}}' \
  --output json | tee "$EVID/route-tables.json"

# Assertions: the network must actually be wired correctly.
echo "--- Assertions ---"
rc=0
pub_route="$(jq -r '[.RouteTables[].Routes[]? | select(.GatewayId != null and (.GatewayId|startswith("igw-")))] | length' "$EVID/route-tables.json" 2>/dev/null || echo 0)"
nat_route="$(jq -r '[.RouteTables[].Routes[]? | select(.NatGatewayId != null)] | length' "$EVID/route-tables.json" 2>/dev/null || echo 0)"
# describe again in a flat shape for reliable assertions
aws ec2 describe-route-tables --region "$REGION" --filters "Name=vpc-id,Values=$VPC_ID" \
  --query 'RouteTables[].Routes[]' --output json > "$EVID/_routes_flat.json"
igw_ok=$(jq '[.[] | select((.GatewayId//"")|startswith("igw-"))] | length' "$EVID/_routes_flat.json")
nat_ok=$(jq '[.[] | select(.NatGatewayId != null)] | length' "$EVID/_routes_flat.json")

if [ "$igw_ok" -ge 1 ]; then echo "PASS: public route to Internet Gateway present"; else echo "FAIL: no IGW route"; rc=1; fi
if [ "$nat_ok" -ge 1 ]; then echo "PASS: private route to NAT Gateway present"; else echo "FAIL: no NAT route"; rc=1; fi

exit "$rc"
