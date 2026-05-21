from django.db.models import Count
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.attendance.views import employee_id_from_request
from apps.authentication.permissions import IsAttendanceAdmin
from apps.common.employee_profiles import resolver_from_request

from .models import Holiday, LeaveBalance, LeaveRequest
from .serializers import HolidaySerializer, LeaveApprovalSerializer, LeaveBalanceSerializer, LeaveRequestSerializer
from .services import (
    deduct_leave_balance,
    send_leave_status_email,
    upcoming_holidays,
)

LEAVE_TOTALS = {
    "annual_leave": 18,
    "sick_leave": 12,
    "casual_leave": 6,
    "compensatory_leave": 2,
}


def leave_days(leave_request):
    return max((leave_request.to_date - leave_request.from_date).days + 1, 1)


def leave_type_label(value):
    return dict(LeaveRequest.LeaveType.choices).get(value, value.title())


def get_or_create_leave_balance(employee_id):
    return LeaveBalance.objects.get_or_create(
        employee_id=employee_id,
        defaults=LEAVE_TOTALS,
    )[0]


def balance_cards(balance):
    return [
        {
            "key": "annual_leave",
            "label": "Annual Leave",
            "remaining": balance.annual_leave,
            "total": LEAVE_TOTALS["annual_leave"],
        },
        {
            "key": "sick_leave",
            "label": "Sick Leave",
            "remaining": balance.sick_leave,
            "total": LEAVE_TOTALS["sick_leave"],
        },
        {
            "key": "casual_leave",
            "label": "Casual Leave",
            "remaining": balance.casual_leave,
            "total": LEAVE_TOTALS["casual_leave"],
        },
        {
            "key": "compensatory_leave",
            "label": "Compensatory",
            "remaining": balance.compensatory_leave,
            "total": LEAVE_TOTALS["compensatory_leave"],
        },
    ]


def leave_card(leave_request, resolver=None):
    employee = (
        resolver.employee_block(leave_request.employee_id)
        if resolver
        else {
            "id": leave_request.employee_id,
            "name": f"Employee {leave_request.employee_id}",
            "department": "—",
            "initials": f"E{leave_request.employee_id}",
            "email": "",
        }
    )
    return {
        "id": leave_request.id,
        "employee_id": leave_request.employee_id,
        "employee": employee,
        "leave_type": leave_request.leave_type,
        "leave_type_label": leave_type_label(leave_request.leave_type),
        "date_range": f"{leave_request.from_date.strftime('%b %d')} - {leave_request.to_date.strftime('%b %d, %Y')}",
        "from_date": leave_request.from_date.isoformat(),
        "to_date": leave_request.to_date.isoformat(),
        "days": leave_days(leave_request),
        "reason": leave_request.reason,
        "status": leave_request.status,
        "applied_on": leave_request.created_at.date().isoformat(),
        "approved_by": leave_request.approved_by,
        "approved_at": leave_request.approved_at.isoformat() if leave_request.approved_at else None,
        "rejection_reason": leave_request.rejection_reason,
    }


def holiday_card(holiday):
    return {
        "id": holiday.id,
        "name": holiday.name,
        "date": holiday.holiday_date.isoformat(),
        "day": holiday.holiday_date.strftime("%A"),
        "description": holiday.description,
    }


class ApplyLeaveAPIView(APIView):
    @extend_schema(
        tags=["Leaves"],
        summary="Apply for leave",
        request=LeaveRequestSerializer,
        responses={201: LeaveRequestSerializer},
        examples=[
            OpenApiExample(
                "Leave apply body",
                value={
                    "leave_type": "ANNUAL",
                    "from_date": "2026-05-20",
                    "to_date": "2026-05-24",
                    "reason": "Family vacation",
                },
                request_only=True,
            )
        ],
    )
    def post(self, request):
        employee_id = employee_id_from_request(request)
        serializer = LeaveRequestSerializer(
            data=request.data,
            context={"employee_id": employee_id},
        )
        serializer.is_valid(raise_exception=True)
        leave_request = serializer.save(employee_id=employee_id)
        balance = get_or_create_leave_balance(leave_request.employee_id)
        return Response(
            {
                "success": True,
                "message": "Leave application submitted successfully.",
                "data": {
                    "leave_request": leave_card(leave_request),
                    "balances": balance_cards(balance),
                },
            },
            status=status.HTTP_201_CREATED,
        )


class LeaveHistoryAPIView(APIView):
    @extend_schema(
        tags=["Leaves"],
        summary="Employee leave history",
        responses={200: LeaveRequestSerializer(many=True)},
    )
    def get(self, request):
        employee_id = employee_id_from_request(request)
        status_filter = (request.query_params.get("status") or "").upper()
        leave_requests = LeaveRequest.objects.filter(employee_id=employee_id)
        if status_filter in LeaveRequest.Status.values:
            leave_requests = leave_requests.filter(status=status_filter)
        balance = get_or_create_leave_balance(employee_id)
        return Response(
            {
                "success": True,
                "message": "Leave history fetched successfully.",
                "balances": balance_cards(balance),
                "requests": [leave_card(item) for item in leave_requests],
            }
        )


class LeaveBalanceAPIView(APIView):
    @extend_schema(
        tags=["Leaves"],
        summary="Employee leave balances",
        responses={200: LeaveBalanceSerializer},
    )
    def get(self, request):
        balance = get_or_create_leave_balance(employee_id_from_request(request))
        return Response(
            {
                "success": True,
                "message": "Leave balances fetched successfully.",
                "balances": balance_cards(balance),
                "raw": LeaveBalanceSerializer(balance).data,
            }
        )


class HolidayListAPIView(APIView):
    @extend_schema(
        tags=["Leaves"],
        summary="Company holidays",
        responses={200: HolidaySerializer(many=True)},
    )
    def get(self, request):
        year = request.query_params.get("year")
        holidays = upcoming_holidays()
        if year and year.isdigit():
            holidays = Holiday.objects.filter(is_active=True, holiday_date__year=int(year)).order_by("holiday_date")
        return Response(
            {
                "success": True,
                "message": "Holidays fetched successfully.",
                "holidays": [holiday_card(item) for item in holidays],
            }
        )


class PendingLeaveRequestsAPIView(APIView):
    permission_classes = [IsAttendanceAdmin]

    @extend_schema(
        tags=["Admin Leaves"],
        summary="Pending leave requests",
        responses={200: LeaveRequestSerializer(many=True)},
    )
    def get(self, request):
        resolver = resolver_from_request(request)
        leave_requests = LeaveRequest.objects.filter(status=LeaveRequest.Status.PENDING)
        return Response(
            {
                "success": True,
                "message": "Pending leave requests fetched successfully.",
                "pending_count": leave_requests.count(),
                "requests": [leave_card(item, resolver) for item in leave_requests],
            }
        )


class AdminLeaveListAPIView(APIView):
    permission_classes = [IsAttendanceAdmin]

    @extend_schema(
        tags=["Admin Leaves"],
        summary="List leave requests by status",
    )
    def get(self, request):
        resolver = resolver_from_request(request)
        status_filter = (request.query_params.get("status") or "").upper()
        leave_requests = LeaveRequest.objects.all().order_by("-created_at")
        if status_filter in LeaveRequest.Status.values:
            leave_requests = leave_requests.filter(status=status_filter)

        counts = {
            LeaveRequest.Status.PENDING: 0,
            LeaveRequest.Status.APPROVED: 0,
            LeaveRequest.Status.REJECTED: 0,
        }
        for row in LeaveRequest.objects.values("status").annotate(total=Count("id")):
            counts[row["status"]] = row["total"]

        return Response(
            {
                "success": True,
                "message": "Leave requests fetched successfully.",
                "counts": counts,
                "requests": [leave_card(item, resolver) for item in leave_requests[:200]],
            }
        )


class ApproveLeaveAPIView(APIView):
    permission_classes = [IsAttendanceAdmin]

    @extend_schema(
        tags=["Admin Leaves"],
        summary="Approve leave request",
        description="Approves a pending leave request and sends email notification.",
        request=LeaveApprovalSerializer,
        responses={200: LeaveRequestSerializer},
    )
    def post(self, request, pk):
        resolver = resolver_from_request(request)
        leave_request = LeaveRequest.objects.filter(pk=pk, status=LeaveRequest.Status.PENDING).first()
        if leave_request is None:
            return Response({"detail": "Pending leave request not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = LeaveApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            deduct_leave_balance(leave_request)
        except serializers.ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)

        leave_request.status = LeaveRequest.Status.APPROVED
        leave_request.approved_by = employee_id_from_request(request)
        leave_request.approved_at = timezone.now()
        leave_request.rejection_reason = ""
        leave_request.save(update_fields=["status", "approved_by", "approved_at", "rejection_reason", "updated_at"])
        send_leave_status_email(leave_request, approved=True)

        return Response(
            {
                "success": True,
                "message": "Leave request approved successfully.",
                "data": leave_card(leave_request, resolver),
            }
        )


class RejectLeaveAPIView(APIView):
    permission_classes = [IsAttendanceAdmin]

    @extend_schema(
        tags=["Admin Leaves"],
        summary="Reject leave request",
        request=LeaveApprovalSerializer,
        responses={200: LeaveRequestSerializer},
        examples=[
            OpenApiExample(
                "Reject body",
                value={"rejection_reason": "Project deadline conflict"},
                request_only=True,
            )
        ],
    )
    def post(self, request, pk):
        resolver = resolver_from_request(request)
        leave_request = LeaveRequest.objects.filter(pk=pk, status=LeaveRequest.Status.PENDING).first()
        if leave_request is None:
            return Response({"detail": "Pending leave request not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = LeaveApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rejection_reason = serializer.validated_data.get("rejection_reason", "")

        leave_request.status = LeaveRequest.Status.REJECTED
        leave_request.approved_by = employee_id_from_request(request)
        leave_request.approved_at = timezone.now()
        leave_request.rejection_reason = rejection_reason
        leave_request.save(update_fields=["status", "approved_by", "approved_at", "rejection_reason", "updated_at"])
        send_leave_status_email(leave_request, approved=False, rejection_reason=rejection_reason)

        return Response(
            {
                "success": True,
                "message": "Leave request rejected successfully.",
                "data": leave_card(leave_request, resolver),
            }
        )
