from datetime import datetime, time, timedelta

from django.utils import timezone

from apps.common.email import send_attendance_email
from apps.common.pms_client import employee_display_name, employee_email, fetch_employee_profile

from .constants import AUTO_CHECKOUT_AFTER, SHIFT_START_HOUR, SHIFT_START_MINUTE, STANDARD_WORK_HOURS
from .models import AttendanceLog


def shift_start_on(date_value):
    tz = timezone.get_current_timezone()
    return timezone.make_aware(
        datetime.combine(date_value, time(SHIFT_START_HOUR, SHIFT_START_MINUTE)),
        tz,
    )


def next_check_in_available_at(attendance_date):
    return shift_start_on(attendance_date + timedelta(days=1))


def can_check_in_now(attendance):
    if attendance is None:
        return True, None
    if attendance.check_out_time is not None:
        available_at = next_check_in_available_at(attendance.attendance_date)
        if timezone.now() < available_at:
            return False, available_at
    return False, next_check_in_available_at(attendance.attendance_date)


def duration_to_hours(value):
    if not value:
        return 0.0
    return round(value.total_seconds() / 3600, 2)


def work_analysis(attendance):
    if not attendance or not attendance.check_in_time:
        return {
            "work_hours": None,
            "work_hours_display": "-",
            "expected_hours": duration_to_hours(STANDARD_WORK_HOURS),
            "expected_hours_display": "9h 00m",
            "variance": "none",
            "variance_hours": 0.0,
            "variance_display": "-",
            "is_capped": False,
            "auto_checked_out": bool(getattr(attendance, "auto_checked_out", False)),
        }

    worked = attendance.total_work_hours or timedelta()
    worked_hours = duration_to_hours(worked)
    expected_hours = duration_to_hours(STANDARD_WORK_HOURS)
    variance_hours = round(worked_hours - expected_hours, 2)

    if variance_hours > 0.05:
        variance = "extra_work"
        variance_display = f"+{variance_hours:.2f}h extra work"
    elif variance_hours < -0.05:
        variance = "less_work"
        variance_display = f"{abs(variance_hours):.2f}h less work today"
    else:
        variance = "on_time"
        variance_display = "On target (9h)"

    total_seconds = int(worked.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60

    return {
        "work_hours": worked_hours,
        "work_hours_display": f"{hours}h {minutes:02d}m",
        "expected_hours": expected_hours,
        "expected_hours_display": "9h 00m",
        "variance": variance,
        "variance_hours": variance_hours,
        "variance_display": variance_display,
        "is_capped": bool(getattr(attendance, "capped_at_standard_hours", False)),
        "auto_checked_out": bool(getattr(attendance, "auto_checked_out", False)),
    }


def _notify_forgot_checkout(attendance):
    if attendance.forgot_checkout_email_sent:
        return

    profile = fetch_employee_profile(attendance.employee_id)
    recipient = employee_email(profile)
    name = employee_display_name(profile, attendance.employee_id)
    checkout_time = timezone.localtime(attendance.check_out_time).strftime("%I:%M %p on %d %b %Y")

    sent = send_attendance_email(
        subject="Attendance reminder: automatic check-out",
        recipient_email=recipient,
        message=(
            f"Hi {name},\n\n"
            "You were still checked in after 9 hours without a manual check-out. "
            "The system checked you out automatically and recorded 9 hours of work for today.\n\n"
            f"Automatic check-out time: {checkout_time}\n"
            "Please remember to check out before leaving office.\n\n"
            "— Attendance System"
        ),
    )
    if sent:
        attendance.forgot_checkout_email_sent = True
        attendance.save(update_fields=["forgot_checkout_email_sent", "updated_at"])


def apply_auto_checkout_if_needed(attendance, *, notify=True):
    if attendance is None or attendance.check_out_time is not None or not attendance.check_in_time:
        return attendance

    elapsed = timezone.now() - attendance.check_in_time
    if elapsed < AUTO_CHECKOUT_AFTER:
        return attendance

    attendance.check_out_time = attendance.check_in_time + AUTO_CHECKOUT_AFTER
    attendance.total_work_hours = STANDARD_WORK_HOURS
    attendance.status = AttendanceLog.Status.CHECKED_OUT
    attendance.auto_checked_out = True
    attendance.capped_at_standard_hours = True
    attendance.save(
        update_fields=[
            "check_out_time",
            "total_work_hours",
            "status",
            "auto_checked_out",
            "capped_at_standard_hours",
            "updated_at",
        ]
    )
    if notify:
        _notify_forgot_checkout(attendance)
    return attendance


def process_open_attendance_records(queryset=None, *, notify=True):
    queryset = queryset or AttendanceLog.objects.filter(
        check_out_time__isnull=True,
        check_in_time__isnull=False,
    )
    processed = 0
    for attendance in queryset.iterator():
        before = attendance.check_out_time
        apply_auto_checkout_if_needed(attendance, notify=notify)
        if before is None and attendance.check_out_time is not None:
            processed += 1
    return processed


def cap_manual_checkout_hours(attendance):
    if attendance.check_out_time and attendance.check_in_time:
        actual = attendance.check_out_time - attendance.check_in_time
        if actual > STANDARD_WORK_HOURS:
            attendance.total_work_hours = STANDARD_WORK_HOURS
            attendance.capped_at_standard_hours = True
            attendance.check_out_time = attendance.check_in_time + STANDARD_WORK_HOURS
        else:
            attendance.total_work_hours = actual
    return attendance


def record_activity(employee_id):
    today = timezone.localdate()
    attendance = AttendanceLog.objects.filter(
        employee_id=employee_id,
        attendance_date=today,
        check_out_time__isnull=True,
    ).first()
    if attendance is None:
        return None

    attendance.last_activity_at = timezone.now()
    attendance.save(update_fields=["last_activity_at", "updated_at"])
    apply_auto_checkout_if_needed(attendance)
    return attendance
