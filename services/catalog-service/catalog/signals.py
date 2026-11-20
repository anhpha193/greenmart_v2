"""Mọi thay đổi sản phẩm, danh mục, đánh giá (API, Django admin, order-worker) làm mới cache catalog."""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from greenmart_common.cache import bump_version

from .models import Category, Product, Review

CACHE_GROUP = "catalog"


@receiver([post_save, post_delete], sender=Product)
@receiver([post_save, post_delete], sender=Category)
@receiver([post_save, post_delete], sender=Review)
def invalidate_catalog_cache(**kwargs):
    bump_version(CACHE_GROUP)
