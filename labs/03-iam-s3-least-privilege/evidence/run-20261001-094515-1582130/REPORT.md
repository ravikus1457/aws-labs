# Evidence report — 03-iam-s3-least-privilege

- **Run ID:** `r20261001-094515-1582130`
- **Account:** `<account-id>`
- **Region:** `us-east-1`
- **Date (UTC):** 2026-10-01 16:48:41

## Terraform outputs
```json
{
  "bucket_arn": {
    "sensitive": false,
    "type": "string",
    "value": "arn:aws:s3:::awslabs-r20261001-094515-1582130-data"
  },
  "bucket_name": {
    "sensitive": false,
    "type": "string",
    "value": "awslabs-r20261001-094515-1582130-data"
  },
  "policy_arn": {
    "sensitive": false,
    "type": "string",
    "value": "arn:aws:iam::<account-id>:policy/awslabs-r20261001-094515-1582130-app-policy"
  },
  "role_arn": {
    "sensitive": false,
    "type": "string",
    "value": "arn:aws:iam::<account-id>:role/awslabs-r20261001-094515-1582130-app-role"
  }
}
```

## Scenario exercise log
```
Verifying least-privilege for role arn:aws:iam::<account-id>:role/awslabs-r20261001-094515-1582130-app-role against bucket awslabs-r20261001-094515-1582130-data
--- Simulating ALLOWED actions (s3:GetObject, s3:PutObject) ---
{
    "EvaluationResults": [
        {
            "EvalActionName": "s3:GetObject",
            "EvalResourceName": "arn:aws:s3:::awslabs-r20261001-094515-1582130-data/test.txt",
            "EvalDecision": "allowed",
            "MatchedStatements": [
                {
                    "SourcePolicyId": "awslabs-r20261001-094515-1582130-app-policy",
                    "SourcePolicyType": "IAM Policy",
                    "StartPosition": {
                        "Line": 1,
                        "Column": 15
                    },
                    "EndPosition": {
                        "Line": 1,
                        "Column": 165
                    }
                }
            ],
            "MissingContextValues": [],
            "EvalDecisionDetails": {},
            "ResourceSpecificResults": [
                {
                    "EvalResourceName": "arn:aws:s3:::awslabs-r20261001-094515-1582130-data/test.txt",
                    "EvalResourceDecision": "allowed",
                    "MatchedStatements": [
                        {
                            "SourcePolicyId": "awslabs-r20261001-094515-1582130-app-policy",
                            "SourcePolicyType": "IAM Policy",
                            "StartPosition": {
                                "Line": 1,
                                "Column": 15
                            },
                            "EndPosition": {
                                "Line": 1,
                                "Column": 165
                            }
                        }
                    ]
                }
            ]
        },
        {
            "EvalActionName": "s3:PutObject",
            "EvalResourceName": "arn:aws:s3:::awslabs-r20261001-094515-1582130-data/test.txt",
            "EvalDecision": "allowed",
            "MatchedStatements": [
                {
                    "SourcePolicyId": "awslabs-r20261001-094515-1582130-app-policy",
                    "SourcePolicyType": "IAM Policy",
                    "StartPosition": {
                        "Line": 1,
                        "Column": 15
                    },
                    "EndPosition": {
                        "Line": 1,
                        "Column": 165
                    }
                }
            ],
            "MissingContextValues": [],
            "EvalDecisionDetails": {},
            "ResourceSpecificResults": [
                {
                    "EvalResourceName": "arn:aws:s3:::awslabs-r20261001-094515-1582130-data/test.txt",
                    "EvalResourceDecision": "allowed",
                    "MatchedStatements": [
                        {
                            "SourcePolicyId": "awslabs-r20261001-094515-1582130-app-policy",
                            "SourcePolicyType": "IAM Policy",
                            "StartPosition": {
                                "Line": 1,
                                "Column": 15
                            },
                            "EndPosition": {
                                "Line": 1,
                                "Column": 165
                            }
                        }
                    ]
                }
            ]
        }
    ]
}
PASS: role is allowed to GetObject and PutObject in its bucket
--- Simulating DENIED actions (s3:DeleteBucket, ec2:RunInstances) ---
{
    "EvaluationResults": [
        {
            "EvalActionName": "s3:DeleteBucket",
            "EvalResourceName": "*",
            "EvalDecision": "implicitDeny",
            "MatchedStatements": [],
            "MissingContextValues": []
        },
        {
            "EvalActionName": "ec2:RunInstances",
            "EvalResourceName": "*",
            "EvalDecision": "implicitDeny",
            "MatchedStatements": [],
            "MissingContextValues": []
        }
    ]
}
PASS: least privilege enforced — DeleteBucket and RunInstances are implicitly denied
--- Public access block for awslabs-r20261001-094515-1582130-data ---
{
    "PublicAccessBlockConfiguration": {
        "BlockPublicAcls": true,
        "IgnorePublicAcls": true,
        "BlockPublicPolicy": true,
        "RestrictPublicBuckets": true
    }
}
PASS: bucket is private — all four public-access-block settings are true
--- Assertions complete (rc=0) ---
```
