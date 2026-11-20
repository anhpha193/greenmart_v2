# ---------------------------------------------------------------------------
# ElastiCache (Valkey): session của web, cache sản phẩm/danh mục (catalog-service), khuyến mãi (promotion-service).
# 1 node nhỏ, không replica (Free plan, tiết kiệm credit). Ứng dụng được viết để cache lỗi thì đọc thẳng MySQL.
# Có TLS (rediss://) theo khuyến nghị của AWS. Chỉ mở cổng 6379 cho VPC của cluster.
# ---------------------------------------------------------------------------

resource "aws_elasticache_subnet_group" "valkey" {
  count      = var.enable_cache ? 1 : 0
  name       = "${var.name}-valkey"
  subnet_ids = local.db_subnet_ids
}

resource "aws_security_group" "valkey" {
  count       = var.enable_cache ? 1 : 0
  name_prefix = "${var.name}-valkey-"
  description = "Valkey cho GreenMart - chi cho phep tu VPC cua cluster EKS"
  vpc_id      = local.vpc_id

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "valkey_from_vpc" {
  count             = var.enable_cache ? 1 : 0
  security_group_id = aws_security_group.valkey[0].id
  cidr_ipv4         = data.aws_vpc.this.cidr_block
  ip_protocol       = "tcp"
  from_port         = 6379
  to_port           = 6379
  description       = "Valkey tu VPC (pod/node EKS)"
}

resource "aws_elasticache_replication_group" "valkey" {
  count                = var.enable_cache ? 1 : 0
  replication_group_id = "${var.name}-valkey"
  description          = "GreenMart session + cache"

  engine               = "valkey"
  engine_version       = var.cache_engine_version
  node_type            = var.cache_node_type
  num_cache_clusters   = 1
  parameter_group_name = var.cache_parameter_group
  port                 = 6379

  subnet_group_name  = aws_elasticache_subnet_group.valkey[0].name
  security_group_ids = [aws_security_group.valkey[0].id]

  automatic_failover_enabled = false
  multi_az_enabled           = false
  transit_encryption_enabled = true
  at_rest_encryption_enabled = true

  snapshot_retention_limit = 0 # cache, không cần backup
  apply_immediately        = true
}

locals {
  redis_url = var.enable_cache ? "rediss://${aws_elasticache_replication_group.valkey[0].primary_endpoint_address}:6379/0" : ""
}
