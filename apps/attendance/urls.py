from django.urls import path

from .views import (
    AdminAttendanceHistoryAPIView,
    AdminAttendanceStatusOverrideAPIView,
    AdminDashboardAPIView,
    AttendanceActivityAPIView,
    AttendanceHistoryAPIView,
    CheckInAPIView,
    CheckOutAPIView,
    TodayAttendanceAPIView,
)

urlpatterns = [
    path("check-in/", CheckInAPIView.as_view(), name="attendance-check-in"),
    path("check-out/", CheckOutAPIView.as_view(), name="attendance-check-out"),
    path("activity/", AttendanceActivityAPIView.as_view(), name="attendance-activity"),
    path("history/", AttendanceHistoryAPIView.as_view(), name="attendance-history"),
    path("today/", TodayAttendanceAPIView.as_view(), name="attendance-today"),
    path("admin/history/", AdminAttendanceHistoryAPIView.as_view(), name="admin-attendance-history"),
    path("admin/dashboard/", AdminDashboardAPIView.as_view(), name="admin-attendance-dashboard"),
    path("admin/override/", AdminAttendanceStatusOverrideAPIView.as_view(), name="admin-attendance-override"),
]
