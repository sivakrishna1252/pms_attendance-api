from django.urls import path

from .views import AdminHolidayDetailAPIView, AdminHolidayListCreateAPIView

urlpatterns = [
    path("", AdminHolidayListCreateAPIView.as_view(), name="admin-holidays"),
    path("<int:holiday_id>/", AdminHolidayDetailAPIView.as_view(), name="admin-holiday-detail"),
]
