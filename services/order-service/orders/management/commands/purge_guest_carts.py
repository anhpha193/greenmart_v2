"""Dọn giỏ của khách chưa đăng nhập bị bỏ lại (1.2.8). Chạy bằng CronJob cart-purge (k8s/overlays/eks).

  python manage.py purge_guest_carts                     # dry-run (mặc định): chỉ đếm, không xóa
  python manage.py purge_guest_carts --execute --days 14 # xóa thật

Chỉ xóa giỏ thỏa TẤT CẢ:
  - chưa hoàn tất (complete = 0), của khách (customer_id IS NULL) - không bao giờ đụng đơn hàng hay giỏ của user;
  - lần ghi giỏ gần nhất (updated_at, NULL thì date_order) cũ hơn --days ngày;
  - không có dòng nào được thêm sau mốc đó (giỏ từ trước 1.2.8 chưa có updated_at).
Mặc định 14 ngày = thời hạn session của khách (SESSION_COOKIE_AGE): quá hạn này trình duyệt không còn mở lại được
giỏ, nên giỏ chắc chắn đã bị bỏ.

An toàn với MySQL đang chạy: duyệt theo khóa chính (id > id cuối của lô trước), mỗi lô một transaction ngắn, khóa
đúng các dòng của lô bằng SELECT ... FOR UPDATE SKIP LOCKED (giỏ đang được request khác ghi thì bỏ qua, lần sau
xét lại), kiểm tra lại điều kiện ngay trong câu khóa, nghỉ --sleep giây giữa các lô và dừng sau --max-batches lô.
"""
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import Exists, OuterRef, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from greenmart_common import cartmodel
from orders.models import Order, OrderItem


class Command(BaseCommand):
    help = "Dọn giỏ khách bỏ lại theo lô (mặc định dry-run)"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=14, help="giữ giỏ có hoạt động trong ngần ấy ngày (>= 1)")
        parser.add_argument("--batch", type=int, default=500)
        parser.add_argument("--sleep", type=float, default=0.5, help="nghỉ giữa các lô (giây)")
        parser.add_argument("--max-batches", type=int, default=400, help="số lô tối đa mỗi lần chạy")
        parser.add_argument("--execute", action="store_true", help="xóa thật (không có cờ này = dry-run)")

    def handle(self, *args, days, batch, sleep, max_batches, execute, **kw):
        if days < 1:
            raise SystemExit("--days phải >= 1")
        cutoff = timezone.now() - timedelta(days=days)
        recent_item = OrderItem.objects.filter(order=OuterRef("pk"), date_added__gte=cutoff)
        abandoned = (Order.objects
                     .filter(complete=False, customer_id__isnull=True)
                     .annotate(last_write=Coalesce("updated_at", "date_order"))
                     .filter(last_write__lt=cutoff)
                     .filter(~Exists(recent_item)))
        mode = "XÓA" if execute else "DRY-RUN"
        self.stdout.write(f"[purge] {mode} giỏ khách không hoạt động từ trước {cutoff:%Y-%m-%d %H:%M} UTC "
                          f"(lô {batch}, nghỉ {sleep}s, tối đa {max_batches} lô)")
        if not execute:
            total = abandoned.count()
            self.stdout.write(f"[purge] DRY-RUN: {total} giỏ đủ điều kiện (chưa xóa gì). Thêm --execute để xóa.")
            return

        skip_locked = connection.features.has_select_for_update_skip_locked
        last_id, deleted, items = 0, 0, 0
        for n in range(max_batches):
            # 1) tìm ứng viên KHÔNG khóa (quét theo khóa chính). RDS đang chạy READ-COMMITTED, nhưng nếu ai đổi sang
            #    REPEATABLE READ thì khóa ngay lúc quét dải id sẽ khóa cả các dòng không khớp (giỏ user, giỏ đang dùng)
            #    cho tới khi lô commit - nên tách tìm và khóa ra hai bước.
            candidates = list(abandoned.filter(id__gt=last_id).order_by("id").values_list("id", flat=True)[:batch])
            if not candidates:
                break
            last_id = candidates[-1]
            with transaction.atomic():
                # 2) chỉ khóa đúng các id đó, kiểm tra lại điều kiện trong cùng câu khóa; dòng đang bị khóa thì bỏ qua
                # OF self: chỉ khóa bảng giỏ, không khóa các dòng orderitem mà subquery "có dòng mới" đọc qua
                of = ("self",) if connection.features.has_select_for_update_of else ()
                ids = list(abandoned.filter(pk__in=candidates).select_for_update(skip_locked=skip_locked, of=of)
                           .values_list("id", "guest_token", "version"))
                pks = [i for i, _, _ in ids]
                if pks:
                    items += OrderItem.objects.filter(order_id__in=pks).delete()[0]
                    deleted += Order.objects.filter(Q(pk__in=pks), complete=False, customer_id__isnull=True).delete()[0]
            for oid, token, ver in ids:  # read model (nếu còn) của giỏ đã xóa: tombstone để không hiện lại
                cartmodel.tombstone(cartmodel.owner_key(None, token), oid, ver + 1)
            if n % 20 == 0:
                self.stdout.write(f"[purge] đã xóa {deleted} giỏ ({items} dòng), id cuối {last_id}")
            time.sleep(sleep)
        self.stdout.write(f"[purge] XONG: xóa {deleted} giỏ ({items} dòng), id cuối {last_id}")
