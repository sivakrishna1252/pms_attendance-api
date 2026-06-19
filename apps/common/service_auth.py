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


def expected_service_tokens():
    """Tokens PMS may send when calling attendance internal APIs."""
    tokens = []
    explicit = (getattr(settings, "PMS_SERVICE_TOKEN", "") or "").strip()
    if explicit:
        tokens.append(explicit)
    derived = derived_attendance_service_token()
    if derived and derived not in tokens:
        tokens.append(derived)
    return tokens


def token_from_authorization_header(auth_header):
    auth = (auth_header or "").strip()
    if not auth:
        return ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return auth


def is_valid_service_authorization(auth_header):
    supplied = token_from_authorization_header(auth_header)
    if not supplied:
        return False
    return supplied in expected_service_tokens()
