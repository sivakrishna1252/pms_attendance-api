from django.conf import settings
from rest_framework.permissions import BasePermission


class IsAttendanceAdmin(BasePermission):
    message = (
        "Admin access requires a PMS token with role=ADMIN/is_staff, "
        "or the employee id must be listed in ADMIN_EMPLOYEE_IDS."
    )

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        employee_id = getattr(user, "employee_id", None)
        role = str(getattr(user, "role", "") or "").upper()

        return (
            role == "ADMIN"
            or bool(getattr(user, "is_staff", False))
            or employee_id in settings.ADMIN_EMPLOYEE_IDS
        )


class IsServiceToken(BasePermission):
    """PMS service calls: PMS_SERVICE_TOKEN or derived token from shared secret."""

    def has_permission(self, request, view):
        from apps.common.service_auth import is_valid_service_authorization

        return is_valid_service_authorization(request.headers.get("Authorization"))
