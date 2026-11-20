# ---------------------------------------------------------------------------
# Amazon SQS: hàng đợi sự kiện "đơn hoàn tất". order-service gửi, order-worker nhận và trừ tồn kho ở catalog-service.
# Tin lỗi 5 lần chuyển sang hàng đợi lỗi (DLQ) để xem lại, không bị mất. Free plan: 1 triệu request/tháng miễn phí.
# ---------------------------------------------------------------------------

resource "aws_sqs_queue" "order_events_dlq" {
  name                      = "${var.name}-order-events-dlq"
  message_retention_seconds = 1209600 # 14 ngày
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "order_events" {
  name                       = "${var.name}-order-events"
  visibility_timeout_seconds = 60     # khớp VisibilityTimeout của order-worker
  message_retention_seconds  = 345600 # 4 ngày
  receive_wait_time_seconds  = 20     # long polling: ít request rỗng, đỡ tốn hạn mức
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.order_events_dlq.arn
    maxReceiveCount     = 5
  })
}
