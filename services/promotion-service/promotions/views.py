from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from greenmart_common.cache import bump_version, cached

from .models import Promotion
from .serializers import PromotionWriteSerializer, promotion_dict
from .signals import CACHE_GROUP

# Quy tắc khuyến mãi ít đổi; mọi lần ghi đều tăng phiên bản cache nên 60 giây chỉ là giới hạn trên.
TTL = 60


class PromotionListCreateView(APIView):
    def get(self, request):
        active = request.query_params.get("active") in ("1", "true")
        return Response(cached(CACHE_GROUP, f"list:active={int(active)}", TTL, lambda: self._list(active)))

    @staticmethod
    def _list(active):
        qs = Promotion.objects.all().order_by("id")
        if active:
            qs = qs.filter(is_active=True)
        return [promotion_dict(p) for p in qs]

    def post(self, request):
        ser = PromotionWriteSerializer(data=request.data)
        if not ser.is_valid():
            return Response({"errors": ser.errors}, status=400)
        data = ser.validated_data
        now = timezone.now()
        start = data.pop("start_date", None) or now
        end = data.get("end_date")
        # Quy tắc giữ nguyên bản cũ: chưa tới ngày bắt đầu hoặc đã quá hạn => chưa/không kích hoạt.
        is_active = not (start > now or (end and end < now))
        promo = Promotion.objects.create(is_active=is_active, **data)
        return Response(promotion_dict(promo), status=201)


class PromotionDetailView(APIView):
    def get(self, request, promo_id):
        return Response(cached(CACHE_GROUP, f"detail:{promo_id}", TTL,
                               lambda: promotion_dict(get_object_or_404(Promotion, pk=promo_id))))

    def delete(self, request, promo_id):
        Promotion.objects.filter(pk=promo_id).delete()
        bump_version(CACHE_GROUP)  # QuerySet.delete() vẫn phát post_delete, gọi thêm cho chắc khi không có dòng nào
        return Response(status=204)
