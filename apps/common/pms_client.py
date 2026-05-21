import json
from urllib import error, request

from django.conf import settings


def _authorization_header(token=None):
    service_token = getattr(settings, "PMS_SERVICE_TOKEN", "") or ""
    raw = token or service_token
    if not raw:
        return None
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    return text if text.lower().startswith("bearer ") else f"Bearer {text}"


def fetch_employee_profile(employee_id, *, token=None):
    base_url = getattr(settings, "PMS_API_BASE_URL", "") or ""
    authorization = _authorization_header(token)
    if not base_url or not authorization:
        return {}

    profile_request = request.Request(
        f"{base_url.rstrip('/')}/users/{employee_id}/",
        headers={"Authorization": authorization, "Accept": "application/json"},
    )
    try:
        with request.urlopen(profile_request, timeout=5) as response:
            body = json.loads(response.read().decode())
    except (error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
        return {}

    return body.get("data") or {}


def employee_display_name(profile, employee_id):
    if not profile:
        return f"Employee {employee_id}"
    first = (profile.get("first_name") or "").strip()
    last = (profile.get("last_name") or "").strip()
    full_name = f"{first} {last}".strip()
    return full_name or profile.get("email") or f"Employee {employee_id}"


def employee_email(profile):
    return (profile or {}).get("email") or ""


def fetch_all_users(*, token=None):
    """Return all PMS users (paginated list), or [] if PMS is unreachable."""
    base_url = getattr(settings, "PMS_API_BASE_URL", "") or ""
    authorization = _authorization_header(token)
    if not base_url or not authorization:
        return []

    users = []
    page = 1
    page_size = 100
    while True:
        list_url = f"{base_url.rstrip('/')}/users/?page={page}&page_size={page_size}"
        list_request = request.Request(
            list_url,
            headers={"Authorization": authorization, "Accept": "application/json"},
        )
        try:
            with request.urlopen(list_request, timeout=10) as response:
                body = json.loads(response.read().decode())
        except (error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
            break

        results = []
        total_pages = 1
        if isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, dict):
                results = data.get("results") or []
                meta = body.get("meta") or {}
                total_pages = int(meta.get("total_pages") or 1)
            elif isinstance(data, list):
                results = data
            elif "results" in body:
                results = body.get("results") or []
                total_pages = int(body.get("total_pages") or 1)

        users.extend(results)
        if page >= total_pages or not results:
            break
        page += 1

    return users
