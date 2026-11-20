"""order-worker: nhận sự kiện từ SQS và trừ tồn kho ở catalog-service.

    python manage.py consume_order_events

SQS giao mỗi tin ít nhất một lần; catalog-service bỏ qua đơn đã trừ (khóa duy nhất theo order_id).
Tin chỉ bị xóa khi xử lý xong. Lỗi thì tin hiện lại sau VisibilityTimeout; sau 5 lần chuyển sang hàng đợi lỗi (DLQ).
"""
import json
import logging
import signal

from django.core.management.base import BaseCommand, CommandError

from greenmart_common.client import ServiceError

from orders.clients import catalog
from orders.events import queue_url, sqs

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Nhận sự kiện order_completed từ SQS và trừ tồn kho"

    def handle(self, *args, **options):
        url = queue_url()
        if not url:
            raise CommandError("Thiếu ORDER_EVENTS_QUEUE_URL")
        self.running = True
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        log.info("order-worker bắt đầu nhận tin từ %s", url)

        while self.running:
            resp = sqs().receive_message(QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=20,
                                         VisibilityTimeout=60)
            for msg in resp.get("Messages", []):
                if self._process(msg["Body"]):
                    sqs().delete_message(QueueUrl=url, ReceiptHandle=msg["ReceiptHandle"])
        log.info("order-worker dừng")

    def _stop(self, *args):
        # Xử lý nốt lô đang nhận rồi thoát (Kubernetes gửi SIGTERM trước khi xóa pod).
        self.running = False

    def _process(self, body):
        try:
            event = json.loads(body)
        except ValueError:
            log.error("Tin không phải JSON, để SQS chuyển sang DLQ: %.200s", body)
            return False
        if event.get("type") != "order_completed":
            log.warning("Bỏ qua loại sự kiện lạ: %s", event.get("type"))
            return True
        try:
            result = catalog.post("/api/stock/apply-order/",
                                  json={"order_id": event["order_id"], "items": event.get("items", [])})
        except ServiceError:
            log.warning("Chưa trừ được tồn kho cho đơn %s, sẽ thử lại", event.get("order_id"), exc_info=True)
            return False
        log.info("Đơn %s: %s", event["order_id"], result)
        return True
