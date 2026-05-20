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
