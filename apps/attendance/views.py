from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.permissions import IsAttendanceAdmin
from apps.common.employee_profiles import resolver_from_request, seed_staff_resolver
from apps.common.pms_client import staff_users_from_pms
from apps.leaves.models import LeaveRequest

from .calendar import (
    holiday_info_for_date,
    is_company_holiday,
    is_working_day,
    iter_dates,
    resolve_staff_attendance_history_window,
    shift_label_for_date,
)

from .constants import (
    AUTO_STOP_FIRST_HOUR,
    AUTO_STOP_FIRST_MINUTE,
    LATE_CHECK_IN_HOUR,
    LATE_CHECK_IN_MINUTE,
)
from .models import AttendanceLog
from .serializers import AttendanceLogSerializer
from .status_rules import resolve_work_day_status
from .admin_status_override import AdminAttendanceEditError, apply_admin_attendance_edit
from .auto_stop import resolve_auto_stop_phase
from .services import (
    apply_auto_checkout_if_needed,
    can_check_in_now,
    next_check_in_available_at,
    process_open_attendance_records,
    record_activity,
    work_analysis,
)

DEFAULT_OFFICE_LOCATION = "Apparatus solutions pune"
DEFAULT_SHIFT_LABEL = "Standard Shift: 9:00 AM - 6:00 PM (9 hours)"


def employee_id_from_request(request):
    employee_id = getattr(request.user, "employee_id", None)
    if employee_id is None:
        raise AuthenticationFailed("JWT token does not contain user_id.")
    return int(employee_id)


def format_duration(value):
    if not value:
        return "-"
    total_seconds = int(value.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes:02d}m"


def format_time(value):
    if not value:
        return "-"
    return timezone.localtime(value).strftime("%I:%M %p")


def is_late_check_in(check_in_time):
    """Late only when check-in is after 11:00 AM local time."""
    if not check_in_time:
        return False
    local = timezone.localtime(check_in_time)
    check_in_minutes = local.hour * 60 + local.minute
    threshold_minutes = LATE_CHECK_IN_HOUR * 60 + LATE_CHECK_IN_MINUTE
    return check_in_minutes > threshold_minutes


def is_late_from_check_in_display(check_in_display):
    """Parse check-in display (e.g. 12:50 PM) for late rule."""
    text = (check_in_display or "").strip()
    if not text or text == "-":
        return False
    try:
        parsed = datetime.strptime(text, "%I:%M %p")
    except ValueError:
        return False
    check_in_minutes = parsed.hour * 60 + parsed.minute
    threshold_minutes = LATE_CHECK_IN_HOUR * 60 + LATE_CHECK_IN_MINUTE
    return check_in_minutes > threshold_minutes


def resolve_record_is_late(record):
    if record.get("is_late") is True:
        return True
    check_in_time = record.get("check_in_time")
    if check_in_time:
        parsed = parse_datetime(check_in_time)
        if parsed:
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            if is_late_check_in(parsed):
                return True
    return is_late_from_check_in_display(record.get("check_in"))


def apply_display_status(record):
    admin_status = (record.get("admin_display_status") or "").strip()
    if admin_status:
        record["display_status"] = admin_status
        record["status_label"] = admin_status
        return record

    status = record.get("status")
    if status == "HOLIDAY":
        record["display_status"] = "Holiday"
    elif status == "WFH":
        record["display_status"] = "WFH"
    elif status == "ON_LEAVE":
        record["display_status"] = "Leave"
    elif status == "ABSENT":
        record["display_status"] = "Absent"
    elif record.get("has_check_in"):
        record["is_late"] = resolve_record_is_late(record)
        analysis = record.get("work_analysis") or {}
        worked = analysis.get("work_hours")
        total_work_hours = None
        if isinstance(worked, (int, float)):
            total_work_hours = timedelta(hours=worked)

        check_in_dt = None
        raw_check_in = record.get("check_in_time")
        if raw_check_in:
            parsed = parse_datetime(raw_check_in)
            if parsed:
                if timezone.is_naive(parsed):
                    parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
                check_in_dt = parsed

        day = parse_date(record.get("date")) or timezone.localdate()
        has_check_out = record.get("check_out") not in (None, "-", "")
        record["display_status"] = resolve_work_day_status(
            day=day,
            check_in_time=check_in_dt or True,
            total_work_hours=total_work_hours,
            is_late=record["is_late"],
            auto_checked_out=bool(record.get("auto_checked_out")),
            has_check_out=has_check_out,
        )
    else:
        record["display_status"] = record.get("display_status") or "—"
    record["status_label"] = record["display_status"]
    return record


def close_stale_open_sessions(employee_id):
    today = timezone.localdate()
    stale = AttendanceLog.objects.filter(
        employee_id=employee_id,
        check_out_time__isnull=True,
        check_in_time__isnull=False,
        attendance_date__lt=today,
    )
    for attendance in stale:
        apply_auto_checkout_if_needed(attendance)


def get_today_attendance(employee_id, *, apply_auto=True):
    today = timezone.localdate()
    attendance = AttendanceLog.objects.filter(employee_id=employee_id, attendance_date=today).first()
    if attendance and not attendance.check_in_time:
        attendance.delete()
        return None
    if apply_auto and attendance:
        attendance = apply_auto_checkout_if_needed(attendance)
    return attendance


def next_check_in_gate(employee_id, today):
    """Block check-in until next working day 9:00 AM after yesterday's check-out."""
    if employee_id is None:
        return None
    yesterday = today - timedelta(days=1)
    yesterday_log = AttendanceLog.objects.filter(
        employee_id=employee_id,
        attendance_date=yesterday,
    ).first()
    if yesterday_log and yesterday_log.check_out_time:
        available_at = next_check_in_available_at(yesterday)
        if timezone.now() < available_at:
            return available_at
    return None


def format_attendance_record(attendance, resolver=None):
    analysis = work_analysis(attendance)
    employee = (
        resolver.employee_block(attendance.employee_id)
        if resolver
        else {
            "id": attendance.employee_id,
            "name": f"Employee {attendance.employee_id}",
            "department": "—",
            "role": "—",
            "initials": f"E{attendance.employee_id}",
            "email": "",
        }
    )
    record = {
        "id": attendance.id,
        "employee_id": attendance.employee_id,
        "employee_name": employee["name"],
        "employee_department": employee["department"],
        "employee_role": employee.get("role", "—"),
        "employee_initials": employee["initials"],
        "employee": employee,
        "date": attendance.attendance_date.isoformat(),
        "day": attendance.attendance_date.strftime("%A"),
        "status": attendance.status,
        "status_label": attendance.get_status_display(),
        "check_in": format_time(attendance.check_in_time),
        "check_in_time": (
            timezone.localtime(attendance.check_in_time).isoformat()
            if attendance.check_in_time
            else None
        ),
        "check_out": format_time(attendance.check_out_time),
        "duration": format_duration(attendance.total_work_hours),
        "location": DEFAULT_OFFICE_LOCATION,
        "auto_checked_out": analysis["auto_checked_out"],
        "auto_stop_pass": getattr(attendance, "auto_stop_pass", "") or "",
        "last_activity_at": (
            timezone.localtime(attendance.last_activity_at).isoformat()
            if attendance.last_activity_at
            else None
        ),
        "last_activity_display": (
            format_time(attendance.last_activity_at)
            if attendance.last_activity_at
            else "-"
        ),
        "has_check_in": bool(attendance.check_in_time),
        "is_late": is_late_check_in(attendance.check_in_time),
        "admin_display_status": getattr(attendance, "admin_display_status", "") or None,
        "work_analysis": analysis,
        "raw": AttendanceLogSerializer(attendance).data,
    }
    record = apply_display_status(record)
    if attendance.auto_checked_out:
        stop_pass = getattr(attendance, "auto_stop_pass", "") or "8PM"
        if record.get("display_status") == "Overtime":
            record["attendance_type"] = f"Auto Stop ({stop_pass}) · Overtime"
        else:
            record["attendance_type"] = f"Auto Stop ({stop_pass})"
    elif not attendance.check_out_time and resolve_auto_stop_phase():
        record["attendance_type"] = "Still working"
    return record


def build_today_payload(attendance, *, employee_id=None):
    has_check_in = bool(attendance and attendance.check_in_time)
    checked_in = bool(
        attendance and attendance.check_in_time and not attendance.check_out_time
    )
    checked_out = bool(
        attendance and attendance.check_in_time and attendance.check_out_time
    )
    can_check_in, next_available = can_check_in_now(attendance)
    today = timezone.localdate()
    check_in_gate = next_check_in_gate(employee_id, today)
    if check_in_gate and not has_check_in:
        can_check_in = False
        if next_available is None or check_in_gate > next_available:
            next_available = check_in_gate
    is_holiday, holiday_name = holiday_info_for_date(today)
    on_leave_ids, _wfh_ids = leave_flags_for_date(today)
    on_approved_leave = employee_id is not None and int(employee_id) in on_leave_ids
    access_blocked = is_holiday or on_approved_leave
    access_blocked_reason = (
        "holiday"
        if is_holiday
        else "approved_leave"
        if on_approved_leave
        else None
    )
    access_blocked_message = None
    if is_holiday:
        access_blocked_message = (
            f"Today is a company holiday ({holiday_name}). "
            "Check-in and check-out are not available."
        )
    elif on_approved_leave:
        access_blocked_message = (
            "You have approved leave today. Check-in and check-out are not available."
        )

    wait_message = None
    if access_blocked:
        action = "wait"
        can_check_in = False
        wait_message = access_blocked_message
    elif checked_out:
        action = "done"
    elif checked_in:
        action = "check_out"
    elif can_check_in:
        action = "check_in"
    else:
        action = "wait"
        wait_message = (
            f"Check-in opens at {format_time(next_available)}."
            if next_available
            else "Check-in is not available right now."
        )

    analysis = work_analysis(attendance) if attendance else None

    today_log_display_status = None
    if attendance and has_check_in:
        if checked_out:
            worked = (analysis or {}).get("work_hours")
            total_work_hours = attendance.total_work_hours
            if total_work_hours is None and isinstance(worked, (int, float)):
                total_work_hours = timedelta(hours=worked)
            today_log_display_status = resolve_work_day_status(
                day=today,
                check_in_time=attendance.check_in_time,
                total_work_hours=total_work_hours,
                is_late=is_late_check_in(attendance.check_in_time),
                auto_checked_out=bool(attendance.auto_checked_out),
                has_check_out=True,
            )
        else:
            today_log_display_status = resolve_work_day_status(
                day=today,
                check_in_time=attendance.check_in_time,
                total_work_hours=None,
                is_late=is_late_check_in(attendance.check_in_time),
                auto_checked_out=False,
                has_check_out=False,
            )

    return {
        "date": today.isoformat(),
        "is_holiday": is_holiday,
        "on_approved_leave": on_approved_leave,
        "holiday_name": holiday_name or None,
        "access_blocked": access_blocked,
        "access_blocked_reason": access_blocked_reason,
        "access_blocked_message": access_blocked_message,
        "wait_message": wait_message,
        "office": {
            "name": DEFAULT_OFFICE_LOCATION,
            "shift": shift_label_for_date(today),
            "status": "Holiday" if is_holiday else "In Office",
            "is_inside_office": not is_holiday,
        },
        "state": {
            "checked_in": checked_in,
            "checked_out": checked_out,
            "next_action": action,
            "can_check_in": can_check_in and not checked_in and not access_blocked,
            "can_check_out": checked_in and not checked_out and not access_blocked,
            "next_check_in_at": next_available.isoformat() if next_available else None,
            "next_check_in_at_display": format_time(next_available) if next_available else None,
        },
        "today_log": {
            "check_in": format_time(attendance.check_in_time) if has_check_in else None,
            "check_out": format_time(attendance.check_out_time) if checked_out else None,
            "duration": format_duration(attendance.total_work_hours) if checked_out else None,
            "display_status": today_log_display_status,
            "work_analysis": analysis,
            "timeline": (
                [
                    {
                        "label": "Checked In",
                        "time": format_time(attendance.check_in_time),
                        "location": DEFAULT_OFFICE_LOCATION,
                    }
                ]
                + (
                    [
                        {
                            "label": "Auto Stop" if attendance.auto_checked_out else "Checked Out",
                            "time": format_time(attendance.check_out_time),
                            "location": DEFAULT_OFFICE_LOCATION,
                        }
                    ]
                    if checked_out
                    else [{"label": "Awaiting Check Out", "time": None, "location": None}]
                )
                if has_check_in
                else []
            ),
        },
        "attendance": AttendanceLogSerializer(attendance).data if attendance else None,
    }


def build_history_summary(queryset):
    status_counts = {
        item["status"]: item["total"]
        for item in queryset.values("status").annotate(total=Count("id")).order_by("status")
    }
    return {
        "present": status_counts.get(AttendanceLog.Status.PRESENT, 0)
        + status_counts.get(AttendanceLog.Status.CHECKED_OUT, 0),
        "absent": 0,
        "leave": 0,
        "holiday": 0,
        "wfh": 0,
        "checked_out": status_counts.get(AttendanceLog.Status.CHECKED_OUT, 0),
        "auto_checked_out": queryset.filter(auto_checked_out=True).count(),
        "total_records": queryset.count(),
    }


PRESENT_DISPLAY_STATUSES = frozenset(
    {"Present", "Late", "Overtime", "Checked Out"}
)


def absent_marking_time_reached(day):
    """Employee absent status is only shown after 8:00 PM on that day."""
    today = timezone.localdate()
    if day < today:
        return True
    if day > today:
        return False
    now = timezone.localtime()
    cutoff = now.replace(
        hour=AUTO_STOP_FIRST_HOUR,
        minute=AUTO_STOP_FIRST_MINUTE,
        second=0,
        microsecond=0,
    )
    return now >= cutoff


def record_has_check_out(record):
    return record.get("check_out") not in (None, "-", "")


def apply_staff_history_display(record, day, employee_id):
    """
    Simplified staff dashboard statuses (Employee + BA):
    - Present: check-in and check-out both done
    - Leave / WFH: approved permission
    - Holiday: admin calendar + company off days (Sunday, 1st/3rd Saturday)
    - Absent: no check-in, no leave, working day, after 8 PM
    """
    on_leave_ids, wfh_ids = leave_flags_for_date(day)
    is_holiday, holiday_name = holiday_info_for_date(day)
    has_check_in = bool(
        record.get("has_check_in") or record.get("check_in") not in (None, "-", "")
    )
    has_check_out = record_has_check_out(record)
    record["has_check_in"] = has_check_in

    if employee_id in on_leave_ids and not has_check_in:
        record["display_status"] = "Leave"
        record["status_label"] = "Leave"
        record["attendance_type"] = "Leave"
        return record

    if employee_id in wfh_ids:
        record["display_status"] = "WFH"
        record["status_label"] = "WFH"
        record["attendance_type"] = "WFH"
        return record

    if is_holiday and not has_check_in:
        record["display_status"] = "Holiday"
        record["status_label"] = "Holiday"
        record["attendance_type"] = "Holiday"
        record["holiday_name"] = holiday_name
        return record

    if has_check_in and has_check_out:
        analysis = record.get("work_analysis") or {}
        worked = analysis.get("work_hours")
        total_work_hours = None
        if isinstance(worked, (int, float)):
            total_work_hours = timedelta(hours=worked)

        check_in_dt = None
        raw_check_in = record.get("check_in_time")
        if raw_check_in:
            parsed = parse_datetime(raw_check_in)
            if parsed:
                if timezone.is_naive(parsed):
                    parsed = timezone.make_aware(
                        parsed, timezone.get_current_timezone()
                    )
                check_in_dt = parsed

        record["display_status"] = resolve_work_day_status(
            day=day,
            check_in_time=check_in_dt or True,
            total_work_hours=total_work_hours,
            is_late=resolve_record_is_late(record),
            auto_checked_out=bool(record.get("auto_checked_out")),
            has_check_out=True,
        )
        record["status_label"] = record["display_status"]
        return record

    if has_check_in and not has_check_out:
        is_late = resolve_record_is_late(record)
        record["display_status"] = resolve_work_day_status(
            day=day,
            check_in_time=True,
            total_work_hours=None,
            is_late=is_late,
            auto_checked_out=False,
            has_check_out=False,
        )
        record["status_label"] = record["display_status"]
        return record

    if is_working_day(day) and not has_check_in and absent_marking_time_reached(day):
        record["display_status"] = "Absent"
        record["status_label"] = "Absent"
        record["attendance_type"] = "Absent"
        return record

    # Today before 8 PM (or check-in without check-out): not shown yet
    record["display_status"] = None
    record["status_label"] = None
    return record


def summarize_staff_history_records(records):
    present = sum(
        1
        for item in records
        if item.get("display_status") in {"Present", "Late", "Overtime"}
    )
    half_day = sum(
        1
        for item in records
        if item.get("display_status") in {"Half Day", "Auto Stop Half Day"}
    )
    absent = sum(1 for item in records if item.get("display_status") == "Absent")
    leave = sum(1 for item in records if item.get("display_status") == "Leave")
    holiday = sum(1 for item in records if item.get("display_status") == "Holiday")
    wfh = sum(1 for item in records if item.get("display_status") == "WFH")
    checked_out = sum(
        1
        for item in records
        if item.get("check_out") not in (None, "-", "")
    )
    auto_checked_out = sum(1 for item in records if item.get("auto_checked_out"))
    return {
        "present": present,
        "half_day": half_day,
        "absent": absent,
        "leave": leave,
        "holiday": holiday,
        "wfh": wfh,
        "checked_out": checked_out,
        "auto_checked_out": auto_checked_out,
        "total_records": len(records),
    }


def summarize_history_records(records):
    present = sum(
        1
        for item in records
        if item.get("has_check_in")
        or item.get("display_status") in PRESENT_DISPLAY_STATUSES
    )
    absent = sum(1 for item in records if item.get("display_status") == "Absent")
    leave = sum(
        1
        for item in records
        if item.get("display_status") == "Leave" or item.get("status") == "ON_LEAVE"
    )
    holiday = sum(1 for item in records if item.get("display_status") == "Holiday")
    wfh = sum(1 for item in records if item.get("display_status") == "WFH")
    checked_out = sum(
        1
        for item in records
        if item.get("check_out") not in (None, "-", "")
    )
    auto_checked_out = sum(1 for item in records if item.get("auto_checked_out"))
    return {
        "present": present,
        "absent": absent,
        "leave": leave,
        "holiday": holiday,
        "wfh": wfh,
        "checked_out": checked_out,
        "auto_checked_out": auto_checked_out,
        "total_records": len(records),
    }


def build_staff_history_payload(employee_id, *, start_date=None, end_date=None):
    today = timezone.localdate()
    window_start, window_end = resolve_staff_attendance_history_window(today=today)

    if start_date:
        range_start = max(start_date, window_start)
    else:
        range_start = window_start

    range_end = min(end_date or window_end, window_end, today)

    logs_qs = AttendanceLog.objects.filter(
        employee_id=employee_id,
        attendance_date__gte=range_start,
        attendance_date__lte=range_end,
    )
    logs_by_date = {log.attendance_date: log for log in logs_qs.order_by("attendance_date")}

    records = []
    seen_dates = set()
    for day in iter_dates(range_start, range_end):
        if day in logs_by_date:
            record = format_attendance_record(logs_by_date[day])
        else:
            record = format_no_checkin_record(employee_id, day, None, as_absent=False)

        apply_staff_history_display(record, day, employee_id)
        if not record.get("display_status"):
            continue
        if day in seen_dates:
            continue
        seen_dates.add(day)
        records.append(record)

    records.sort(key=lambda item: item["date"], reverse=True)
    return {
        "records": records,
        "summary": summarize_staff_history_records(records),
        "period": {
            "start_date": range_start.isoformat(),
            "end_date": range_end.isoformat(),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "clear_day": window_end.day,
        },
    }


def approved_leaves_on_date(attendance_date):
    return LeaveRequest.objects.filter(
        status=LeaveRequest.Status.APPROVED,
        from_date__lte=attendance_date,
        to_date__gte=attendance_date,
    )


def leave_flags_for_date(attendance_date):
    from apps.leaves.services import is_leave_deductible_day

    on_leave_ids = set()
    wfh_ids = set()
    # Include recent leaves so bridge working Saturdays after to_date are picked up.
    lookback = attendance_date - timedelta(days=6)
    leaves = LeaveRequest.objects.filter(
        status=LeaveRequest.Status.APPROVED,
        from_date__lte=attendance_date,
        to_date__gte=lookback,
    ).only("employee_id", "leave_type", "from_date", "to_date")
    for leave in leaves:
        if not is_leave_deductible_day(
            attendance_date, leave.from_date, leave.to_date
        ):
            continue
        if leave.leave_type == LeaveRequest.LeaveType.WFH:
            wfh_ids.add(leave.employee_id)
        else:
            on_leave_ids.add(leave.employee_id)
    return on_leave_ids, wfh_ids


def attendance_day_restrictions(employee_id, attendance_date=None):
    """
    Returns (allowed, message, reason).
    reason: None | 'holiday' | 'approved_leave'
    Blocks check-in and check-out on company holidays and approved leave (non-WFH).
    """
    attendance_date = attendance_date or timezone.localdate()
    is_holiday, holiday_name = holiday_info_for_date(attendance_date)
    if is_holiday:
        return (
            False,
            f"Today is a company holiday ({holiday_name}). Check-in and check-out are not allowed.",
            "holiday",
        )

    on_leave_ids, _wfh_ids = leave_flags_for_date(attendance_date)
    if int(employee_id) in on_leave_ids:
        return (
            False,
            "You have approved leave today. Check-in and check-out are not allowed.",
            "approved_leave",
        )

    return True, None, None


def attendance_access_denied_response(employee_id, *, status_code=status.HTTP_400_BAD_REQUEST):
    allowed, message, _reason = attendance_day_restrictions(employee_id)
    if allowed:
        return None
    return Response(
        {
            "success": False,
            "message": message,
            "data": build_today_payload(None, employee_id=employee_id),
        },
        status=status_code,
    )


def format_leave_only_record(employee_id, attendance_date, resolver, *, is_wfh=False):
    employee = (
        resolver.employee_block(employee_id)
        if resolver
        else {
            "id": employee_id,
            "name": f"Employee {employee_id}",
            "department": "—",
            "role": "—",
            "initials": f"E{employee_id}",
            "email": "",
        }
    )
    status = "WFH" if is_wfh else "ON_LEAVE"
    status_label = "WFH" if is_wfh else "Leave"
    display_status = status_label
    return {
        "id": f"virtual-{employee_id}-{attendance_date.isoformat()}",
        "employee_id": employee_id,
        "employee_name": employee["name"],
        "employee_department": employee["department"],
        "employee_role": employee.get("role", "—"),
        "employee_initials": employee["initials"],
        "employee": employee,
        "date": attendance_date.isoformat(),
        "day": attendance_date.strftime("%A"),
        "status": status,
        "status_label": status_label,
        "display_status": display_status,
        "check_in": "-",
        "check_out": "-",
        "duration": "-",
        "location": "—",
        "auto_checked_out": False,
        "has_check_in": False,
        "attendance_type": "WFH" if is_wfh else "Leave",
        "is_late": False,
        "work_analysis": work_analysis(None),
        "raw": None,
    }


def format_no_checkin_record(employee_id, attendance_date, resolver, *, as_absent=False):
    employee = (
        resolver.employee_block(employee_id)
        if resolver
        else {
            "id": employee_id,
            "name": f"Employee {employee_id}",
            "department": "—",
            "role": "—",
            "initials": f"E{employee_id}",
            "email": "",
        }
    )
    status = "ABSENT" if as_absent else "NOT_CHECKED_IN"
    display_status = "Absent" if as_absent else "—"
    return {
        "id": f"virtual-{employee_id}-{attendance_date.isoformat()}",
        "employee_id": employee_id,
        "employee_name": employee["name"],
        "employee_department": employee["department"],
        "employee_role": employee.get("role", "—"),
        "employee_initials": employee["initials"],
        "employee": employee,
        "date": attendance_date.isoformat(),
        "day": attendance_date.strftime("%A"),
        "status": status,
        "status_label": display_status,
        "display_status": display_status,
        "check_in": "-",
        "check_out": "-",
        "duration": "-",
        "location": "—",
        "auto_checked_out": False,
        "has_check_in": False,
        "attendance_type": "Absent" if as_absent else "—",
        "is_late": False,
        "work_analysis": work_analysis(None),
        "raw": None,
    }


def format_holiday_record(employee_id, attendance_date, resolver, *, holiday_name):
    employee = (
        resolver.employee_block(employee_id)
        if resolver
        else {
            "id": employee_id,
            "name": f"Employee {employee_id}",
            "department": "—",
            "role": "—",
            "initials": f"E{employee_id}",
            "email": "",
        }
    )
    return {
        "id": f"virtual-{employee_id}-{attendance_date.isoformat()}",
        "employee_id": employee_id,
        "employee_name": employee["name"],
        "employee_department": employee["department"],
        "employee_role": employee.get("role", "—"),
        "employee_initials": employee["initials"],
        "employee": employee,
        "date": attendance_date.isoformat(),
        "day": attendance_date.strftime("%A"),
        "status": "HOLIDAY",
        "status_label": "Holiday",
        "display_status": "Holiday",
        "holiday_name": holiday_name,
        "check_in": "-",
        "check_out": "-",
        "duration": "-",
        "location": "—",
        "auto_checked_out": False,
        "has_check_in": False,
        "attendance_type": "Holiday",
        "is_late": False,
        "work_analysis": work_analysis(None),
        "raw": None,
    }


def annotate_attendance_day_context(record, *, on_leave_ids, wfh_ids, is_holiday=False, holiday_name=""):
    employee_id = record["employee_id"]
    has_check_in = record.get("check_in") not in (None, "-", "")
    record["has_check_in"] = has_check_in

    if employee_id in wfh_ids:
        record["status"] = "WFH"
        record["status_label"] = "WFH"
        record["attendance_type"] = "WFH"
    elif is_holiday and not has_check_in:
        record["status"] = "HOLIDAY"
        record["status_label"] = "Holiday"
        record["attendance_type"] = "Holiday"
        record["holiday_name"] = holiday_name
    elif record.get("auto_checked_out"):
        record["attendance_type"] = "Auto Stop"
    else:
        record["attendance_type"] = record.get("attendance_type") or "Office"
    return apply_display_status(record)


def average_work_hours_summary(records):
    hours = []
    for record in records:
        if not record.get("has_check_in"):
            continue
        if record.get("check_out") in (None, "-", ""):
            continue
        worked = record.get("work_analysis", {}).get("work_hours")
        if isinstance(worked, (int, float)) and worked > 0:
            hours.append(worked)
    if not hours:
        return {"avg_work_hours": 0.0, "avg_work_hours_display": "0h"}
    avg = sum(hours) / len(hours)
    return {
        "avg_work_hours": round(avg, 2),
        "avg_work_hours_display": f"{avg:.1f}h",
    }


def summarize_admin_day_counts(records_by_employee, *, on_leave_ids, wfh_ids, is_holiday):
    """
    Dashboard card totals for admin attendance:
    - present: on-time check-ins (display_status Present)
    - late_check_ins: check-ins after 11:00 AM (display_status Late)
    - absent: display_status Absent only (matches table filter; not no-check-in)
    Checked-in employees are never counted as absent.
    """
    items = records_by_employee.values()
    present_count = sum(
        1 for item in items if item.get("display_status") == "Present"
    )
    late_count = sum(1 for item in items if item.get("display_status") == "Late")
    checked_in_count = sum(1 for item in items if item.get("has_check_in"))
    absent_count = sum(
        1 for item in items if item.get("display_status") == "Absent"
    )

    return present_count, late_count, absent_count, checked_in_count


def apply_check_in_filter(records, check_in_filter):
    normalized = (check_in_filter or "all").lower()
    if normalized in {"", "all"}:
        return [
            item
            for item in records
            if item.get("has_check_in")
            or item.get("status") in ("ON_LEAVE", "WFH", "HOLIDAY")
        ]
    if normalized == "checked_in":
        return [item for item in records if item.get("has_check_in")]
    if normalized in {"without_check_in", "no_check_in", "not_checked_in"}:
        return [
            item
            for item in records
            if not item.get("has_check_in")
            and item.get("status") not in ("ON_LEAVE", "WFH", "HOLIDAY")
        ]
    return records


def apply_status_filter(records, status_filter):
    normalized = (status_filter or "all").lower()
    targets = {
        "late": "Late",
        "present": "Present",
        "absent": "Absent",
        "leave": "Leave",
        "wfh": "WFH",
    }
    target = targets.get(normalized)
    if not target:
        return records
    return [item for item in records if item.get("display_status") == target]


def parse_staff_ids_param(raw):
    if not raw:
        return []
    ids = []
    for part in str(raw).split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return ids


def _resolve_staff_employee_ids(resolver, *, auth_token=None, staff_ids=None, employee_id=None):
    if employee_id:
        return [int(employee_id)]
    if staff_ids:
        return list(staff_ids)

    if resolver:
        seed_staff_resolver(resolver, token=auth_token)
        if resolver._cache:
            return sorted(resolver._cache.keys())

    users = staff_users_from_pms()
    if users:
        return sorted(int(user["id"]) for user in users if user.get("id") is not None)
    return []


def build_admin_day_payload(
    attendance_date,
    *,
    resolver=None,
    employee_id=None,
    check_in_filter="all",
    status_filter="all",
    auth_token=None,
    staff_ids=None,
):
    # One bulk staff lookup per request — avoids per-employee PMS HTTP calls (10s each).
    if resolver:
        seed_staff_resolver(resolver, token=auth_token)

    is_holiday, holiday_name = holiday_info_for_date(attendance_date)
    today_logs = AttendanceLog.objects.filter(attendance_date=attendance_date).order_by("-check_in_time")
    if employee_id:
        today_logs = today_logs.filter(employee_id=employee_id)

    on_leave_ids, wfh_ids = leave_flags_for_date(attendance_date)
    records_by_employee = {}

    for attendance in today_logs:
        record = format_attendance_record(attendance, resolver)
        annotate_attendance_day_context(
            record,
            on_leave_ids=on_leave_ids,
            wfh_ids=wfh_ids,
            is_holiday=is_holiday,
            holiday_name=holiday_name,
        )
        records_by_employee[attendance.employee_id] = record

    target_employee_ids = _resolve_staff_employee_ids(
        resolver,
        auth_token=auth_token,
        staff_ids=staff_ids,
        employee_id=employee_id,
    )
    if not target_employee_ids:
        target_employee_ids = sorted(
            set(records_by_employee.keys()) | wfh_ids
        )

    for emp_id in target_employee_ids:
        if emp_id in records_by_employee:
            continue
        if emp_id in on_leave_ids:
            # Approved leave is shown in Leave Management — admin attendance lists check-ins only.
            continue
        elif emp_id in wfh_ids:
            records_by_employee[emp_id] = format_leave_only_record(
                emp_id,
                attendance_date,
                resolver,
                is_wfh=True,
            )
        elif is_holiday:
            records_by_employee[emp_id] = format_holiday_record(
                emp_id,
                attendance_date,
                resolver,
                holiday_name=holiday_name,
            )
        else:
            records_by_employee[emp_id] = format_no_checkin_record(
                emp_id,
                attendance_date,
                resolver,
                as_absent=False,
            )

    records = sorted(
        records_by_employee.values(),
        key=lambda item: (item.get("employee_name") or "").lower(),
    )
    records = apply_check_in_filter(records, check_in_filter)
    records = apply_status_filter(records, status_filter)

    present_count, late_count, absent_count, checked_in_count = summarize_admin_day_counts(
        records_by_employee,
        on_leave_ids=on_leave_ids,
        wfh_ids=wfh_ids,
        is_holiday=is_holiday,
    )
    checked_out_count = sum(
        1
        for item in records_by_employee.values()
        if item.get("check_out") not in (None, "-", "")
    )
    still_working_count = sum(
        1
        for item in records_by_employee.values()
        if item.get("has_check_in") and item.get("check_out") in (None, "-", "")
    )
    extra_work_count = sum(
        1 for item in records_by_employee.values() if item["work_analysis"]["variance"] == "extra_work"
    )
    overtime_count = sum(
        1
        for item in records_by_employee.values()
        if item.get("display_status") == "Overtime"
        or item["work_analysis"].get("is_overtime")
    )
    less_work_count = sum(
        1 for item in records_by_employee.values() if item["work_analysis"]["variance"] == "less_work"
    )
    holiday_count = sum(
        1 for item in records_by_employee.values() if item.get("display_status") == "Holiday"
    )
    if employee_id:
        emp_record = records_by_employee.get(int(employee_id), {})
        present_count = 1 if emp_record.get("display_status") == "Present" else 0
        late_count = 1 if emp_record.get("display_status") == "Late" else 0
        absent_count = 1 if emp_record.get("display_status") == "Absent" else 0
        holiday_count = (
            1 if emp_record.get("display_status") == "Holiday" else 0
        )
    wfh_count = len(wfh_ids)
    if employee_id:
        wfh_count = 1 if int(employee_id) in wfh_ids else 0

    avg_hours = average_work_hours_summary(records_by_employee.values())

    return {
        "records": records,
        "day_context": {
            "is_holiday": is_holiday,
            "holiday_name": holiday_name,
            "can_mark_holiday": not is_holiday,
        },
        "summary": {
            "present": present_count,
            "absent": absent_count,
            "checked_in": checked_in_count,
            "leave": len(on_leave_ids) if not employee_id else (1 if int(employee_id) in on_leave_ids else 0),
            "holiday": holiday_count,
            "wfh": wfh_count,
            "on_leave_employee_ids": sorted(on_leave_ids),
            "wfh_employee_ids": sorted(wfh_ids),
            "checked_in_employee_ids": sorted(
                emp_id
                for emp_id, item in records_by_employee.items()
                if item.get("has_check_in")
            ),
            "checked_out": checked_out_count,
            "still_working": still_working_count,
            "extra_work_days": extra_work_count,
            "overtime_days": overtime_count,
            "less_work_days": less_work_count,
            "late_check_ins": late_count,
            **avg_hours,
        },
        "attendance_today": {
            "checked_in_count": checked_in_count,
            "checked_out_count": checked_out_count,
            "still_working_count": still_working_count,
            "extra_work_count": extra_work_count,
            "overtime_count": overtime_count,
            "less_work_count": less_work_count,
            "late_check_ins": late_count,
            "records": records,
        },
    }


class CheckInAPIView(APIView):
    @extend_schema(
        tags=["Attendance"],
        summary="Employee check-in",
        description="Creates today's attendance record. Only one check-in/check-out cycle per calendar day.",
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "example": "Apparatus solutions pune"}
                },
            }
        },
        responses={201: AttendanceLogSerializer},
        examples=[
            OpenApiExample(
                "Check-in body",
                value={"location": "Apparatus solutions pune"},
                request_only=True,
            )
        ],
    )
    def post(self, request):
        employee_id = employee_id_from_request(request)
        today = timezone.localdate()
        close_stale_open_sessions(employee_id)

        denied = attendance_access_denied_response(employee_id)
        if denied:
            return denied

        existing = AttendanceLog.objects.filter(employee_id=employee_id, attendance_date=today).first()
        if existing and not existing.check_in_time:
            existing.delete()
            existing = None
        if existing:
            existing = apply_auto_checkout_if_needed(existing)
            allowed, available_at = can_check_in_now(existing)
            if existing.check_out_time is not None:
                return Response(
                    {
                        "success": False,
                        "message": (
                            "You already completed check-in and check-out today. "
                            f"Next check-in opens at {format_time(available_at)}."
                        ),
                        "data": build_today_payload(existing, employee_id=employee_id),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {
                    "success": False,
                    "message": "Already checked in today. Please check out first.",
                    "data": build_today_payload(existing, employee_id=employee_id),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        yesterday = today - timedelta(days=1)
        yesterday_log = AttendanceLog.objects.filter(
            employee_id=employee_id,
            attendance_date=yesterday,
        ).first()
        if yesterday_log and yesterday_log.check_out_time:
            available_at = next_check_in_available_at(yesterday)
            if timezone.now() < available_at:
                return Response(
                    {
                        "success": False,
                        "message": f"Check-in opens at {format_time(available_at)} (next working day 9:00 AM).",
                        "data": build_today_payload(None, employee_id=employee_id),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            with transaction.atomic():
                attendance = AttendanceLog.objects.create(
                    employee_id=employee_id,
                    attendance_date=today,
                    check_in_time=timezone.now(),
                    last_activity_at=timezone.now(),
                    status=AttendanceLog.Status.PRESENT,
                )
        except IntegrityError:
            attendance = AttendanceLog.objects.filter(employee_id=employee_id, attendance_date=today).first()
            return Response(
                {
                    "success": False,
                    "message": "Already checked in today.",
                    "data": build_today_payload(attendance, employee_id=employee_id),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "success": True,
                "message": "Checked in successfully.",
                "data": build_today_payload(attendance, employee_id=employee_id),
            },
            status=status.HTTP_201_CREATED,
        )


class CheckOutAPIView(APIView):
    @extend_schema(
        tags=["Attendance"],
        summary="Employee check-out",
        description="Closes today's attendance. Only allowed once per day after check-in.",
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "example": "Apparatus solutions pune"}
                },
            }
        },
        responses={200: AttendanceLogSerializer},
    )
    def post(self, request):
        employee_id = employee_id_from_request(request)
        denied = attendance_access_denied_response(employee_id)
        if denied:
            return denied

        attendance = get_today_attendance(employee_id)

        if attendance is None:
            return Response(
                {"success": False, "message": "Check-in is required before check-out.", "data": None},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if attendance.check_out_time is not None:
            return Response(
                {
                    "success": False,
                    "message": "Already checked out today. Next check-in is available tomorrow from 9:00 AM.",
                    "data": build_today_payload(attendance, employee_id=employee_id),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        attendance.check_out_time = timezone.now()
        attendance.total_work_hours = attendance.check_out_time - attendance.check_in_time
        attendance.status = AttendanceLog.Status.CHECKED_OUT
        attendance.save(update_fields=["check_out_time", "total_work_hours", "status", "updated_at"])

        return Response(
            {
                "success": True,
                "message": "Checked out successfully.",
                "data": build_today_payload(attendance, employee_id=employee_id),
            }
        )


class AttendanceActivityAPIView(APIView):
    @extend_schema(
        tags=["Attendance"],
        summary="Record employee activity heartbeat",
        description="Updates last activity for today's open session and runs auto check-out rules.",
    )
    def post(self, request):
        employee_id = employee_id_from_request(request)
        denied = attendance_access_denied_response(employee_id)
        if denied:
            return denied

        attendance = record_activity(employee_id)
        if attendance is None:
            return Response(
                {
                    "success": True,
                    "message": "No active check-in session for today.",
                    "data": build_today_payload(None, employee_id=employee_id),
                }
            )
        return Response(
            {
                "success": True,
                "message": "Activity recorded.",
                "data": build_today_payload(attendance, employee_id=employee_id),
            }
        )


class AttendanceHistoryAPIView(APIView):
    @extend_schema(
        tags=["Attendance"],
        summary="Employee/BA staff attendance history",
        responses={200: AttendanceLogSerializer(many=True)},
    )
    def get(self, request):
        employee_id = employee_id_from_request(request)
        process_open_attendance_records(
            AttendanceLog.objects.filter(employee_id=employee_id, check_out_time__isnull=True)
        )
        start_date = parse_date(request.query_params.get("start_date", ""))
        end_date = parse_date(request.query_params.get("end_date", ""))

        history = build_staff_history_payload(
            employee_id,
            start_date=start_date,
            end_date=end_date,
        )

        return Response(
            {
                "success": True,
                "message": "Attendance history fetched successfully.",
                "summary": history["summary"],
                "period": history["period"],
                "records": history["records"],
            }
        )


class TodayAttendanceAPIView(APIView):
    @extend_schema(
        tags=["Attendance"],
        summary="Employee today's attendance",
        responses={200: AttendanceLogSerializer},
    )
    def get(self, request):
        employee_id = employee_id_from_request(request)
        close_stale_open_sessions(employee_id)
        attendance = get_today_attendance(employee_id)
        if attendance is None:
            return Response(
                {
                    "success": True,
                    "message": "No attendance record for today.",
                    "data": build_today_payload(None, employee_id=employee_id),
                }
            )
        return Response(
            {
                "success": True,
                "message": "Today's attendance fetched successfully.",
                "data": build_today_payload(attendance, employee_id=employee_id),
            }
        )


class AdminAttendanceHistoryAPIView(APIView):
    permission_classes = [IsAttendanceAdmin]

    @extend_schema(
        tags=["Admin Attendance"],
        summary="Admin attendance history for all employees",
        responses={200: AttendanceLogSerializer(many=True)},
    )
    def get(self, request):
        attendance_date = parse_date(request.query_params.get("date", ""))
        resolver = resolver_from_request(request)
        employee_id = request.query_params.get("employee_id")
        start_date = parse_date(request.query_params.get("start_date", ""))
        end_date = parse_date(request.query_params.get("end_date", ""))
        check_in_filter = request.query_params.get("check_in_filter", "all")
        status_filter = request.query_params.get("status_filter", "all")
        auth_token = request.headers.get("Authorization")
        staff_ids = parse_staff_ids_param(request.query_params.get("staff_ids", ""))

        if attendance_date and not start_date and not end_date:
            process_open_attendance_records(notify=False)
            day_payload = build_admin_day_payload(
                attendance_date,
                resolver=resolver,
                employee_id=employee_id,
                check_in_filter=check_in_filter,
                status_filter=status_filter,
                auth_token=auth_token,
                staff_ids=staff_ids,
            )
            return Response(
                {
                    "success": True,
                    "message": "Admin attendance history fetched successfully.",
                    "filters": {
                        "employee_id": employee_id,
                        "start_date": start_date,
                        "end_date": end_date,
                        "date": attendance_date,
                        "check_in_filter": check_in_filter,
                        "status_filter": status_filter,
                    },
                    "day_context": day_payload.get("day_context"),
                    "summary": day_payload["summary"],
                    "records": day_payload["records"],
                }
            )

        if resolver:
            seed_staff_resolver(resolver, token=auth_token)

        attendance = AttendanceLog.objects.all()
        if employee_id:
            attendance = attendance.filter(employee_id=employee_id)
        if start_date:
            attendance = attendance.filter(attendance_date__gte=start_date)
        if end_date:
            attendance = attendance.filter(attendance_date__lte=end_date)
        if attendance_date:
            attendance = attendance.filter(attendance_date=attendance_date)

        records = [
            format_attendance_record(item, resolver) for item in attendance[:500]
        ]
        extra_work = sum(1 for item in records if item["work_analysis"]["variance"] == "extra_work")
        less_work = sum(1 for item in records if item["work_analysis"]["variance"] == "less_work")

        return Response(
            {
                "success": True,
                "message": "Admin attendance history fetched successfully.",
                "filters": {
                    "employee_id": employee_id,
                    "start_date": start_date,
                    "end_date": end_date,
                    "date": attendance_date,
                    "check_in_filter": check_in_filter,
                },
                "summary": {
                    **build_history_summary(attendance),
                    "extra_work_days": extra_work,
                    "less_work_days": less_work,
                },
                "records": records,
            }
        )


class AdminDashboardAPIView(APIView):
    permission_classes = [IsAttendanceAdmin]

    @extend_schema(
        tags=["Admin Dashboard"],
        summary="Admin attendance and leave dashboard",
    )
    def get(self, request):
        from apps.leaves.services import apply_leave_history_retention
        from apps.leaves.views import leave_card

        process_open_attendance_records()
        resolver = resolver_from_request(request)
        dashboard_date = parse_date(request.query_params.get("date", "")) or timezone.localdate()
        check_in_filter = request.query_params.get("check_in_filter", "all")
        status_filter = request.query_params.get("status_filter", "all")
        employee_id = request.query_params.get("employee_id")
        auth_token = request.headers.get("Authorization")

        day_payload = build_admin_day_payload(
            dashboard_date,
            resolver=resolver,
            employee_id=employee_id,
            check_in_filter=check_in_filter,
            status_filter=status_filter,
            auth_token=auth_token,
        )

        retained_leaves = apply_leave_history_retention(LeaveRequest.objects.all())
        leave_counts = {
            LeaveRequest.Status.PENDING: LeaveRequest.objects.filter(
                status=LeaveRequest.Status.PENDING
            ).count(),
            LeaveRequest.Status.APPROVED: retained_leaves.filter(
                status=LeaveRequest.Status.APPROVED
            ).count(),
            LeaveRequest.Status.REJECTED: retained_leaves.filter(
                status=LeaveRequest.Status.REJECTED
            ).count(),
        }

        return Response(
            {
                "success": True,
                "message": "Admin dashboard fetched successfully.",
                "date": dashboard_date.isoformat(),
                "day_context": day_payload.get("day_context"),
                "attendance_today": day_payload["attendance_today"],
                "summary": day_payload["summary"],
                "leaves": {
                    "pending_count": leave_counts[LeaveRequest.Status.PENDING],
                    "approved_count": leave_counts[LeaveRequest.Status.APPROVED],
                    "rejected_count": leave_counts[LeaveRequest.Status.REJECTED],
                    "pending_requests": [
                        leave_card(item, resolver)
                        for item in LeaveRequest.objects.filter(status=LeaveRequest.Status.PENDING)[:20]
                    ],
                    "recent_approved": [
                        leave_card(item, resolver)
                        for item in retained_leaves.filter(
                            status=LeaveRequest.Status.APPROVED
                        ).order_by("-approved_at")[:10]
                    ],
                    "recent_rejected": [
                        leave_card(item, resolver)
                        for item in retained_leaves.filter(
                            status=LeaveRequest.Status.REJECTED
                        ).order_by("-approved_at")[:10]
                    ],
                },
            }
        )


class AdminAttendanceStatusOverrideAPIView(APIView):
    permission_classes = [IsAttendanceAdmin]

    @extend_schema(
        tags=["Admin Attendance"],
        summary="Admin edit attendance record",
        description=(
            "Allows admins to override display status and check-in/check-out times "
            "for an employee on a given date."
        ),
    )
    def patch(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        employee_id = payload.get("employee_id")
        attendance_date = parse_date(str(payload.get("date") or ""))
        attendance_log_id = payload.get("attendance_log_id")

        if employee_id is None or attendance_date is None:
            return Response(
                {
                    "success": False,
                    "message": "employee_id and date are required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            employee_id = int(employee_id)
        except (TypeError, ValueError):
            return Response(
                {"success": False, "message": "employee_id must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if attendance_log_id is not None:
            try:
                attendance_log_id = int(attendance_log_id)
            except (TypeError, ValueError):
                return Response(
                    {
                        "success": False,
                        "message": "attendance_log_id must be an integer.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        admin_id = getattr(request.user, "employee_id", None)
        if admin_id is not None:
            admin_id = int(admin_id)

        has_display_status = "display_status" in payload
        has_check_in = "check_in" in payload
        has_check_out = "check_out" in payload
        if not has_display_status and not has_check_in and not has_check_out:
            return Response(
                {
                    "success": False,
                    "message": "Provide display_status, check_in, and/or check_out to update.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            attendance = apply_admin_attendance_edit(
                employee_id=employee_id,
                attendance_date=attendance_date,
                admin_id=admin_id,
                attendance_log_id=attendance_log_id,
                display_status=(
                    payload.get("display_status") if has_display_status else None
                ),
                check_in=payload.get("check_in") if has_check_in else None,
                check_out=payload.get("check_out") if has_check_out else None,
            )
        except AdminAttendanceEditError as exc:
            return Response(
                {"success": False, "message": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        resolver = resolver_from_request(request)
        record = format_attendance_record(attendance, resolver)
        return Response(
            {
                "success": True,
                "message": "Attendance updated successfully.",
                "data": record,
            }
        )
