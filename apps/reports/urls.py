from django.urls import path

from .views import AttendanceReportsAPIView

urlpatterns = [
    path("reports/", AttendanceReportsAPIView.as_view(), name="admin-attendance-reports"),
]
