# ---------------------------------------------------------------------------
# Giám sát: chỉ Grafana (Prometheus + Grafana do Argo CD cài, gitops/apps/monitoring-stack.yaml.tpl).
# Không dùng CloudWatch Container Insights, không dùng X-Ray: số liệu pod/node/request lấy từ Prometheus.
# Grafana có thêm datasource CloudWatch chỉ để đọc metric mặc định của dịch vụ AWS mà Prometheus không thấy
# (ALB, RDS, ElastiCache, SQS, CloudFront). Đây là metric AWS tự gửi, không mất phí lưu; chỉ tính phí API đọc rất nhỏ.
# Role IRSA bên dưới chỉ có quyền ĐỌC CloudWatch.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "grafana_assume" {
  count = var.enable_monitoring ? 1 : 0

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.eks.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = ["system:serviceaccount:monitoring:grafana"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "grafana" {
  count              = var.enable_monitoring ? 1 : 0
  name               = "${var.name}-${var.cluster_name}-grafana"
  assume_role_policy = data.aws_iam_policy_document.grafana_assume[0].json
}

resource "aws_iam_role_policy_attachment" "grafana_cloudwatch_read" {
  count      = var.enable_monitoring ? 1 : 0
  role       = aws_iam_role.grafana[0].name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchReadOnlyAccess"
}
