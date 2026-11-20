# ---------------------------------------------------------------------------
# Ảnh sản phẩm trên S3 (thay ổ EBS ReadWriteOnce, để catalog-service chạy nhiều pod).
#   - Bucket private, chặn public access, mã hóa SSE-S3.
#   - VPC gateway endpoint cho S3 (miễn phí): traffic từ pod tới S3 không qua NAT.
#   - Bucket policy: cho ĐỌC object khi request đi qua đúng endpoint này (gateway nginx chuyển /media/ tới S3,
#     không cần ký). Chỉ catalog-service (role IRSA ở app-irsa.tf) được ghi/xóa.
#   - Key: media/<đường dẫn> (catalog/storage.py). Ảnh mẫu được đưa lên ở workflow EKS 6.
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

data "aws_route_tables" "vpc" {
  vpc_id = local.vpc_id
}

locals {
  media_bucket = "${var.name}-media-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket" "media" {
  bucket        = local.media_bucket
  force_destroy = var.media_force_destroy # môi trường thử: terraform destroy xóa luôn ảnh
}

resource "aws_s3_bucket_public_access_block" "media" {
  bucket                  = aws_s3_bucket.media.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "media" {
  bucket = aws_s3_bucket.media.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "media" {
  bucket = aws_s3_bucket.media.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_vpc_endpoint" "s3" {
  count             = var.create_s3_vpc_endpoint ? 1 : 0
  vpc_id            = local.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = data.aws_route_tables.vpc.ids

  tags = { Name = "${var.name}-s3" }
}

locals {
  s3_vpc_endpoint_id = var.create_s3_vpc_endpoint ? aws_vpc_endpoint.s3[0].id : var.s3_vpc_endpoint_id
}

data "aws_iam_policy_document" "media_bucket" {
  # Có điều kiện aws:SourceVpce nên S3 không coi là policy public (Block Public Access vẫn bật).
  statement {
    sid       = "ReadFromVpcEndpoint"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.media.arn}/media/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceVpce"
      values   = [local.s3_vpc_endpoint_id]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.media.arn, "${aws_s3_bucket.media.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "media" {
  bucket     = aws_s3_bucket.media.id
  policy     = data.aws_iam_policy_document.media_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.media]
}
