import json
import logging
from urllib import error, request

from django.conf import settings

from apps.common.pms_db_bridge import (
    admin_users_from_db,
    create_notifications_in_db,
    staff_users_from_db,
    user_profile_from_db,
)
from apps.common.service_auth import service_authorization_header

logger = logging.getLogger(__name__)


def _pms_get_json(path, *, timeout=10):
    base_url = getattr(settings, "PMS_API_BASE_URL", "") or ""
    authorization = service_authorization_header()
    if not base_url or not authorization:
        logger.warning("PMS call skipped (%s): missing PMS_API_BASE_URL or service token.", path)
        return None

    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    req = request.Request(
        url,
        headers={"Authorization": authorization, "Accept": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode()[:500]
        except OSError:
            pass
        logger.warning("PMS GET %s failed: HTTP %s — %s", url, exc.code, body)
        return None
    except (error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("PMS GET %s failed: %s", url, exc)
        return None


def _pms_post_json(path, payload, *, timeout=10):
    base_url = getattr(settings, "PMS_API_BASE_URL", "") or ""
    authorization = service_authorization_header()
    if not base_url or not authorization:
        logger.warning("PMS call skipped (%s): missing PMS_API_BASE_URL or service token.", path)
        return None

    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    req = request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": authorization,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode()[:500]
        except OSError:
            pass
        logger.warning("PMS POST %s failed: HTTP %s — %s", url, exc.code, body)
        return None
    except (error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("PMS POST %s failed: %s", url, exc)
        return None


def fetch_employee_profile(employee_id):
    """Load PMS user profile via internal service API, with PMS DB fallback."""
    body = _pms_get_json(f"internal/users/{employee_id}/")
    if isinstance(body, dict) and body.get("success"):
        return body.get("data") or {}
    profile = user_profile_from_db(employee_id)
    if profile:
        return profile
    return {}


def employee_display_name(profile, employee_id):
    if not profile:
        return f"Employee {employee_id}"
    first = (profile.get("first_name") or "").strip()
    last = (profile.get("last_name") or "").strip()
    full_name = f"{first} {last}".strip()
    if full_name:
        return full_name
    email = (profile.get("email") or "").strip()
    if email:
        local = email.split("@")[0].replace(".", " ").replace("_", " ").strip()
        if local:
            return local.title()
        return email
    return f"Employee {employee_id}"


def employee_email(profile):
    return (profile or {}).get("email") or ""


def admin_users_from_pms():
    """Active PMS admin users (HTTP internal API, then PMS DB fallback)."""
    body = _pms_get_json("internal/admin-users/")
    if isinstance(body, dict) and body.get("success"):
        data = body.get("data") or {}
        results = data.get("results") if isinstance(data, dict) else []
        if results:
            return results

    db_admins = admin_users_from_db()
    if db_admins:
        return db_admins
    return _admin_users_from_settings_fallback()


def _admin_users_from_settings_fallback():
    """Fallback when PMS internal API is unreachable."""
    ids = getattr(settings, "ADMIN_EMPLOYEE_IDS", None) or set()
    return [{"id": int(uid), "email": "", "first_name": "", "last_name": ""} for uid in ids]


def create_pms_notifications(notifications):
    """Create in-app notifications (HTTP internal API, then PMS DB fallback)."""
    if not notifications:
        return 0

    body = _pms_post_json("internal/notifications/", {"notifications": notifications})
    if isinstance(body, dict) and body.get("success"):
        data = body.get("data") or {}
        created_ids = data.get("created_ids") or []
        if created_ids:
            return len(created_ids)

    created = create_notifications_in_db(notifications)
    if created:
        logger.info("Created %s notification(s) via PMS database bridge.", created)
    return created


def staff_users_from_pms():
    """Active PMS Employee/BA users (HTTP internal API, then PMS DB fallback)."""
    body = _pms_get_json("internal/staff-users/")
    if isinstance(body, dict) and body.get("success"):
        data = body.get("data") or {}
        results = data.get("results") if isinstance(data, dict) else []
        if results:
            return results
    return staff_users_from_db()
