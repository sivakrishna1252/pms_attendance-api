from django.urls import path

from .views import ApplyLeaveAPIView, HolidayListAPIView, LeaveBalanceAPIView, LeaveHistoryAPIView

urlpatterns = [
    path("apply/", ApplyLeaveAPIView.as_view(), name="leave-apply"),
    path("history/", LeaveHistoryAPIView.as_view(), name="leave-history"),
    path("balance/", LeaveBalanceAPIView.as_view(), name="leave-balance"),
    path("holidays/", HolidayListAPIView.as_view(), name="leave-holidays"),
]
