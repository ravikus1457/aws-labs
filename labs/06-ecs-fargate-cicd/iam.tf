# ---------------------------------------------------------------------------
# Two roles, two jobs:
#   execution role -> used by the ECS *agent* to pull the image and write logs.
#                     Hand-written instead of the AmazonECSTaskExecutionRolePolicy
#                     managed policy, which allows ecr:* reads on EVERY repo and
#                     logs on EVERY group.
#   task role      -> what the *application* gets. The app calls no AWS APIs,
#                     so the role exists with no permissions: the place to add a
#                     scoped policy later, and proof that the two are separate.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    # Confused-deputy guard: only tasks in THIS account may assume these roles.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-ecs-exec"
  description        = "ECS agent: pull the lab06 image, write to the lab06 log group"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = { Name = "${local.name}-ecs-exec" }
}

data "aws_iam_policy_document" "execution" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"] # not resource-scopable
    resources = ["*"]
  }
  statement {
    sid = "PullThisRepoOnly"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [data.aws_ecr_repository.app.arn]
  }
  statement {
    sid       = "WriteThisLogGroupOnly"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.app.arn}:*"]
  }
}

resource "aws_iam_role_policy" "execution" {
  name   = "${local.name}-ecs-exec"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution.json
}

resource "aws_iam_role" "task" {
  name               = "${local.name}-ecs-task"
  description        = "Application role for lab06 tasks (no permissions: the app calls no AWS APIs)"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = { Name = "${local.name}-ecs-task" }
}
