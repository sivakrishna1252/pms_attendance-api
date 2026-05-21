from apps.common.pms_client import (
    employee_display_name,
    employee_email,
    fetch_employee_profile,
)

_DEPARTMENT_LABELS = {
    "FRONTEND": "Frontend",
    "BACKEND": "Backend",
    "FULLSTACK": "Fullstack",
    "HR": "HR",
    "SALES": "Sales",
    "ENGINEERING": "Engineering",
}

_ROLE_LABELS = {
    "ADMIN": "Admin",
    "BA": "BA",
    "EMPLOYEE": "Employee",
}


def _department_label(profile):
    raw = (profile or {}).get("department") or ""
    text = str(raw).strip()
    if not text:
        return ""
    upper = text.upper()
    return _DEPARTMENT_LABELS.get(upper, text.replace("_", " ").title())


def _role_label(profile):
    raw = (profile or {}).get("role") or ""
    text = str(raw).strip()
    if not text:
        return ""
    upper = text.upper()
    return _ROLE_LABELS.get(upper, text.replace("_", " ").title())


def _initials(profile, employee_id):
    first = ((profile or {}).get("first_name") or "").strip()
    last = ((profile or {}).get("last_name") or "").strip()
    if first and last:
        return f"{first[0]}{last[0]}".upper()
    if first:
        return first[:2].upper()
    email = employee_email(profile)
    if email:
        return email[0].upper()
    return f"E{employee_id}"


class EmployeeProfileResolver:
    """Resolve PMS user profile fields once per employee per request."""

    def __init__(self, *, token=None):
        self.token = token
        self._cache = {}

    def seed_from_users(self, users):
        """Preload profiles from a PMS user list (avoids per-employee HTTP calls)."""
        for user in users or []:
            user_id = user.get("id")
            if user_id is None:
                continue
            self._cache[int(user_id)] = user

    def profile(self, employee_id):
        employee_id = int(employee_id)
        if employee_id not in self._cache:
            self._cache[employee_id] = fetch_employee_profile(employee_id, token=self.token)
        return self._cache[employee_id]

    def display_name(self, employee_id):
        return employee_display_name(self.profile(employee_id), employee_id)

    def department_label(self, employee_id):
        return _department_label(self.profile(employee_id))

    def role_label(self, employee_id):
        return _role_label(self.profile(employee_id))

    def initials(self, employee_id):
        return _initials(self.profile(employee_id), employee_id)

    def employee_block(self, employee_id):
        employee_id = int(employee_id)
        profile = self.profile(employee_id)
        name = employee_display_name(profile, employee_id)
        department = _department_label(profile) or "—"
        role = _role_label(profile) or "—"
        return {
            "id": employee_id,
            "name": name,
            "department": department,
            "role": role,
            "initials": _initials(profile, employee_id),
            "email": employee_email(profile),
        }


def resolver_from_request(request):
    token = request.headers.get("Authorization")
    return EmployeeProfileResolver(token=token)
