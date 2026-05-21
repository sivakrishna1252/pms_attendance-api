import hashlib

from django.conf import settings


def derived_attendance_service_token():
    secret = (getattr(settings, "JWT_SECRET", "") or getattr(settings, "SECRET_KEY", "") or "").strip()
    if not secret:
        return ""
    return hashlib.sha256(f"pms-attendance-service:{secret}".encode()).hexdigest()


def resolve_service_token():
    explicit = (getattr(settings, "PMS_SERVICE_TOKEN", "") or "").strip()
    if explicit:
        return explicit
    return derived_attendance_service_token()


def service_authorization_header():
    token = resolve_service_token()
    if not token:
        return None
    return f"Bearer {token}"
