from datetime import timedelta

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
            "initials": f"E{attendance.employee_id}",
            "email": "",
        }
    )
    return {
        "id": attendance.id,
        "employee_id": attendance.employee_id,
        "employee_name": employee["name"],
        "employee_department": employee["department"],
        "employee_initials": employee["initials"],
        "employee": employee,
        "date": attendance.attendance_date.isoformat(),
        "day": attendance.attendance_date.strftime("%A"),
        "status": attendance.status,
        "status_label": attendance.get_status_display(),
        "check_in": format_time(attendance.check_in_time),
        "check_out": format_time(attendance.check_out_time),
        "duration": format_duration(attendance.total_work_hours),
        "location": DEFAULT_OFFICE_LOCATION,
        "auto_checked_out": analysis["auto_checked_out"],
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
        process_open_attendance_records()
        resolver = resolver_from_request(request)
        attendance = AttendanceLog.objects.all()
        employee_id = request.query_params.get("employee_id")
        start_date = parse_date(request.query_params.get("start_date", ""))
        end_date = parse_date(request.query_params.get("end_date", ""))
        attendance_date = parse_date(request.query_params.get("date", ""))

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

        from apps.leaves.models import LeaveRequest

        process_open_attendance_records()
        resolver = resolver_from_request(request)
        dashboard_date = parse_date(request.query_params.get("date", "")) or timezone.localdate()

        today_logs = AttendanceLog.objects.filter(attendance_date=dashboard_date).order_by("-check_in_time")
        formatted_records = [
            format_attendance_record(item, resolver) for item in today_logs
        ]

        checked_in = today_logs.filter(check_in_time__isnull=False).count()
        checked_out = today_logs.filter(check_out_time__isnull=False).count()
        still_working = today_logs.filter(check_out_time__isnull=True, check_in_time__isnull=False).count()

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
                "attendance_today": {
                    "checked_in_count": checked_in,
                    "checked_out_count": checked_out,
                    "still_working_count": still_working,
                    "extra_work_count": sum(
                        1 for item in formatted_records if item["work_analysis"]["variance"] == "extra_work"
                    ),
                    "less_work_count": sum(
                        1 for item in formatted_records if item["work_analysis"]["variance"] == "less_work"
                    ),
                    "records": formatted_records,
                },
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
