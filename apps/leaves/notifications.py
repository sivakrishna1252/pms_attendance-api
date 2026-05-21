import logging

from django.conf import settings

from apps.common.email import send_attendance_email
from apps.common.pms_client import (
    admin_users_from_pms,
    create_pms_notifications,
    employee_display_name,
    employee_email,
    fetch_employee_profile,
)

from .models import LeaveRequest

logger = logging.getLogger(__name__)


def _leave_type_label(value):
    return dict(LeaveRequest.LeaveType.choices).get(value, str(value).title())


def _leave_date_range(leave_request):
    return (
        f"{leave_request.from_date.strftime('%b %d')} - "
        f"{leave_request.to_date.strftime('%b %d, %Y')}"
    )


def notify_admins_leave_submitted(leave_request):
    """Bell + email for admins when an employee submits leave."""
    profile = fetch_employee_profile(leave_request.employee_id)
    employee_name = employee_display_name(profile, leave_request.employee_id)
    leave_label = _leave_type_label(leave_request.leave_type)
    date_range = _leave_date_range(leave_request)
    message = (
        f"{employee_name} submitted {leave_label} leave ({date_range}). "
        "Review in Leave Management."
    )

    admins = admin_users_from_pms()
    if not admins:
        logger.warning(
            "No admin users for leave notification (leave_id=%s). "
            "Check PMS is running and JWT_SECRET matches PMS DJANGO_SECRET_KEY.",
            leave_request.id,
        )
        return

    notifications = [
        {
            "user_id": admin["id"],
            "type": "LEAVE_REQUEST_SUBMITTED",
            "title": "New leave request",
            "message": message,
            "ref_type": "LEAVE",
            "ref_id": leave_request.id,
            "details": {
                "employee_id": leave_request.employee_id,
                "status": leave_request.status,
            },
        }
        for admin in admins
    ]
    created = create_pms_notifications(notifications)
    if created == 0:
        logger.warning("Failed to create admin notifications for leave_id=%s", leave_request.id)

    subject = f"New leave request — {employee_name}"
    body = (
        f"Hi,\n\n"
        f"{employee_name} has applied for {leave_label} leave ({date_range}).\n"
        f"Reason: {leave_request.reason}\n\n"
        "Please review pending requests in Leave Management.\n\n"
        "— Attendance System"
    )
    for admin in admins:
        recipient = employee_email(admin)
        if not recipient:
            profile = fetch_employee_profile(admin["id"])
            recipient = employee_email(profile)
        if recipient:
            send_attendance_email(subject=subject, message=body, recipient_email=recipient)
        else:
            logger.warning("No email for admin user_id=%s (leave_id=%s)", admin.get("id"), leave_request.id)


def notify_employee_leave_decision(
    leave_request,
    *,
    approved=True,
    rejection_reason="",
):
    """Bell notification + email for employee when admin approves or rejects leave."""
    profile = fetch_employee_profile(leave_request.employee_id)
    leave_label = _leave_type_label(leave_request.leave_type)
    date_range = _leave_date_range(leave_request)

    if approved:
        notif_type = "LEAVE_APPROVED"
        title = "Leave approved"
        message = f"Your {leave_label} leave ({date_range}) has been approved."
    else:
        notif_type = "LEAVE_REJECTED"
        title = "Leave rejected"
        reason_bit = f" Reason: {rejection_reason}" if rejection_reason else ""
        message = f"Your {leave_label} leave ({date_range}) was rejected.{reason_bit}"

    created = create_pms_notifications(
        [
            {
                "user_id": leave_request.employee_id,
                "type": notif_type,
                "title": title,
                "message": message,
                "ref_type": "LEAVE",
                "ref_id": leave_request.id,
                "details": {
                    "status": leave_request.status,
                    "approved": approved,
                },
            }
        ]
    )
    if created == 0:
        logger.warning(
            "Failed to create employee notification leave_id=%s user_id=%s",
            leave_request.id,
            leave_request.employee_id,
        )

    from .services import send_leave_status_email

    send_leave_status_email(
        leave_request,
        approved=approved,
        rejection_reason=rejection_reason,
        profile=profile,
    )
