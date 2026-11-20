# ---------------------------------------------------------------------------
# Quyền AWS cho pod (IRSA) + cấu hình hạ tầng cho ứng dụng.
#   ServiceAccount catalog-service  -> ghi/xóa ảnh trong bucket media
#   ServiceAccount order-service    -> gửi tin vào hàng đợi order-events
#   ServiceAccount order-worker     -> nhận/xóa tin trong hàng đợi order-events
#   ConfigMap greenmart-infra       -> REDIS_URL, MEDIA_BUCKET, ORDER_EVENTS_QUEUE_URL, AWS_REGION
# Overlay k8s/overlays/eks dùng các tên trên; vì vậy chạy Terraform trước khi deploy.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "media_rw" {
  statement {
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.media.arn}/media/*"]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.media.arn]
  }
}

data "aws_iam_policy_document" "order_events_send" {
  statement {
    actions   = ["sqs:SendMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.order_events.arn]
  }
}

data "aws_iam_policy_document" "order_events_consume" {
  statement {
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.order_events.arn]
  }
}

locals {
  app_irsa = {
    catalog-service = data.aws_iam_policy_document.media_rw.json
    order-service   = data.aws_iam_policy_document.order_events_send.json
    order-worker    = data.aws_iam_policy_document.order_events_consume.json
  }
}

data "aws_iam_policy_document" "app_assume" {
  for_each = local.app_irsa

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.eks.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = ["system:serviceaccount:${var.k8s_namespace}:${each.key}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app" {
  for_each           = local.app_irsa
  name               = "${var.name}-${var.cluster_name}-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.app_assume[each.key].json
}

resource "aws_iam_role_policy" "app" {
  for_each = local.app_irsa
  name     = "app"
  role     = aws_iam_role.app[each.key].id
  policy   = each.value
}

resource "kubernetes_service_account_v1" "app" {
  for_each = local.app_irsa

  metadata {
    name      = each.key
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels = {
      "app.kubernetes.io/name"    = each.key
      "app.kubernetes.io/part-of" = "greenmart"
    }
    annotations = {
      "eks.amazonaws.com/role-arn" = aws_iam_role.app[each.key].arn
    }
  }

  automount_service_account_token = false
}

# ConfigMap trống do Terraform sở hữu; dữ liệu ghi bằng kubernetes_config_map_v1_data để stack edge
# (infra/terraform-edge) thêm được key EXTRA_CSRF_TRUSTED_ORIGINS mà không bị stack này xóa.
resource "kubernetes_config_map_v1" "infra" {
  metadata {
    name      = "greenmart-infra"
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels = {
      "app.kubernetes.io/name"    = "infra"
      "app.kubernetes.io/part-of" = "greenmart"
    }
  }

  lifecycle {
    ignore_changes = [data]
  }
}

resource "kubernetes_config_map_v1_data" "infra" {
  metadata {
    name      = kubernetes_config_map_v1.infra.metadata[0].name
    namespace = kubernetes_config_map_v1.infra.metadata[0].namespace
  }
  field_manager = "terraform-core"
  force         = true

  data = {
    AWS_REGION             = var.region
    REDIS_URL              = local.redis_url
    MEDIA_BUCKET           = aws_s3_bucket.media.bucket
    ORDER_EVENTS_QUEUE_URL = aws_sqs_queue.order_events.url
  }
}
