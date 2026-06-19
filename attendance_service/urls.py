from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
import apps.authentication.schema 

from django.http import HttpResponse


def home(request):
    return HttpResponse("Welcome to Attendance Service API")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api/attendance/", include("apps.attendance.urls")),
    path("api/leaves/", include("apps.leaves.urls")),
    path("api/admin/", include("apps.reports.urls")),
    path("api/admin/leaves/", include("apps.leaves.admin_urls")),
    path("api/admin/holidays/", include("apps.leaves.admin_holiday_urls")),
    path("api/internal/", include("apps.common.internal_urls")),
    path("", home),
]
