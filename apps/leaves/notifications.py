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

# Leave request email routing (admin gets in-app bell only — not this email).
LEAVE_REQUEST_TO_DEFAULT = "harsh.singh@apparatus.solutions"
LEAVE_REQUEST_CC_DEFAULT = [
    "Vivek@apparatus.solutions",
    "Rishabh@apparatus.solutions",
]

_ROLE_LABELS = {
    "EMPLOYEE": "Employee",
    "BA": "Business Analyst",
    "ADMIN": "Admin",
}


def _leave_type_label(value):
    return dict(LeaveRequest.LeaveType.choices).get(value, str(value).title())


def _leave_date_range(leave_request):
    return (
        f"{leave_request.from_date.strftime('%b %d')} - "
        f"{leave_request.to_date.strftime('%b %d, %Y')}"
    )


def _ordinal(day):
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def _format_leave_period(leave_request):
    from_date = leave_request.from_date
    to_date = leave_request.to_date

    if from_date == to_date:
        return f"{from_date.strftime('%B')} {_ordinal(from_date.day)}, {from_date.year}"

    from_part = f"{from_date.strftime('%B')} {_ordinal(from_date.day)}"
    to_part = f"{to_date.strftime('%B')} {_ordinal(to_date.day)}, {to_date.year}"

    if from_date.year == to_date.year and from_date.month == to_date.month:
        return f"{from_part} to {_ordinal(to_date.day)}, {to_date.year}"

    if from_date.year == to_date.year:
        return f"{from_part} to {to_part}"

    return f"{from_part}, {from_date.year} to {to_part}"


def _employee_job_title(profile):
    department = ((profile or {}).get("department") or "").strip()
    if department:
        return department

    role = ((profile or {}).get("role") or "").strip()
    return _ROLE_LABELS.get(role, role.replace("_", " ").title() or "Employee")


def _build_leave_request_paragraph(*, reason, period):
    """Combine employee-written reason with system-selected leave dates."""
    reason = (reason or "").strip().rstrip(".")
    if not reason:
        return f"I request leave from {period} due to personal commitments."

    lower = reason.lower()
    if lower.startswith("i would like to inform you that"):
        base = reason
    elif lower.startswith("i ") and "request leave" in lower:
        return reason if reason.endswith(".") else f"{reason}."
    else:
        base = f"I would like to inform you that {reason}"

    return f"{base}. I request leave from {period}."


def _pms_leave_review_url(leave_request):
    base = getattr(settings, "PMS_FRONTEND_URL", "https://nexus-pms.aspune.cloud").strip().rstrip("/")
    return f"{base}/admin/leave-management?leaveId={leave_request.id}"


def _build_leave_request_email_body(*, employee_name, job_title, leave_request):
    period = _format_leave_period(leave_request)
    request_line = _build_leave_request_paragraph(
        reason=leave_request.reason,
        period=period,
    )
    review_url = _pms_leave_review_url(leave_request)

    return (
        "Hi Everyone,\n\n"
        "I hope you are doing well.\n\n"
        f"{request_line}\n\n"
        "I will ensure that any pending work is managed accordingly.\n\n"
        "Thank you.\n"
        "Yours sincerely,\n"
        f"{employee_name}\n"
        f"{job_title}\n\n"
        "---\n"
        "Please open PMS and review this leave request in Leave Management:\n"
        f"{review_url}"
    )


def _leave_request_recipients():
    to_email = (
        getattr(settings, "LEAVE_REQUEST_TO_EMAIL", "") or LEAVE_REQUEST_TO_DEFAULT
    ).strip()
    cc_emails = getattr(settings, "LEAVE_REQUEST_CC_EMAILS", None) or LEAVE_REQUEST_CC_DEFAULT
    cc_emails = [address.strip() for address in cc_emails if address and str(address).strip()]
    return to_email, cc_emails


def _send_leave_request_email(leave_request, profile, employee_name):
    to_email, cc_emails = _leave_request_recipients()

    if not to_email:
        logger.warning(
            "Skipping leave request email (leave_id=%s): no To recipient configured.",
            leave_request.id,
        )
        return

    job_title = _employee_job_title(profile)
    subject = f"Leave Request — {employee_name}"
    body = _build_leave_request_email_body(
        employee_name=employee_name,
        job_title=job_title,
        leave_request=leave_request,
    )
    employee_reply_to = employee_email(profile)

    sent = send_attendance_email(
        subject=subject,
        message=body,
        recipient_email=to_email,
        cc=cc_emails,
        reply_to=[employee_reply_to] if employee_reply_to else None,
    )
    if sent:
        logger.info(
            "Leave request email sent (leave_id=%s) To=%s CC=%s Reply-To=%s",
            leave_request.id,
            to_email,
            ", ".join(cc_emails) or "(none)",
            employee_reply_to or "(none)",
        )


def notify_admins_leave_submitted(leave_request):
    """In-app bell for admins; professional leave email to configured To/CC only."""
    profile = fetch_employee_profile(leave_request.employee_id)
    employee_name = employee_display_name(profile, leave_request.employee_id)
    leave_label = _leave_type_label(leave_request.leave_type)
    date_range = _leave_date_range(leave_request)
    message = (
        f"{employee_name} submitted {leave_label} leave ({date_range}). "
        "Review in Leave Management."
    )

    admins = admin_users_from_pms()
    if admins:
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
    else:
        logger.warning(
            "No admin users for leave notification (leave_id=%s). "
            "Check PMS is running and JWT_SECRET matches PMS DJANGO_SECRET_KEY.",
            leave_request.id,
        )

    _send_leave_request_email(leave_request, profile, employee_name)


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
