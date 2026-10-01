# Evidence report — 01-vpc-networking

- **Run ID:** `r20261001-093811-1571414`
- **Account:** `<account-id>`
- **Region:** `us-east-1`
- **Date (UTC):** 2026-10-01 16:40:35

## Terraform outputs
```json
{
  "nat_gateway_id": {
    "sensitive": false,
    "type": "string",
    "value": "nat-0480f61cd633909d7"
  },
  "private_subnet_ids": {
    "sensitive": false,
    "type": [
      "tuple",
      [
        "string",
        "string"
      ]
    ],
    "value": [
      "subnet-038bfda35d7fc29f1",
      "subnet-009864986caa4ca8b"
    ]
  },
  "public_subnet_ids": {
    "sensitive": false,
    "type": [
      "tuple",
      [
        "string",
        "string"
      ]
    ],
    "value": [
      "subnet-0245cd85ba9cf3096",
      "subnet-006f9f63004f6a900"
    ]
  },
  "vpc_id": {
    "sensitive": false,
    "type": "string",
    "value": "vpc-0f98bc7a46b389422"
  },
  "web_security_group_id": {
    "sensitive": false,
    "type": "string",
    "value": "sg-0e6e9b1af1fe18fa0"
  }
}
```

## Scenario exercise log
```
Verifying VPC topology for vpc-0f98bc7a46b389422 in us-east-1
--- VPC ---
-----------------------------------------------------------
|                      DescribeVpcs                       |
+--------------+----------------+-------------------------+
|     Cidr     | DnsHostnames   |          VpcId          |
+--------------+----------------+-------------------------+
|  10.20.0.0/16|  None          |  vpc-0f98bc7a46b389422  |
+--------------+----------------+-------------------------+
--- Subnets (public vs private) ---
------------------------------------------------------------------------
|                            DescribeSubnets                           |
+------------+----------------+----------------------------+-----------+
|     AZ     |     Cidr       |          Subnet            |   Tier    |
+------------+----------------+----------------------------+-----------+
|  us-east-1a|  10.20.0.0/24  |  subnet-0245cd85ba9cf3096  |  public   |
|  us-east-1a|  10.20.10.0/24 |  subnet-038bfda35d7fc29f1  |  private  |
|  us-east-1b|  10.20.11.0/24 |  subnet-009864986caa4ca8b  |  private  |
|  us-east-1b|  10.20.1.0/24  |  subnet-006f9f63004f6a900  |  public   |
+------------+----------------+----------------------------+-----------+
--- Route tables (proves public->IGW, private->NAT) ---
[
    {
        "RT": "rtb-0777523f0ea8dc2f1",
        "Routes": [
            {
                "Dest": "10.20.0.0/16",
                "GW": "local",
                "NAT": null
            },
            {
                "Dest": "0.0.0.0/0",
                "GW": "igw-0f838f9b5deb740b9",
                "NAT": null
            }
        ]
    },
    {
        "RT": "rtb-0db46850d71ee6c93",
        "Routes": [
            {
                "Dest": "10.20.0.0/16",
                "GW": "local",
                "NAT": null
            }
        ]
    },
    {
        "RT": "rtb-0a44ffd733ead6c84",
        "Routes": [
            {
                "Dest": "10.20.0.0/16",
                "GW": "local",
                "NAT": null
            },
            {
                "Dest": "0.0.0.0/0",
                "GW": null,
                "NAT": "nat-0480f61cd633909d7"
            }
        ]
    }
]
--- Assertions ---
PASS: public route to Internet Gateway present
PASS: private route to NAT Gateway present
```
