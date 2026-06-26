# Lab 01 — VPC Networking (the foundation)

**What it builds:** a production-shaped Virtual Private Cloud — the private network
that almost every other AWS workload lives inside.

```
                    Internet
                       │
                ┌──────┴───────┐
                │ Internet GW  │
                └──────┬───────┘
        ┌──────────────┴───────────────┐
        │            VPC 10.20.0.0/16   │
        │  ┌─────────────┐  ┌─────────────┐
   AZ-a │  │ public  /24 │  │ private /24 │ AZ-a
        │  │  → IGW      │  │  → NAT      │
        │  └──────┬──────┘  └─────────────┘
        │     ┌───┴───┐
        │     │  NAT  │  (outbound-only for private subnets)
        │     └───────┘
   AZ-b │  ┌─────────────┐  ┌─────────────┐
        │  │ public  /24 │  │ private /24 │ AZ-b
        │  └─────────────┘  └─────────────┘
```

## Concepts demonstrated
- **VPC + CIDR planning** (`10.20.0.0/16`, split into /24 subnets with `cidrsubnet()`)
- **Public vs private subnets** across two Availability Zones (high availability)
- **Internet Gateway** — inbound/outbound internet for public subnets
- **NAT Gateway** — *outbound-only* internet for private subnets (so private hosts
  can pull updates but can't be reached from outside)
- **Route tables & associations** — the actual mechanism that makes a subnet
  "public" or "private"
- **Security groups** — stateful, least-privilege firewall (HTTP in, all out)
- **Default tags** for cost tracking and clean teardown

## Run it
```bash
scripts/run-lab.sh labs/01-vpc-networking            # apply → verify → destroy
scripts/run-lab.sh labs/01-vpc-networking --plan-only # see the plan, create nothing
```

## What the runner verifies (evidence)
`exercise.sh` calls the AWS API and **asserts**:
- public route table has a `0.0.0.0/0 → igw-…` route
- private route table has a `0.0.0.0/0 → nat-…` route

Evidence (VPC, subnets, route tables, pass/fail) lands in `evidence/01-vpc-networking-<run>/`.

## Cost
NAT Gateway is the only meaningful charge (~$0.045/hr + tiny data). A full
apply→destroy cycle runs in a few minutes, so a run costs **well under $0.01**.
Nothing is left running.

## Résumé bullet (defensible — make sure you can explain every word)
> Designed and automated a multi-AZ AWS VPC with public/private subnet tiers,
> Internet and NAT gateways, and least-privilege security groups using Terraform;
> built a runner that provisions, validates routing via the AWS API, and tears
> down the environment for repeatable, zero-idle-cost testing.

**Be ready to explain:** why private subnets use a NAT Gateway instead of an IGW,
what a route table association does, and why you'd want two AZs.
