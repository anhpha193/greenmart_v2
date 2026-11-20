"""Gửi sự kiện "đơn hoàn tất" vào Amazon SQS (bật khi có ORDER_EVENTS_QUEUE_URL).

order-worker (lệnh consume_order_events) nhận sự kiện và gọi catalog-service trừ tồn kho. Việc này chạy sau,
không nằm trong request đặt hàng, nên lúc nhiều người đặt cùng lúc MySQL của catalog không bị dội.
Quyền gửi lấy từ role IRSA của ServiceAccount order-service.
"""
import json
import logging

from greenmart_common.env import env

log = logging.getLogger(__name__)
_sqs = None


def queue_url():
    return env("ORDER_EVENTS_QUEUE_URL", "")


def sqs():
    global _sqs
    if _sqs is None:
        import boto3

        _sqs = boto3.client("sqs", region_name=env("AWS_REGION", "ap-southeast-1"))
    return _sqs


def order_completed_message(order):
    return {
        "type": "order_completed",
        "order_id": order.id,
        "items": [{"product_id": i.product_id, "quantity": i.quantity}
                  for i in order.items.all() if i.product_id and i.quantity and i.quantity > 0],
    }


def publish_order_completed(order):
    """Không làm hỏng việc đặt hàng nếu SQS lỗi: đơn đã ghi xong, chỉ ghi log để xử lý tay."""
    url = queue_url()
    if not url:
        return
    body = order_completed_message(order)
    try:
        sqs().send_message(QueueUrl=url, MessageBody=json.dumps(body))
    except Exception:  # noqa: BLE001
        log.exception("Không gửi được sự kiện order_completed cho đơn %s; tồn kho chưa được trừ", order.id)
