from apps.attendance.calendar import holiday_info_for_date, is_working_day, iter_dates, resolve_report_date_range
from apps.attendance.models import AttendanceLog
from apps.attendance.status_rules import resolve_work_day_status
from apps.attendance.views import is_late_check_in, leave_flags_for_date
from apps.common.pms_client import staff_users_from_pms


def _resolve_employee_ids(resolver, *, employee_id=None, staff_ids=None):
    if employee_id:
        return [int(employee_id)]
    if staff_ids:
        return [int(value) for value in staff_ids]

    if resolver and getattr(resolver, "_cache", None):
        return sorted(resolver._cache.keys())

    users = staff_users_from_pms()
    if users:
        return sorted(int(user["id"]) for user in users if user.get("id") is not None)
    return []


def _attendance_logs_by_employee_date(attendance_queryset):
    mapping = {}
    for log in attendance_queryset.select_related():
        mapping[(log.employee_id, log.attendance_date)] = log
    return mapping


def _display_status_for_day(*, employee_id, day, log, on_leave_ids, wfh_ids):
    is_holiday, holiday_name = holiday_info_for_date(day)

    if employee_id in on_leave_ids and (log is None or not log.check_in_time):
        return "Absent", "", "-", "-", "-"

    if employee_id in wfh_ids and (log is None or not log.check_in_time):
        return "WFH", "", "-", "-", "-"

    if is_holiday and (log is None or not log.check_in_time):
        return "Holiday", holiday_name, "-", "-", "-"

    if log and log.check_in_time:
        from apps.attendance.views import format_time

        status = resolve_work_day_status(
            day=day,
            check_in_time=log.check_in_time,
            total_work_hours=log.total_work_hours,
            is_late=is_late_check_in(log.check_in_time),
            auto_checked_out=bool(log.auto_checked_out),
        )
        check_in = format_time(log.check_in_time)
        check_out = format_time(log.check_out_time)
        if log.auto_checked_out and check_out != "-":
            stop_pass = getattr(log, "auto_stop_pass", "") or "8PM"
            check_out = f"{check_out} (Auto Stop {stop_pass})"
        if log.total_work_hours:
            total_seconds = int(log.total_work_hours.total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes = remainder // 60
            work_hours = f"{hours}h {minutes:02d}m"
        else:
            work_hours = "-"
        return status, "", check_in, check_out, work_hours

    return "—", "", "-", "-", "-"


PRESENT_STATUSES = frozenset({"Present", "Late", "Overtime"})
HALF_DAY_STATUSES = frozenset({"Half Day", "1/3"})


def compute_attendance_status_totals(rows, *, start_date, end_date):
    """Aggregate day counts for the attendance summary footer row."""
    working_days = sum(
        1 for day in iter_dates(start_date, end_date) if is_working_day(day)
    )
    totals = {
        "working_days": working_days,
        "present": 0,
        "absent": 0,
        "holiday": 0,
        "wfh": 0,
        "half_day": 0,
        "late": 0,
        "overtime": 0,
        "not_recorded": 0,
    }

    for row in rows:
        if row.get("is_summary"):
            continue
        status = (row.get("status") or "").strip()
        if status in PRESENT_STATUSES:
            totals["present"] += 1
            if status == "Late":
                totals["late"] += 1
            elif status == "Overtime":
                totals["overtime"] += 1
        elif status in HALF_DAY_STATUSES:
            totals["half_day"] += 1
        elif status == "Absent":
            totals["absent"] += 1
        elif status == "Holiday":
            totals["holiday"] += 1
        elif status == "WFH":
            totals["wfh"] += 1
        elif status in {"—", "-", ""}:
            totals["not_recorded"] += 1

    return totals


def build_attendance_summary_row(totals):
    return {
        "employee_name": "TOTAL",
        "date": "",
        "day": "",
        "status": "Summary",
        "note": (
            f"Working Days: {totals['working_days']} | "
            f"Present: {totals['present']} | "
            f"Absent: {totals['absent']} | "
            f"Holiday: {totals['holiday']} | "
            f"WFH: {totals['wfh']} | "
            f"Half Day: {totals['half_day']}"
        ),
        "check_in": "-",
        "check_out": "-",
        "work_hours": "-",
        "is_summary": True,
    }


def build_attendance_report_rows(
    *,
    start_date,
    end_date,
    resolver,
    employee_id=None,
    staff_ids=None,
    attendance_queryset=None,
):
    attendance_queryset = attendance_queryset or AttendanceLog.objects.filter(
        attendance_date__gte=start_date,
        attendance_date__lte=end_date,
    )
    employee_ids = _resolve_employee_ids(
        resolver,
        employee_id=employee_id,
        staff_ids=staff_ids,
    )
    if not employee_ids:
        employee_ids = sorted(
            attendance_queryset.values_list("employee_id", flat=True).distinct()
        )

    logs_by_key = _attendance_logs_by_employee_date(attendance_queryset)
    rows = []

    for day in iter_dates(start_date, end_date):
        on_leave_ids, wfh_ids = leave_flags_for_date(day)
        for emp_id in employee_ids:
            log = logs_by_key.get((emp_id, day))
            status, note, check_in, check_out, work_hours = _display_status_for_day(
                employee_id=emp_id,
                day=day,
                log=log,
                on_leave_ids=on_leave_ids,
                wfh_ids=wfh_ids,
            )
            name = resolver.display_name(emp_id) if resolver else f"Employee {emp_id}"
            rows.append(
                {
                    "employee_name": name,
                    "date": day.strftime("%d %b %Y"),
                    "day": day.strftime("%A"),
                    "status": status,
                    "note": note,
                    "check_in": check_in,
                    "check_out": check_out,
                    "work_hours": work_hours,
                }
            )

    return rows


def build_leave_report_rows(*, leaves_queryset, resolver, name_cache):
    rows = []
    for leave in leaves_queryset.order_by("-created_at"):
        employee_id = int(leave.employee_id)
        if employee_id in name_cache:
            name = name_cache[employee_id]
        elif resolver is None:
            name = f"Employee {employee_id}"
        else:
            name = resolver.display_name(employee_id)
        name_cache[employee_id] = name
        rows.append(
            {
                "employee_name": name,
                "leave_type": leave.leave_type,
                "from_date": leave.from_date.strftime("%d %b %Y"),
                "to_date": leave.to_date.strftime("%d %b %Y"),
                "days": max((leave.to_date - leave.from_date).days + 1, 1),
                "status": leave.status,
                "applied_on": leave.created_at.date().strftime("%d %b %Y"),
            }
        )
    return rows


def normalize_report_filters(start_date, end_date):
    return resolve_report_date_range(start_date, end_date)
