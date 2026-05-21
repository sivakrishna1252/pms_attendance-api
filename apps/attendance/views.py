from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.permissions import IsAttendanceAdmin
from apps.common.employee_profiles import resolver_from_request
from apps.common.pms_client import fetch_all_users
from apps.leaves.models import LeaveRequest

from .constants import LATE_CHECK_IN_HOUR, LATE_CHECK_IN_MINUTE
from .models import AttendanceLog
from .serializers import AttendanceLogSerializer
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
        from django.utils.dateparse import parse_datetime

        parsed = parse_datetime(check_in_time)
        if parsed:
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            if is_late_check_in(parsed):
                return True
    return is_late_from_check_in_display(record.get("check_in"))


def apply_display_status(record):
    status = record.get("status")
    if status == "WFH":
        record["display_status"] = "WFH"
    elif status == "ON_LEAVE":
        record["display_status"] = "Absent"
    elif record.get("has_check_in"):
        record["is_late"] = resolve_record_is_late(record)
        record["display_status"] = "Late" if record["is_late"] else "Present"
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
    if apply_auto and attendance:
        attendance = apply_auto_checkout_if_needed(attendance)
    return attendance


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
    return {
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
        "is_late": is_late_check_in(attendance.check_in_time),
        "work_analysis": analysis,
        "raw": AttendanceLogSerializer(attendance).data,
    }


def build_today_payload(attendance):
    checked_in = attendance is not None
    checked_out = bool(attendance and attendance.check_out_time)
    can_check_in, next_available = can_check_in_now(attendance)

    if checked_out:
        action = "done"
    elif checked_in:
        action = "check_out"
    elif can_check_in:
        action = "check_in"
    else:
        action = "wait"

    analysis = work_analysis(attendance) if attendance else None

    return {
        "date": timezone.localdate().isoformat(),
        "office": {
            "name": DEFAULT_OFFICE_LOCATION,
            "shift": DEFAULT_SHIFT_LABEL,
            "status": "In Office",
            "is_inside_office": True,
        },
        "state": {
            "checked_in": checked_in,
            "checked_out": checked_out,
            "next_action": action,
            "can_check_in": can_check_in and not checked_in,
            "can_check_out": checked_in and not checked_out,
            "next_check_in_at": next_available.isoformat() if next_available else None,
            "next_check_in_at_display": format_time(next_available) if next_available else None,
        },
        "today_log": {
            "check_in": format_time(attendance.check_in_time) if attendance else None,
            "check_out": format_time(attendance.check_out_time) if attendance else None,
            "duration": format_duration(attendance.total_work_hours) if attendance else None,
            "work_analysis": analysis,
            "timeline": [
                {
                    "label": "Checked In",
                    "time": format_time(attendance.check_in_time),
                    "location": DEFAULT_OFFICE_LOCATION,
                }
            ] + (
                [
                    {
                        "label": "Checked Out"
                        + (" (Auto)" if attendance.auto_checked_out else ""),
                        "time": format_time(attendance.check_out_time),
                        "location": DEFAULT_OFFICE_LOCATION,
                    }
                ]
                if checked_out
                else [{"label": "Awaiting Check Out", "time": None, "location": None}]
            )
            if attendance
            else [],
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


def approved_leaves_on_date(attendance_date):
    return LeaveRequest.objects.filter(
        status=LeaveRequest.Status.APPROVED,
        from_date__lte=attendance_date,
        to_date__gte=attendance_date,
    )


def leave_flags_for_date(attendance_date):
    on_leave_ids = set()
    wfh_ids = set()
    for leave in approved_leaves_on_date(attendance_date).only("employee_id", "leave_type"):
        if leave.leave_type == LeaveRequest.LeaveType.WFH:
            wfh_ids.add(leave.employee_id)
        else:
            on_leave_ids.add(leave.employee_id)
    return on_leave_ids, wfh_ids


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
    status_label = "WFH" if is_wfh else "Absent"
    return {
        "id": None,
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
        "check_in": "-",
        "check_out": "-",
        "duration": "-",
        "location": "—",
        "auto_checked_out": False,
        "has_check_in": False,
        "attendance_type": "WFH" if is_wfh else "Leave",
        "is_late": False,
        "display_status": status_label,
        "work_analysis": work_analysis(None),
        "raw": None,
    }


def format_no_checkin_record(employee_id, attendance_date, resolver):
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
        "id": None,
        "employee_id": employee_id,
        "employee_name": employee["name"],
        "employee_department": employee["department"],
        "employee_role": employee.get("role", "—"),
        "employee_initials": employee["initials"],
        "employee": employee,
        "date": attendance_date.isoformat(),
        "day": attendance_date.strftime("%A"),
        "status": "NOT_CHECKED_IN",
        "status_label": "—",
        "display_status": "—",
        "check_in": "-",
        "check_out": "-",
        "duration": "-",
        "location": "—",
        "auto_checked_out": False,
        "has_check_in": False,
        "attendance_type": "—",
        "is_late": False,
        "work_analysis": work_analysis(None),
        "raw": None,
    }


def staff_users_from_pms(*, token=None):
    """Active PMS users with Employee or BA role (excludes Admin)."""
    staff = []
    for user in fetch_all_users(token=token):
        role = str(user.get("role") or "").upper()
        status = str(user.get("status") or "ACTIVE").upper()
        user_id = user.get("id")
        if user_id is None:
            continue
        if role not in {"EMPLOYEE", "BA"}:
            continue
        if status != "ACTIVE":
            continue
        staff.append(user)
    return staff


def annotate_attendance_day_context(record, *, on_leave_ids, wfh_ids):
    employee_id = record["employee_id"]
    has_check_in = record.get("check_in") not in (None, "-", "")
    record["has_check_in"] = has_check_in

    if employee_id in wfh_ids:
        record["status"] = "WFH"
        record["status_label"] = "WFH"
        record["attendance_type"] = "WFH"
    elif employee_id in on_leave_ids and not has_check_in:
        record["status"] = "ON_LEAVE"
        record["status_label"] = "Absent"
        record["attendance_type"] = "Leave"
    elif record.get("auto_checked_out"):
        record["attendance_type"] = "Auto checkout"
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


def apply_check_in_filter(records, check_in_filter):
    normalized = (check_in_filter or "all").lower()
    if normalized in {"", "all"}:
        return records
    if normalized == "checked_in":
        return [item for item in records if item.get("has_check_in")]
    if normalized in {"without_check_in", "no_check_in", "not_checked_in"}:
        return [
            item
            for item in records
            if not item.get("has_check_in")
            and item.get("status") not in ("ON_LEAVE", "WFH")
        ]
    return records


def apply_status_filter(records, status_filter):
    normalized = (status_filter or "all").lower()
    targets = {
        "late": "Late",
        "present": "Present",
        "absent": "Absent",
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
    today_logs = AttendanceLog.objects.filter(attendance_date=attendance_date).order_by("-check_in_time")
    if employee_id:
        today_logs = today_logs.filter(employee_id=employee_id)

    on_leave_ids, wfh_ids = leave_flags_for_date(attendance_date)
    records_by_employee = {}
    normalized_check_in = (check_in_filter or "all").lower()
    is_without_check_in = normalized_check_in in {
        "without_check_in",
        "no_check_in",
        "not_checked_in",
    }
    token = auth_token or (getattr(resolver, "token", None) if resolver else None)
    pms_staff_users = []
    if is_without_check_in and not employee_id:
        if staff_ids:
            pms_staff_users = [{"id": staff_id} for staff_id in staff_ids]
        else:
            pms_staff_users = staff_users_from_pms(token=token)
        if resolver and pms_staff_users:
            resolver.seed_from_users(pms_staff_users)

    for attendance in today_logs:
        record = format_attendance_record(attendance, resolver)
        annotate_attendance_day_context(
            record,
            on_leave_ids=on_leave_ids,
            wfh_ids=wfh_ids,
        )
        records_by_employee[attendance.employee_id] = record

    if not employee_id and not is_without_check_in:
        for emp_id in on_leave_ids:
            if emp_id not in records_by_employee:
                records_by_employee[emp_id] = format_leave_only_record(
                    emp_id,
                    attendance_date,
                    resolver,
                    is_wfh=False,
                )
        for emp_id in wfh_ids:
            if emp_id not in records_by_employee:
                records_by_employee[emp_id] = format_leave_only_record(
                    emp_id,
                    attendance_date,
                    resolver,
                    is_wfh=True,
                )

    if is_without_check_in and not employee_id:
        on_approved_leave = on_leave_ids | wfh_ids
        for user in pms_staff_users:
            emp_id = int(user["id"])
            if emp_id in records_by_employee or emp_id in on_approved_leave:
                continue
            records_by_employee[emp_id] = format_no_checkin_record(
                emp_id,
                attendance_date,
                resolver,
            )

    records = sorted(
        records_by_employee.values(),
        key=lambda item: (item.get("employee_name") or "").lower(),
    )
    records = apply_check_in_filter(records, check_in_filter)
    records = apply_status_filter(records, status_filter)

    checked_in_count = sum(1 for item in records_by_employee.values() if item.get("has_check_in"))
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
    less_work_count = sum(
        1 for item in records_by_employee.values() if item["work_analysis"]["variance"] == "less_work"
    )
    late_count = sum(
        1
        for item in records_by_employee.values()
        if item.get("display_status") == "Late" or resolve_record_is_late(item)
    )

    absent_count = len(on_leave_ids)
    if employee_id:
        absent_count = 1 if int(employee_id) in on_leave_ids else 0
    wfh_count = len(wfh_ids)
    if employee_id:
        wfh_count = 1 if int(employee_id) in wfh_ids else 0

    avg_hours = average_work_hours_summary(records_by_employee.values())

    return {
        "records": records,
        "summary": {
            "present": checked_in_count,
            "absent": absent_count,
            "leave": absent_count,
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
            "less_work_days": less_work_count,
            "late_check_ins": late_count,
            **avg_hours,
        },
        "attendance_today": {
            "checked_in_count": checked_in_count,
            "checked_out_count": checked_out_count,
            "still_working_count": still_working_count,
            "extra_work_count": extra_work_count,
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

        existing = AttendanceLog.objects.filter(employee_id=employee_id, attendance_date=today).first()
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
                        "data": build_today_payload(existing),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {
                    "success": False,
                    "message": "Already checked in today. Please check out first.",
                    "data": build_today_payload(existing),
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
                        "data": build_today_payload(None),
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
                    "data": build_today_payload(attendance),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "success": True,
                "message": "Checked in successfully.",
                "data": build_today_payload(attendance),
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
                    "data": build_today_payload(attendance),
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
                "data": build_today_payload(attendance),
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
        attendance = record_activity(employee_id)
        if attendance is None:
            return Response(
                {
                    "success": True,
                    "message": "No active check-in session for today.",
                    "data": build_today_payload(None),
                }
            )
        return Response(
            {
                "success": True,
                "message": "Activity recorded.",
                "data": build_today_payload(attendance),
            }
        )


class AttendanceHistoryAPIView(APIView):
    @extend_schema(
        tags=["Attendance"],
        summary="Employee attendance history",
        responses={200: AttendanceLogSerializer(many=True)},
    )
    def get(self, request):
        employee_id = employee_id_from_request(request)
        process_open_attendance_records(
            AttendanceLog.objects.filter(employee_id=employee_id, check_out_time__isnull=True)
        )
        attendance = AttendanceLog.objects.filter(employee_id=employee_id)
        start_date = parse_date(request.query_params.get("start_date", ""))
        end_date = parse_date(request.query_params.get("end_date", ""))
        if start_date:
            attendance = attendance.filter(attendance_date__gte=start_date)
        if end_date:
            attendance = attendance.filter(attendance_date__lte=end_date)

        return Response(
            {
                "success": True,
                "message": "Attendance history fetched successfully.",
                "summary": build_history_summary(attendance),
                "records": [format_attendance_record(item) for item in attendance[:200]],
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
                    "data": build_today_payload(None),
                }
            )
        return Response(
            {
                "success": True,
                "message": "Today's attendance fetched successfully.",
                "data": build_today_payload(attendance),
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
        if attendance_date:
            process_open_attendance_records(
                AttendanceLog.objects.filter(
                    attendance_date=attendance_date,
                    check_out_time__isnull=True,
                    check_in_time__isnull=False,
                ),
                notify=False,
            )
        else:
            process_open_attendance_records(notify=False)

        resolver = resolver_from_request(request)
        employee_id = request.query_params.get("employee_id")
        start_date = parse_date(request.query_params.get("start_date", ""))
        end_date = parse_date(request.query_params.get("end_date", ""))
        check_in_filter = request.query_params.get("check_in_filter", "all")
        status_filter = request.query_params.get("status_filter", "all")
        auth_token = request.headers.get("Authorization")
        staff_ids = parse_staff_ids_param(request.query_params.get("staff_ids", ""))

        if attendance_date and not start_date and not end_date:
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
                    "summary": day_payload["summary"],
                    "records": day_payload["records"],
                }
            )

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

        leave_counts = {
            status: LeaveRequest.objects.filter(status=status).count()
            for status in [
                LeaveRequest.Status.PENDING,
                LeaveRequest.Status.APPROVED,
                LeaveRequest.Status.REJECTED,
            ]
        }

        return Response(
            {
                "success": True,
                "message": "Admin dashboard fetched successfully.",
                "date": dashboard_date.isoformat(),
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
                        for item in LeaveRequest.objects.filter(status=LeaveRequest.Status.APPROVED).order_by(
                            "-approved_at"
                        )[:10]
                    ],
                    "recent_rejected": [
                        leave_card(item, resolver)
                        for item in LeaveRequest.objects.filter(status=LeaveRequest.Status.REJECTED).order_by(
                            "-approved_at"
                        )[:10]
                    ],
                },
            }
        )
