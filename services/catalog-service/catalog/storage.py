"""Lưu ảnh sản phẩm trên S3 (bật khi có MEDIA_BUCKET).

Ảnh nằm ở key media/<đường dẫn>. URL trả cho trình duyệt vẫn là /media/<đường dẫn> (tương đối):
gateway nginx chuyển /media/ tới S3 qua VPC endpoint, CloudFront cache /media/* ở edge.
Nhờ vậy catalog-service không còn giữ ổ EBS và chạy được nhiều pod.
"""
from django.conf import settings
from django.utils.encoding import filepath_to_uri
from storages.backends.s3 import S3Storage


class MediaStorage(S3Storage):
    location = "media"
    file_overwrite = False
    default_acl = None
    querystring_auth = False

    def url(self, name, parameters=None, expire=None, http_method=None):
        return settings.MEDIA_URL + filepath_to_uri(name)
