from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from apps.common.email import send_attendance_email
from apps.common.pms_client import employee_display_name, employee_email, fetch_employee_profile

from .models import Holiday, LeaveBalance, LeaveRequest

LEAVE_TYPE_TO_BALANCE_FIELD = {
    LeaveRequest.LeaveType.ANNUAL: "annual_leave",
    LeaveRequest.LeaveType.SICK: "sick_leave",
    LeaveRequest.LeaveType.CASUAL: "casual_leave",
    LeaveRequest.LeaveType.COMPENSATORY: "compensatory_leave",
}


def iter_dates(from_date, to_date):
    current = from_date
    while current <= to_date:
        yield current
        current += timedelta(days=1)


def holiday_dates_between(from_date, to_date):
    return set(
        Holiday.objects.filter(
            is_active=True,
            holiday_date__gte=from_date,
            holiday_date__lte=to_date,
        ).values_list("holiday_date", flat=True)
    )


def leave_days_between(from_date, to_date):
    return max((to_date - from_date).days + 1, 1)


def validate_leave_balance(*, employee_id, leave_type, from_date, to_date):
    """Block apply/approve when requested days exceed remaining balance."""
    field_name = LEAVE_TYPE_TO_BALANCE_FIELD.get((leave_type or "").strip().upper())
    if not field_name:
        return

    days = leave_days_between(from_date, to_date)
    balance, _ = LeaveBalance.objects.get_or_create(
        employee_id=employee_id,
        defaults={
            "annual_leave": 18,
            "sick_leave": 12,
            "casual_leave": 6,
            "compensatory_leave": 2,
        },
    )
    current = getattr(balance, field_name)
    if current < days:
        label = field_name.replace("_", " ")
        raise serializers.ValidationError(
            {
                "leave_type": [
                    f"Insufficient {label} balance ({current} left, {days} requested)."
                ]
            }
        )


def validate_leave_application(
    *,
    employee_id,
    leave_type,
    from_date,
    to_date,
    exclude_request_id=None,
):
    if to_date < from_date:
        raise serializers.ValidationError(
            {"to_date": ["To date cannot be before from date."]}
        )

    validate_leave_balance(
        employee_id=employee_id,
        leave_type=leave_type,
        from_date=from_date,
        to_date=to_date,
    )

    holidays = holiday_dates_between(from_date, to_date)
    if holidays:
        labels = ", ".join(sorted(day.isoformat() for day in holidays))
        raise serializers.ValidationError(
            {"from_date": [f"Selected dates include company holiday(s): {labels}."]}
        )

    overlap_query = LeaveRequest.objects.filter(
        employee_id=employee_id,
        status__in=[LeaveRequest.Status.PENDING, LeaveRequest.Status.APPROVED],
        from_date__lte=to_date,
        to_date__gte=from_date,
    )
    if exclude_request_id:
        overlap_query = overlap_query.exclude(pk=exclude_request_id)
    if overlap_query.exists():
        raise serializers.ValidationError(
            {
                "from_date": [
                    "You already have a pending or approved leave for overlapping dates."
                ]
            }
        )


def deduct_leave_balance(leave_request):
    leave_type = (leave_request.leave_type or "").strip().upper()
    field_name = LEAVE_TYPE_TO_BALANCE_FIELD.get(leave_type)
    if not field_name:
        return

    days = leave_days_between(leave_request.from_date, leave_request.to_date)
    validate_leave_balance(
        employee_id=leave_request.employee_id,
        leave_type=leave_request.leave_type,
        from_date=leave_request.from_date,
        to_date=leave_request.to_date,
    )
    balance, _ = LeaveBalance.objects.get_or_create(
        employee_id=leave_request.employee_id,
        defaults={
            "annual_leave": 18,
            "sick_leave": 12,
            "casual_leave": 6,
            "compensatory_leave": 2,
        },
    )
    current = getattr(balance, field_name)
    setattr(balance, field_name, current - days)
    balance.save(update_fields=[field_name, "updated_at"])


def send_leave_status_email(
    leave_request,
    *,
    approved=True,
    rejection_reason="",
    profile=None,
):
    profile = profile if profile is not None else fetch_employee_profile(leave_request.employee_id)
    recipient = employee_email(profile)
    name = employee_display_name(profile, leave_request.employee_id)
    date_range = f"{leave_request.from_date.isoformat()} to {leave_request.to_date.isoformat()}"

    if approved:
        subject = "Leave request approved"
        message = (
            f"Hi {name},\n\n"
            f"Your {leave_request.leave_type.title()} leave ({date_range}) has been approved.\n\n"
            "— Attendance System"
        )
    else:
        subject = "Leave request rejected"
        reason_line = f"\nReason: {rejection_reason}\n" if rejection_reason else "\n"
        message = (
            f"Hi {name},\n\n"
            f"Your {leave_request.leave_type.title()} leave ({date_range}) was rejected.{reason_line}\n"
            "— Attendance System"
        )

    send_attendance_email(subject=subject, message=message, recipient_email=recipient)


def upcoming_holidays(*, from_date=None, limit=50):
    from_date = from_date or timezone.localdate()
    return Holiday.objects.filter(is_active=True, holiday_date__gte=from_date).order_by("holiday_date")[:limit]
