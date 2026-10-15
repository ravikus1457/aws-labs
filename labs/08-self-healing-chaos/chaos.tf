# ---------------------------------------------------------------------------
# AWS Fault Injection Service: three experiment TEMPLATES. Templates are free and
# inert; nothing breaks until chaos.sh calls `fis start-experiment`. FIS bills
# $0.10 per action-minute while an experiment runs.
#
# Every template:
#   - targets instances by the lab08-role=web tag (and the FIS role's IAM policy
#     refuses anything else, so a template typo cannot widen the blast radius)
#   - stops itself if the canary alarm fires (customers are actually affected)
# ---------------------------------------------------------------------------

locals {
  fis_stop_condition = aws_cloudwatch_metric_alarm.canary.arn
  ssm_doc_arn        = "arn:${local.partition}:ssm:${var.aws_region}::document"
}

# 1. Kill one instance outright. Expected: the ALB stops routing to it within
#    ~10 s, the ASG launches a replacement, healthy targets return to baseline.
#    Remediation = ASG + ELB health checks (layer 3), no runbook involved.
resource "aws_fis_experiment_template" "terminate" {
  description = "Lab 08: terminate ONE running web instance; the ASG must restore the baseline"
  role_arn    = aws_iam_role.fis.arn

  stop_condition {
    source = "aws:cloudwatch:alarm"
    value  = local.fis_stop_condition
  }

  action {
    name      = "terminate-one-instance"
    action_id = "aws:ec2:terminate-instances"
    target {
      key   = "Instances"
      value = "one-web-instance"
    }
  }

  target {
    name           = "one-web-instance"
    resource_type  = "aws:ec2:instance"
    selection_mode = "COUNT(1)"
    resource_tag {
      key   = "lab08-role"
      value = "web"
    }
    filter {
      path   = "State.Name"
      values = ["running"]
    }
  }

  tags = { Name = "${local.name}-terminate-instance" }
}

# 2. Pin every web instance at 100 % CPU for cpu_stress_seconds. Expected: the
#    cpu-high alarm fires, EventBridge starts the runbook, the ASG grows by one;
#    when the stress ends the alarm clears and the runbook scales back in.
resource "aws_fis_experiment_template" "cpu_stress" {
  description = "Lab 08: 100% CPU on ALL web instances for ${var.cpu_stress_seconds}s; alarm -> EventBridge -> runbook must scale out, then back in"
  role_arn    = aws_iam_role.fis.arn

  stop_condition {
    source = "aws:cloudwatch:alarm"
    value  = local.fis_stop_condition
  }

  action {
    name      = "cpu-stress"
    action_id = "aws:ssm:send-command"
    parameter {
      key   = "documentArn"
      value = "${local.ssm_doc_arn}/AWSFIS-Run-CPU-Stress"
    }
    parameter {
      key = "documentParameters"
      value = jsonencode({
        DurationSeconds     = tostring(var.cpu_stress_seconds)
        InstallDependencies = "True"
      })
    }
    parameter {
      key   = "duration"
      value = "PT${ceil(var.cpu_stress_seconds / 60) + 1}M" # stress time + 1 min for the command to start/report
    }
    target {
      key   = "Instances"
      value = "all-web-instances"
    }
  }

  target {
    name           = "all-web-instances"
    resource_type  = "aws:ec2:instance"
    selection_mode = "ALL"
    resource_tag {
      key   = "lab08-role"
      value = "web"
    }
    filter {
      path   = "State.Name"
      values = ["running"]
    }
  }

  tags = { Name = "${local.name}-cpu-stress" }
}

# 3. (optional run) Add latency_ms to every packet leaving every web instance.
#    Expected: the latency alarm fires (time-to-detect is the measurement) and
#    clears when the fault is removed. No runbook: see observability.tf.
resource "aws_fis_experiment_template" "network_latency" {
  description = "Lab 08: +${var.latency_ms}ms egress latency on ALL web instances for ${var.latency_seconds}s; detect-only"
  role_arn    = aws_iam_role.fis.arn

  stop_condition {
    source = "aws:cloudwatch:alarm"
    value  = local.fis_stop_condition
  }

  action {
    name      = "network-latency"
    action_id = "aws:ssm:send-command"
    parameter {
      key   = "documentArn"
      value = "${local.ssm_doc_arn}/AWSFIS-Run-Network-Latency"
    }
    parameter {
      key = "documentParameters"
      value = jsonencode({
        DelayMilliseconds   = tostring(var.latency_ms)
        Interface           = var.network_interface
        DurationSeconds     = tostring(var.latency_seconds)
        InstallDependencies = "True"
      })
    }
    parameter {
      key   = "duration"
      value = "PT${ceil(var.latency_seconds / 60) + 1}M"
    }
    target {
      key   = "Instances"
      value = "all-web-instances"
    }
  }

  target {
    name           = "all-web-instances"
    resource_type  = "aws:ec2:instance"
    selection_mode = "ALL"
    resource_tag {
      key   = "lab08-role"
      value = "web"
    }
    filter {
      path   = "State.Name"
      values = ["running"]
    }
  }

  tags = { Name = "${local.name}-network-latency" }
}
