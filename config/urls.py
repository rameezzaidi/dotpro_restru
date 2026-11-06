from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", include("rms.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)   # dev only; use nginx/CDN for media in production
