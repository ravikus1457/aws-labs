# ---------------------------------------------------------------------------
# Five roles, each with one job. All named awslabs-lab08-* (the deploy role's
# bootstrap policy can only create/pass roles with that prefix).
#
#   web          EC2 instances: SSM agent only (fault injection + shell, no SSH)
#   canary       Synthetics canary (a Lambda): artifacts to S3, logs, metrics
#   fis          FIS experiments: terminate / send-command to lab 08 instances only
#   remediation  SSM Automation runbook: change the lab 08 ASG's desired capacity
#   events       EventBridge: start that one runbook, pass that one role
# ---------------------------------------------------------------------------

# --- web instances -----------------------------------------------------------
data "aws_iam_policy_document" "ec2_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "web" {
  name               = "${local.name}-web"
  description        = "Lab 08 web instances: SSM agent (FIS send-command faults, Session Manager instead of SSH)"
  assume_role_policy = data.aws_iam_policy_document.ec2_trust.json
}

resource "aws_iam_role_policy_attachment" "web_ssm" {
  role       = aws_iam_role.web.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "web" {
  name = "${local.name}-web"
  role = aws_iam_role.web.name
}

# --- Synthetics canary -------------------------------------------------------
data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "canary" {
  name               = "${local.name}-canary"
  description        = "Lab 08 Synthetics canary: write run artifacts, logs and CloudWatchSynthetics metrics"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

# The permissions AWS documents for a canary execution role, scoped to this lab.
data "aws_iam_policy_document" "canary" {
  statement {
    sid       = "Artifacts"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.canary.arn}/*"]
  }
  statement {
    sid       = "ArtifactBucket"
    actions   = ["s3:GetBucketLocation"]
    resources = [aws_s3_bucket.canary.arn]
  }
  statement {
    sid       = "ListBucketsRequiredBySynthetics"
    actions   = ["s3:ListAllMyBuckets"]
    resources = ["*"]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:${local.partition}:logs:${var.aws_region}:${local.account_id}:log-group:/aws/lambda/cwsyn-*"]
  }
  statement {
    sid       = "SyntheticsMetricsOnly"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["CloudWatchSynthetics"]
    }
  }
}

resource "aws_iam_role_policy" "canary" {
  name   = "${local.name}-canary"
  role   = aws_iam_role.canary.id
  policy = data.aws_iam_policy_document.canary.json
}

# --- FIS ---------------------------------------------------------------------
data "aws_iam_policy_document" "fis_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["fis.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "fis" {
  name               = "${local.name}-fis"
  description        = "Lab 08 FIS experiments: may only hurt instances tagged lab08-role=web"
  assume_role_policy = data.aws_iam_policy_document.fis_trust.json
}

# Least privilege for chaos: the blast radius is the IAM policy, not the template.
# Even a mistyped target filter cannot terminate or stress anything not tagged lab08-role=web.
data "aws_iam_policy_document" "fis" {
  statement {
    sid       = "DescribeToResolveTargets"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }
  statement {
    sid       = "TerminateLab08WebOnly"
    actions   = ["ec2:TerminateInstances"]
    resources = ["arn:${local.partition}:ec2:${var.aws_region}:${local.account_id}:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/lab08-role"
      values   = ["web"]
    }
  }
  statement {
    sid     = "SendFisDocuments"
    actions = ["ssm:SendCommand"]
    resources = [
      "arn:${local.partition}:ssm:${var.aws_region}::document/AWSFIS-Run-CPU-Stress",
      "arn:${local.partition}:ssm:${var.aws_region}::document/AWSFIS-Run-Network-Latency",
    ]
  }
  statement {
    sid       = "SendToLab08WebOnly"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:${local.partition}:ec2:${var.aws_region}:${local.account_id}:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "ssm:resourceTag/lab08-role"
      values   = ["web"]
    }
  }
  statement {
    sid       = "TrackAndCancelCommands"
    actions   = ["ssm:ListCommands", "ssm:ListCommandInvocations", "ssm:CancelCommand"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "fis" {
  name   = "${local.name}-fis"
  role   = aws_iam_role.fis.id
  policy = data.aws_iam_policy_document.fis.json
}

# --- remediation runbook (SSM Automation) ------------------------------------
data "aws_iam_policy_document" "ssm_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ssm.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "remediation" {
  name               = "${local.name}-remediation"
  description        = "Lab 08 SSM Automation runbook: set the desired capacity of the lab 08 ASG, nothing else"
  assume_role_policy = data.aws_iam_policy_document.ssm_trust.json
}

data "aws_iam_policy_document" "remediation" {
  statement {
    sid       = "ReadAsg"
    actions   = ["autoscaling:DescribeAutoScalingGroups"]
    resources = ["*"]
  }
  statement {
    sid       = "ScaleThisAsgOnly"
    actions   = ["autoscaling:SetDesiredCapacity"]
    resources = [aws_autoscaling_group.web.arn]
  }
}

resource "aws_iam_role_policy" "remediation" {
  name   = "${local.name}-remediation"
  role   = aws_iam_role.remediation.id
  policy = data.aws_iam_policy_document.remediation.json
}

# --- EventBridge -> SSM Automation ------------------------------------------
data "aws_iam_policy_document" "events_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "events" {
  name               = "${local.name}-events"
  description        = "Lab 08 EventBridge rules: start the scale runbook with the remediation role"
  assume_role_policy = data.aws_iam_policy_document.events_trust.json
}

data "aws_iam_policy_document" "events" {
  statement {
    sid     = "StartTheRunbook"
    actions = ["ssm:StartAutomationExecution"]
    resources = [
      "arn:${local.partition}:ssm:${var.aws_region}:${local.account_id}:automation-definition/${aws_ssm_document.scale.name}:*",
      # 10-15 runs 3+4 (from the DLQ): SSM authorizes StartAutomationExecution against the DOCUMENT arn.
      # Built from the name, not the provider's arn attribute (run 4 still denied with that).
      "arn:${local.partition}:ssm:${var.aws_region}:${local.account_id}:document/${aws_ssm_document.scale.name}",
    ]
  }
  statement {
    sid       = "PassTheRemediationRoleToSsm"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.remediation.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ssm.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "events" {
  name   = "${local.name}-events"
  role   = aws_iam_role.events.id
  policy = data.aws_iam_policy_document.events.json
}
