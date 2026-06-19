"""Read-only attendance/leave snapshot for admin AI (ORM queries only — no writes)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.utils import timezone

from apps.attendance.models import AttendanceLog
from apps.common.pms_client import employee_display_name, staff_users_from_pms
from apps.leaves.models import Holiday, LeaveBalance, LeaveRequest

_MAX_RECENT_LOGS = 80
_MAX_LEAVE_ROWS = 40
_MAX_HOLIDAYS = 20


def _employee_name(name_by_id: dict[int, str], employee_id: int) -> str:
    return name_by_id.get(int(employee_id), f"Employee {employee_id}")


def _serialize_leave(row: LeaveRequest, name_by_id: dict[int, str]) -> dict[str, Any]:
    return {
        "employee_id": row.employee_id,
        "employee_name": _employee_name(name_by_id, row.employee_id),
        "leave_type": row.leave_type,
        "from_date": row.from_date.isoformat(),
        "to_date": row.to_date.isoformat(),
        "status": row.status,
        "reason_excerpt": (row.reason or "")[:200],
    }


def _serialize_attendance(row: AttendanceLog, name_by_id: dict[int, str]) -> dict[str, Any]:
    return {
        "employee_id": row.employee_id,
        "employee_name": _employee_name(name_by_id, row.employee_id),
        "attendance_date": row.attendance_date.isoformat(),
        "status": row.status,
        "check_in_time": row.check_in_time.isoformat() if row.check_in_time else None,
        "check_out_time": row.check_out_time.isoformat() if row.check_out_time else None,
        "total_work_hours": str(row.total_work_hours) if row.total_work_hours else None,
        "auto_checked_out": row.auto_checked_out,
    }


def _attendance_summary_for_date(target_date, staff_ids: set[int]) -> dict[str, Any]:
    logs = AttendanceLog.objects.filter(attendance_date=target_date)
    if staff_ids:
        logs = logs.filter(employee_id__in=staff_ids)
    checked_in = logs.filter(check_in_time__isnull=False).count()
    checked_out = logs.filter(status=AttendanceLog.Status.CHECKED_OUT).count()
    still_present = logs.filter(status=AttendanceLog.Status.PRESENT).count()
    return {
        "date": target_date.isoformat(),
        "records": logs.count(),
        "checked_in": checked_in,
        "checked_out": checked_out,
        "still_present_not_checked_out": still_present,
    }


def build_attendance_readonly_snapshot() -> dict[str, Any]:
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)
    staff = staff_users_from_pms()
    name_by_id = {
        int(user["id"]): employee_display_name(user, int(user["id"]))
        for user in staff
        if user.get("id") is not None
    }
    staff_ids = set(name_by_id.keys())

    today_logs = AttendanceLog.objects.filter(attendance_date=today)
    if staff_ids:
        today_logs = today_logs.filter(employee_id__in=staff_ids)

    checked_in_today = today_logs.filter(check_in_time__isnull=False).count()
    checked_out_today = today_logs.filter(status=AttendanceLog.Status.CHECKED_OUT).count()
    still_present = today_logs.filter(status=AttendanceLog.Status.PRESENT).count()
    yesterday_summary = _attendance_summary_for_date(yesterday, staff_ids)
    yesterday_logs_qs = AttendanceLog.objects.filter(attendance_date=yesterday)
    if staff_ids:
        yesterday_logs_qs = yesterday_logs_qs.filter(employee_id__in=staff_ids)
    yesterday_logs = [
        _serialize_attendance(row, name_by_id)
        for row in yesterday_logs_qs.order_by("-check_in_time")[:40]
    ]

    leave_status_counts = {
        "pending": LeaveRequest.objects.filter(status=LeaveRequest.Status.PENDING).count(),
        "approved": LeaveRequest.objects.filter(status=LeaveRequest.Status.APPROVED).count(),
        "rejected": LeaveRequest.objects.filter(status=LeaveRequest.Status.REJECTED).count(),
    }

    pending_leaves = (
        LeaveRequest.objects.filter(status=LeaveRequest.Status.PENDING)
        .order_by("-created_at")[:_MAX_LEAVE_ROWS]
    )
    approved_upcoming = (
        LeaveRequest.objects.filter(
            status=LeaveRequest.Status.APPROVED,
            to_date__gte=today,
        )
        .order_by("from_date")[:_MAX_LEAVE_ROWS]
    )
    on_leave_today = LeaveRequest.objects.filter(
        status=LeaveRequest.Status.APPROVED,
        from_date__lte=today,
        to_date__gte=today,
    )

    holidays = (
        Holiday.objects.filter(is_active=True, holiday_date__gte=today)
        .order_by("holiday_date")[:_MAX_HOLIDAYS]
    )

    recent_logs = (
        AttendanceLog.objects.filter(attendance_date__gte=today - timedelta(days=30))
        .order_by("-attendance_date", "-check_in_time")[:_MAX_RECENT_LOGS]
    )

    balances = LeaveBalance.objects.all().order_by("employee_id")[:50]

    snapshot = {
        "as_of_date": today.isoformat(),
        "attendance_summary_today": {
            "records_today": today_logs.count(),
            "checked_in_today": checked_in_today,
            "checked_out_today": checked_out_today,
            "still_present_not_checked_out": still_present,
            "staff_count_known": len(staff_ids),
        },
        "attendance_summary_yesterday": yesterday_summary,
        "attendance_logs_yesterday": yesterday_logs,
        "leave_status_counts": leave_status_counts,
        "employees_on_approved_leave_today": [
            _serialize_leave(row, name_by_id) for row in on_leave_today[:30]
        ],
        "pending_leave_requests": [_serialize_leave(row, name_by_id) for row in pending_leaves],
        "approved_upcoming_leaves": [_serialize_leave(row, name_by_id) for row in approved_upcoming],
        "upcoming_holidays": [
            {
                "name": h.name,
                "holiday_date": h.holiday_date.isoformat(),
                "description_excerpt": (h.description or "")[:120],
            }
            for h in holidays
        ],
        "recent_attendance_logs": [_serialize_attendance(row, name_by_id) for row in recent_logs],
        "leave_balances_sample": [
            {
                "employee_id": b.employee_id,
                "employee_name": _employee_name(name_by_id, b.employee_id),
                "annual_leave": b.annual_leave,
                "sick_leave": b.sick_leave,
                "casual_leave": b.casual_leave,
                "compensatory_leave": b.compensatory_leave,
            }
            for b in balances
        ],
        "instructions": (
            "Use attendance_summary_today for today, attendance_summary_yesterday for yesterday, "
            "attendance_logs_yesterday for who checked in/out yesterday, leave_status_counts, "
            "pending_leave_requests, employees_on_approved_leave_today, and recent_attendance_logs."
        ),
    }
    summary = snapshot["attendance_summary_today"]
    leave = snapshot["leave_status_counts"]
    on_leave_names = [
        r["employee_name"] for r in snapshot["employees_on_approved_leave_today"] if r.get("employee_name")
    ]
    pending_names = [r["employee_name"] for r in snapshot["pending_leave_requests"][:10] if r.get("employee_name")]
    hints = [
        f"Today: {summary['checked_in_today']} checked in, {summary['still_present_not_checked_out']} still present.",
    ]
    if yesterday_summary.get("date"):
        hints.append(
            f"Yesterday ({yesterday_summary['date']}): "
            f"{yesterday_summary.get('checked_in', 0)} checked in, "
            f"{yesterday_summary.get('checked_out', 0)} checked out, "
            f"{yesterday_summary.get('still_present_not_checked_out', 0)} still present."
        )
    hints.append(f"Pending leave requests: {leave['pending']}.")
    if on_leave_names:
        hints.append(f"On approved leave today: {', '.join(on_leave_names[:15])}.")
    if pending_names:
        hints.append(f"Awaiting approval: {', '.join(pending_names)}.")
    snapshot["attendance_ai_briefing"] = {"plain_english_hints": hints}
    return snapshot
