from datetime import datetime, time

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import AttendanceLog

VALID_ADMIN_DISPLAY_STATUSES = frozenset(
    {
        "Present",
        "Absent",
        "Late",
        "Half Day",
        "Overtime",
        "WFH",
        "Holiday",
    }
)


class AdminAttendanceEditError(ValueError):
    pass


def _localize_on_date(attendance_date, parsed_time: time):
    tz = timezone.get_current_timezone()
    return timezone.make_aware(datetime.combine(attendance_date, parsed_time), tz)


def parse_admin_time_value(attendance_date, value):
    """Parse HH:MM AM/PM display or ISO datetime for a given attendance date."""
    if value is None:
        return None

    text = str(value).strip()
    if not text or text == "-":
        return None

    parsed = parse_datetime(text)
    if parsed:
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed

    for fmt in ("%I:%M %p", "%H:%M"):
        try:
            parsed_time = datetime.strptime(text, fmt).time()
            return _localize_on_date(attendance_date, parsed_time)
        except ValueError:
            continue

    raise AdminAttendanceEditError(f"Invalid time value: {value}")


def apply_admin_attendance_edit(
    *,
    employee_id: int,
    attendance_date,
    admin_id: int | None,
    attendance_log_id: int | None = None,
    display_status: str | None = None,
    check_in=None,
    check_out=None,
    clear_times: bool = False,
):
    if display_status is not None:
        normalized_status = display_status.strip()
        if normalized_status and normalized_status not in VALID_ADMIN_DISPLAY_STATUSES:
            allowed = ", ".join(sorted(VALID_ADMIN_DISPLAY_STATUSES))
            raise AdminAttendanceEditError(
                f"Invalid display_status. Allowed values: {allowed}."
            )
    else:
        normalized_status = None

    attendance = None
    if attendance_log_id:
        attendance = AttendanceLog.objects.filter(
            id=attendance_log_id,
            employee_id=employee_id,
            attendance_date=attendance_date,
        ).first()
        if attendance is None:
            raise AdminAttendanceEditError("Attendance record not found.")

    if attendance is None:
        attendance, _created = AttendanceLog.objects.get_or_create(
            employee_id=employee_id,
            attendance_date=attendance_date,
            defaults={"status": AttendanceLog.Status.PRESENT},
        )

    update_fields = ["updated_at"]

    if clear_times or normalized_status == "Absent":
        if check_in is None and check_out is None:
            attendance.check_in_time = None
            attendance.check_out_time = None
            attendance.total_work_hours = None
            attendance.auto_checked_out = False
            attendance.auto_stop_pass = ""
            attendance.status = AttendanceLog.Status.PRESENT
            update_fields.extend(
                [
                    "check_in_time",
                    "check_out_time",
                    "total_work_hours",
                    "auto_checked_out",
                    "auto_stop_pass",
                    "status",
                ]
            )

    check_in_provided = check_in is not None
    check_out_provided = check_out is not None

    parsed_check_in = None
    parsed_check_out = None

    if check_in_provided:
        parsed_check_in = parse_admin_time_value(attendance_date, check_in)
        attendance.check_in_time = parsed_check_in
        update_fields.append("check_in_time")

    if check_out_provided:
        parsed_check_out = parse_admin_time_value(attendance_date, check_out)
        attendance.check_out_time = parsed_check_out
        update_fields.append("check_out_time")

    if (
        normalized_status == "Absent"
        and check_in_provided
        and check_out_provided
        and parsed_check_in is None
        and parsed_check_out is None
    ):
        attendance.total_work_hours = None
        attendance.auto_checked_out = False
        attendance.auto_stop_pass = ""
        attendance.status = AttendanceLog.Status.PRESENT
        update_fields.extend(
            ["total_work_hours", "auto_checked_out", "auto_stop_pass", "status"]
        )
    elif attendance.check_in_time and attendance.check_out_time:
        if attendance.check_out_time < attendance.check_in_time:
            raise AdminAttendanceEditError("Check-out time must be after check-in time.")
        attendance.total_work_hours = attendance.check_out_time - attendance.check_in_time
        attendance.status = AttendanceLog.Status.CHECKED_OUT
        attendance.auto_checked_out = False
        attendance.auto_stop_pass = ""
        update_fields.extend(
            ["total_work_hours", "status", "auto_checked_out", "auto_stop_pass"]
        )
    elif attendance.check_in_time and not attendance.check_out_time:
        attendance.total_work_hours = None
        attendance.status = AttendanceLog.Status.PRESENT
        update_fields.extend(["total_work_hours", "status"])

    if normalized_status is not None:
        attendance.admin_display_status = normalized_status
        update_fields.append("admin_display_status")
    elif check_in_provided or check_out_provided:
        attendance.admin_display_status = ""
        update_fields.append("admin_display_status")

    attendance.admin_overridden_at = timezone.now()
    attendance.admin_overridden_by = admin_id
    update_fields.extend(["admin_overridden_at", "admin_overridden_by"])

    attendance.save(update_fields=list(dict.fromkeys(update_fields)))
    return attendance
