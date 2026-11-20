"""Tạo/sửa/xóa khuyến mãi thì làm mới cache khuyến mãi."""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from greenmart_common.cache import bump_version

from .models import Promotion

CACHE_GROUP = "promotion"


@receiver([post_save, post_delete], sender=Promotion)
def invalidate_promotion_cache(**kwargs):
    bump_version(CACHE_GROUP)
