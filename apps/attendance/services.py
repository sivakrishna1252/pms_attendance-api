from datetime import datetime, time, timedelta

from django.utils import timezone

from apps.common.email import send_attendance_email
from apps.common.pms_client import employee_display_name, employee_email, fetch_employee_profile

from .auto_stop import PASS_8PM, PASS_9PM, PASS_9PM_FORCED, decide_auto_stop, resolve_auto_stop_phase
from .calendar import expected_work_hours
from .constants import (
    AUTO_STOP_FINAL_HOUR,
    AUTO_STOP_FIRST_HOUR,
    OVERTIME_AFTER,
    SHIFT_START_HOUR,
    SHIFT_START_MINUTE,
    STANDARD_WORK_HOURS,
)
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
    if attendance is None or not attendance.check_in_time:
        return True, None
    if attendance.check_out_time is not None:
        available_at = next_check_in_available_at(attendance.attendance_date)
        if timezone.now() < available_at:
            return False, available_at
        return False, available_at
    return False, None


def duration_to_hours(value):
    if not value:
        return 0.0
    return round(value.total_seconds() / 3600, 2)


def _format_hours_display(value):
    total_seconds = int(value.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes:02d}m"


def worked_duration(attendance):
    if not attendance or not attendance.check_in_time:
        return timedelta(0)
    if attendance.total_work_hours is not None:
        return attendance.total_work_hours
    if attendance.check_out_time is None:
        return timezone.now() - attendance.check_in_time
    return timedelta(0)


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
            "auto_stop_pass": getattr(attendance, "auto_stop_pass", "") or "",
            "is_overtime": False,
            "overtime_hours": 0.0,
            "still_working": False,
        }

    worked = worked_duration(attendance)
    worked_hours = duration_to_hours(worked)
    expected = expected_work_hours(attendance.attendance_date)
    expected_hours = duration_to_hours(expected)
    variance_hours = round(worked_hours - expected_hours, 2)
    auto_stopped = bool(getattr(attendance, "auto_checked_out", False))
    is_overtime = worked > OVERTIME_AFTER
    overtime_hours = (
        duration_to_hours(worked - OVERTIME_AFTER) if is_overtime else 0.0
    )
    still_working = attendance.check_out_time is None
    worked_display = _format_hours_display(worked)

    if auto_stopped and is_overtime:
        variance = "auto_stop"
        variance_display = (
            f"Auto Stop · {worked_display} (Overtime — last activity until "
            f"{timezone.localtime(attendance.check_out_time).strftime('%I:%M %p') if attendance.check_out_time else '—'})"
        )
    elif auto_stopped:
        variance = "auto_stop"
        variance_display = f"Auto Stop · {worked_display} (hours from last activity)"
    elif is_overtime:
        variance = "overtime"
        variance_display = f"+{overtime_hours:.2f}h overtime"
    elif variance_hours > 0.05:
        variance = "extra_work"
        variance_display = f"+{variance_hours:.2f}h extra work"
    elif variance_hours < -0.05:
        variance = "less_work"
        variance_display = f"{abs(variance_hours):.2f}h less work today"
    else:
        variance = "on_time"
        variance_display = f"On target ({_format_hours_display(expected)})"

    return {
        "work_hours": worked_hours,
        "work_hours_display": _format_hours_display(worked),
        "expected_hours": expected_hours,
        "expected_hours_display": _format_hours_display(expected),
        "variance": variance,
        "variance_hours": variance_hours,
        "variance_display": variance_display,
        "is_capped": bool(getattr(attendance, "capped_at_standard_hours", False)),
        "auto_checked_out": bool(getattr(attendance, "auto_checked_out", False)),
        "auto_stop_pass": getattr(attendance, "auto_stop_pass", "") or "",
        "is_overtime": is_overtime,
        "overtime_hours": overtime_hours,
        "still_working": still_working,
    }


def _auto_stop_email_subject(pass_label: str) -> str:
    if pass_label == PASS_8PM:
        return "Attendance reminder: Auto Stop at 8:00 PM"
    return "Attendance reminder: Auto Stop at 9:00 PM"


def _auto_stop_email_body(attendance, *, pass_label: str) -> str:
    profile = fetch_employee_profile(attendance.employee_id)
    name = employee_display_name(profile, attendance.employee_id)
    work_end = timezone.localtime(attendance.check_out_time).strftime("%I:%M %p on %d %b %Y")
    worked_display = _format_hours_display(attendance.total_work_hours or timedelta())
    stop_label = "8:00 PM" if pass_label == PASS_8PM else "9:00 PM"

    if pass_label == PASS_8PM:
        reason = (
            "No activity was detected for at least 30 minutes before 8:00 PM. "
            "Work hours and attendance status were calculated "
            "from check-in until your last activity time."
        )
    else:
        reason = (
            "Your session was still open at 9:00 PM (including if you were still "
            "active after 8:00 PM). Work hours were calculated from check-in until "
            "your last activity or the end-of-day Auto Stop."
        )

    return (
        f"Hi {name},\n\n"
        f"{reason}\n\n"
        f"Work end (last activity): {work_end}\n"
        f"Auto Stop processed: {stop_label}\n"
        f"Work recorded today: {worked_display}\n\n"
        "Please check out manually when you leave the office.\n\n"
        "— Attendance System"
    )


def _notify_auto_stop(attendance, *, pass_label: str):
    if attendance.forgot_checkout_email_sent:
        return

    profile = fetch_employee_profile(attendance.employee_id)
    recipient = employee_email(profile)
    sent = send_attendance_email(
        subject=_auto_stop_email_subject(pass_label),
        recipient_email=recipient,
        message=_auto_stop_email_body(attendance, pass_label=pass_label),
    )
    if sent:
        attendance.forgot_checkout_email_sent = True
        attendance.save(update_fields=["forgot_checkout_email_sent", "updated_at"])


def apply_auto_checkout_if_needed(
    attendance,
    *,
    notify=True,
    phase=None,
    force_final=False,
):
    """Apply 8 PM / 9 PM Auto Stop rules based on last_activity_at."""
    if attendance is None or attendance.check_out_time is not None or not attendance.check_in_time:
        return attendance

    if attendance.attendance_date < timezone.localdate():
        notify = False

    should_stop, pass_label, work_end_at, _system_stop_at = decide_auto_stop(
        attendance,
        phase=phase,
        force_final=force_final,
    )
    if not should_stop:
        return attendance

    attendance.check_out_time = work_end_at
    attendance.total_work_hours = work_end_at - attendance.check_in_time
    attendance.status = AttendanceLog.Status.CHECKED_OUT
    attendance.auto_checked_out = True
    attendance.auto_stop_pass = pass_label
    attendance.capped_at_standard_hours = False
    attendance.save(
        update_fields=[
            "check_out_time",
            "total_work_hours",
            "status",
            "auto_checked_out",
            "auto_stop_pass",
            "capped_at_standard_hours",
            "updated_at",
        ]
    )
    if notify:
        _notify_auto_stop(attendance, pass_label=pass_label)
    return attendance


def process_open_attendance_records(queryset=None, *, notify=True, phase=None, force_final=False):
    queryset = queryset or AttendanceLog.objects.filter(
        check_out_time__isnull=True,
        check_in_time__isnull=False,
    )
    processed = 0
    for attendance in queryset.iterator():
        before = attendance.check_out_time
        apply_auto_checkout_if_needed(
            attendance,
            notify=notify,
            phase=phase,
            force_final=force_final,
        )
        if before is None and attendance.check_out_time is not None:
            processed += 1
    return processed


def run_scheduled_auto_stop_pass(*, pass_name="auto", notify=True):
    """
    pass_name: 'first' (8 PM), 'final' (9 PM), or 'auto' (detect from clock).
    """
    now_local = timezone.localtime(timezone.now())
    if pass_name == "first":
        phase = "first"
        force_final = False
    elif pass_name == "final":
        phase = "final"
        force_final = True
    else:
        phase = resolve_auto_stop_phase(now_local)
        force_final = phase == "final"

    if phase is None and pass_name == "auto":
        return 0, phase

    return (
        process_open_attendance_records(notify=notify, phase=phase, force_final=force_final),
        phase,
    )


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
