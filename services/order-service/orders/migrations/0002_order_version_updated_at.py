# 1.2.8: nhãn phiên bản cho Cart Read Model + thời điểm ghi giỏ gần nhất (CronJob dọn giỏ khách).
# MySQL 8: ADD COLUMN có DEFAULT / cho phép NULL chạy kiểu INSTANT (chỉ đổi metadata), không khóa bảng lâu.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="version",
            field=models.BigIntegerField(default=0, db_default=0),
        ),
        migrations.AddField(
            model_name="order",
            name="updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
