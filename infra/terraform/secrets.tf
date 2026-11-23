# ---------------------------------------------------------------------------
# Tự sinh toàn bộ secret, tạo K8s Secret `greenmart-secrets`, rồi chạy Job tạo 5 database + 5 user trên RDS.
# (RDS nằm trong subnet private nên Terraform không kết nối trực tiếp từ máy local được;
#  Job chạy trong cluster, cùng VPC, nên vào được.)
# ---------------------------------------------------------------------------

locals {
  # max_conn: ngân sách kết nối MySQL của từng service (MAX_USER_CONNECTIONS). Tổng 132 < max_connections 150,
  # chừa 18 cho tài khoản quản trị (rds-init, data-migrator, thao tác tay). Service vượt ngân sách chỉ tự nó bị lỗi
  # 1203, không làm cả RDS hết kết nối (sự cố ở bản 1.2.5). Tính theo số kết nối tối đa có thể có:
  #   order     4 pod x 2 worker x 4 thread = 32 (giữ kết nối 60s) + order-worker + Job migrate  -> 40
  #   user      6 pod x 2 x 2 thread = 24 + Job                                                  -> 30
  #   catalog   4 pod x 2 x 2 thread = 16 + order-worker gọi qua HTTP (không tính) + Job         -> 20
  #   promotion 3 pod x 2 x 2 thread = 12                                                         -> 12
  #   web       web + web-auth: từ 1.2.6 chỉ chạm MySQL khi tạo/đổi session (đăng nhập, khách mới) -> 30
  databases = {
    user      = { db = "user_db", user = "user_svc", env = "USER_DB_PASSWORD", max_conn = 30 }
    catalog   = { db = "catalog_db", user = "catalog_svc", env = "CATALOG_DB_PASSWORD", max_conn = 20 }
    order     = { db = "order_db", user = "order_svc", env = "ORDER_DB_PASSWORD", max_conn = 40 }
    promotion = { db = "promotion_db", user = "promotion_svc", env = "PROMOTION_DB_PASSWORD", max_conn = 12 }
    web       = { db = "web_db", user = "web_svc", env = "WEB_DB_PASSWORD", max_conn = 30 }
  }
}

# special = false: tránh ký tự làm hỏng SQL / shell.
resource "random_password" "db" {
  for_each = local.databases
  length   = 32
  special  = false
}

resource "random_password" "secret_key" {
  length  = 64
  special = false
}

resource "random_password" "internal_api_token" {
  length  = 48
  special = false
}

# Mật khẩu master do RDS tự sinh, lưu ở Secrets Manager (JSON: username, password).
data "aws_secretsmanager_secret_version" "rds_master" {
  secret_id = aws_db_instance.mysql.master_user_secret[0].secret_arn
}

locals {
  rds_master_password = jsondecode(data.aws_secretsmanager_secret_version.rds_master.secret_string)["password"]
}

resource "kubernetes_namespace_v1" "app" {
  metadata {
    name = var.k8s_namespace
    labels = {
      "app.kubernetes.io/part-of" = "greenmart"
    }
  }

  # kubectl apply -k sẽ thêm nhãn/annotation khác; không coi là drift.
  lifecycle {
    ignore_changes = [metadata[0].labels, metadata[0].annotations]
  }
}

resource "kubernetes_secret_v1" "app" {
  metadata {
    name      = "greenmart-secrets"
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels = {
      "app.kubernetes.io/name"    = "secrets"
      "app.kubernetes.io/part-of" = "greenmart"
    }
  }

  type = "Opaque"

  data = merge(
    {
      SECRET_KEY          = random_password.secret_key.result
      INTERNAL_API_TOKEN  = random_password.internal_api_token.result
      EMAIL_HOST_PASSWORD = var.email_host_password
      RDS_ADMIN_USER      = var.db_master_username
      RDS_ADMIN_PASSWORD  = local.rds_master_password
      MYSQL_ROOT_PASSWORD = local.rds_master_password # data-migrator dùng key này
    },
    { for k, v in local.databases : v.env => random_password.db[k].result }
  )
}

# Tạo 5 database + 5 tài khoản (idempotent, đặt lại mật khẩu theo Secret). Chạy trong cluster, xong mới tiếp tục.
resource "kubernetes_job_v1" "rds_init" {
  metadata {
    name      = "rds-init"
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels = {
      "app.kubernetes.io/name"    = "rds-init"
      "app.kubernetes.io/part-of" = "greenmart"
    }
  }

  spec {
    backoff_limit = 6

    template {
      metadata {
        labels = { "app.kubernetes.io/name" = "rds-init" }
      }

      spec {
        automount_service_account_token = false
        restart_policy                  = "OnFailure"

        container {
          name  = "mysql"
          image = "mysql:8.0"

          env {
            name  = "DB_HOST"
            value = aws_db_instance.mysql.address
          }
          env {
            name = "RDS_ADMIN_USER"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.app.metadata[0].name
                key  = "RDS_ADMIN_USER"
              }
            }
          }
          env {
            name = "MYSQL_PWD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.app.metadata[0].name
                key  = "RDS_ADMIN_PASSWORD"
              }
            }
          }

          dynamic "env" {
            for_each = local.databases
            content {
              name = env.value.env
              value_from {
                secret_key_ref {
                  name = kubernetes_secret_v1.app.metadata[0].name
                  key  = env.value.env
                }
              }
            }
          }

          command = ["sh", "-ec", <<-EOT
            mysql -h "$DB_HOST" -P 3306 -u "$RDS_ADMIN_USER" <<SQL
            %{for k, v in local.databases~}
            CREATE DATABASE IF NOT EXISTS ${v.db} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
            CREATE USER IF NOT EXISTS '${v.user}'@'%' IDENTIFIED WITH mysql_native_password BY '${"$"}${v.env}';
            ALTER USER '${v.user}'@'%' IDENTIFIED BY '${"$"}${v.env}' WITH MAX_USER_CONNECTIONS ${v.max_conn};
            GRANT ALL PRIVILEGES ON ${v.db}.* TO '${v.user}'@'%';
            %{endfor~}
            FLUSH PRIVILEGES;
            SQL
          EOT
          ]
        }
      }
    }
  }

  wait_for_completion = true

  timeouts {
    create = "10m"
    update = "10m"
  }

  depends_on = [
    aws_vpc_security_group_ingress_rule.rds_from_cluster,
    aws_vpc_security_group_ingress_rule.rds_from_vpc,
  ]
}
