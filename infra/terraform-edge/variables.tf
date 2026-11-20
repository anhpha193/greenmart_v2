variable "region" {
  type    = string
  default = "ap-southeast-1"
}

variable "cluster_name" {
  type = string
}

variable "name" {
  type    = string
  default = "greenmart"
}

variable "k8s_namespace" {
  type    = string
  default = "greenmart"
}

variable "alb_dns_name" {
  description = "DNS của ALB do Ingress greenmart-alb tạo (kubectl -n greenmart get ingress greenmart-alb)."
  type        = string
}

variable "price_class" {
  description = "PriceClass_200 có edge ở châu Á (gồm Việt Nam/Singapore), rẻ hơn PriceClass_All."
  type        = string
  default     = "PriceClass_200"
}
