from django.urls import path

from .views import (
    AdminLeaveListAPIView,
    ApproveLeaveAPIView,
    PendingLeaveRequestsAPIView,
    RejectLeaveAPIView,
)

urlpatterns = [
    path("", AdminLeaveListAPIView.as_view(), name="admin-leaves-list"),
    path("pending/", PendingLeaveRequestsAPIView.as_view(), name="admin-leaves-pending"),
    path("<int:leave_id>/approve/", ApproveLeaveAPIView.as_view(), name="admin-leaves-approve"),
    path("<int:leave_id>/reject/", RejectLeaveAPIView.as_view(), name="admin-leaves-reject"),
]
