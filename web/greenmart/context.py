from django.conf import settings


def user(request):
    """Thay cho context processor 'auth' của Django: template vẫn dùng {{ user.is_authenticated }}.
    gm_me_cache: feature flag cho gm.js (giống nhau với mọi người nên không ảnh hưởng microcache)."""
    return {"user": getattr(request, "user", None), "gm_me_cache": "1" if settings.CLIENT_ME_CACHE else "0"}
