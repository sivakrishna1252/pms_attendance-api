"""Direct PMS PostgreSQL access when internal HTTP APIs are not deployed yet."""

import json
import logging

from django.db import connections
from django.utils import timezone

logger = logging.getLogger(__name__)


def _pms_available():
    return "pms" in connections.databases


def _cursor():
    return connections["pms"].cursor()


def _row_to_user(row, columns):
    return dict(zip(columns, row, strict=False))


def admin_users_from_db():
    if not _pms_available():
        return []
    try:
        with _cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id, u.email, u.first_name, u.last_name, p.role, p.status
                FROM auth_user u
                INNER JOIN pms_api_userprofile p ON p.user_id = u.id
                WHERE p.role = 'ADMIN' AND p.status = 'ACTIVE'
                ORDER BY u.id
                """
            )
            columns = [col[0] for col in cursor.description]
            return [_row_to_user(row, columns) for row in cursor.fetchall()]
    except Exception:
        logger.exception("PMS DB admin user lookup failed")
        return []


def staff_users_from_db():
    if not _pms_available():
        return []
    try:
        with _cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id, u.email, u.first_name, u.last_name, p.role, p.status, p.department
                FROM auth_user u
                INNER JOIN pms_api_userprofile p ON p.user_id = u.id
                WHERE p.role IN ('EMPLOYEE', 'BA') AND p.status = 'ACTIVE'
                ORDER BY u.id
                """
            )
            columns = [col[0] for col in cursor.description]
            return [_row_to_user(row, columns) for row in cursor.fetchall()]
    except Exception:
        logger.exception("PMS DB staff user lookup failed")
        return []


def user_profile_from_db(user_id):
    if not _pms_available():
        return {}
    try:
        with _cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id, u.email, u.first_name, u.last_name, p.role, p.status, p.department
                FROM auth_user u
                LEFT JOIN pms_api_userprofile p ON p.user_id = u.id
                WHERE u.id = %s
                LIMIT 1
                """,
                [int(user_id)],
            )
            row = cursor.fetchone()
            if not row:
                return {}
            columns = [col[0] for col in cursor.description]
            return _row_to_user(row, columns)
    except Exception:
        logger.exception("PMS DB user profile lookup failed for user_id=%s", user_id)
        return {}


def create_notifications_in_db(notifications):
    if not _pms_available() or not notifications:
        return 0

    now = timezone.now()
    created = 0
    try:
        with _cursor() as cursor:
            for item in notifications:
                user_id = item.get("user_id")
                if user_id is None:
                    continue
                details = item.get("details")
                cursor.execute(
                    """
                    INSERT INTO pms_api_notification (
                        user_id, type, title, message, ref_type, ref_id,
                        is_read, details, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, FALSE, %s, %s, %s)
                    """,
                    [
                        int(user_id),
                        item.get("type") or "",
                        item.get("title") or "",
                        item.get("message") or "",
                        item.get("ref_type") or "",
                        item.get("ref_id"),
                        json.dumps(details) if details is not None else None,
                        now,
                        now,
                    ],
                )
                created += 1
    except Exception:
        logger.exception("PMS DB notification insert failed")
        return 0
    return created
