# ---------------------------------------------------------------------------
# Tầng edge: CloudFront đặt trước ALB (có trong AWS Free plan).
# Chạy SAU khi Ingress đã tạo ALB (workflow EKS 10), vì cần DNS của ALB làm origin.
#
# Cache theo hướng dẫn "CloudFront for e-commerce" của AWS:
#   /static/*  -> cache ở edge (gateway gửi Cache-Control 1 ngày)
#   /media/*   -> cache ở edge (ảnh sản phẩm trên S3, gateway gửi Cache-Control 30 ngày)
#   còn lại    -> KHÔNG cache: trang có CSRF token, tên user, giỏ hàng; chuyển nguyên header/cookie tới ALB
# Muốn cache cả trang sản phẩm phải tách phần riêng từng người (tên user, số món trong giỏ) ra API gọi bằng JS trước.
#
# Viewer dùng HTTPS với chứng chỉ mặc định *.cloudfront.net; CloudFront -> ALB dùng HTTP (ALB chưa có chứng chỉ).
# Sau khi tạo, ghi https://<domain> vào ConfigMap greenmart-infra (EXTRA_CSRF_TRUSTED_ORIGINS) để Django nhận form.
# ---------------------------------------------------------------------------

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_origin_request_policy" "all_viewer" {
  name = "Managed-AllViewer"
}

locals {
  origin_id = "alb"
}

resource "aws_cloudfront_distribution" "web" {
  enabled         = true
  comment         = "GreenMart - ${var.cluster_name}"
  is_ipv6_enabled = true
  price_class     = var.price_class
  http_version    = "http2and3"

  origin {
    origin_id   = local.origin_id
    domain_name = var.alb_dns_name

    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "http-only"
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = 30
      origin_keepalive_timeout = 5
    }

    connection_timeout  = 10
    connection_attempts = 3
  }

  # Trang động: không cache, chuyển mọi header (kể cả Host), cookie, query tới ALB.
  default_cache_behavior {
    target_origin_id         = local.origin_id
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer.id
    compress                 = true
  }

  ordered_cache_behavior {
    path_pattern           = "/static/*"
    target_origin_id       = local.origin_id
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.optimized.id
    compress               = true
  }

  ordered_cache_behavior {
    path_pattern           = "/media/*"
    target_origin_id       = local.origin_id
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.optimized.id
    compress               = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

# ---------- Báo cho Django biết địa chỉ CloudFront (CSRF) ----------
resource "kubernetes_config_map_v1_data" "edge" {
  metadata {
    name      = "greenmart-infra"
    namespace = var.k8s_namespace
  }
  field_manager = "terraform-edge"
  force         = true

  data = {
    PUBLIC_EDGE_URL            = "https://${aws_cloudfront_distribution.web.domain_name}"
    EXTRA_CSRF_TRUSTED_ORIGINS = "https://${aws_cloudfront_distribution.web.domain_name}"
  }
}
