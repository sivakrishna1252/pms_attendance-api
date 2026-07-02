from django.db.models import Count
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.attendance.views import employee_id_from_request
from apps.common.pagination import paginate_request, paginated_list_response
from apps.authentication.permissions import IsAttendanceAdmin
from apps.common.employee_profiles import resolver_from_request, seed_staff_resolver

from .models import Holiday, LeaveBalance, LeaveRequest
from .serializers import HolidaySerializer, LeaveApprovalSerializer, LeaveBalanceSerializer, LeaveRequestSerializer
from .notifications import notify_admins_leave_submitted, notify_employee_leave_decision
from .services import (
    apply_leave_history_retention,
    deduct_leave_balance,
    leave_days_between,
    LEAVE_HISTORY_RETENTION_MONTHS,
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
    return leave_days_between(leave_request.from_date, leave_request.to_date)


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


def _balance_field_for_leave_type(leave_type):
    from .services import LEAVE_TYPE_TO_BALANCE_FIELD

    return LEAVE_TYPE_TO_BALANCE_FIELD.get((leave_type or "").strip().upper())


def leave_card(leave_request, resolver=None):
    employee = (
        resolver.employee_block(leave_request.employee_id)
        if resolver
        else {
            "id": leave_request.employee_id,
            "name": f"Employee {leave_request.employee_id}",
            "department": "—",
            "role": "—",
            "initials": f"E{leave_request.employee_id}",
            "email": "",
        }
    )
    days = leave_days(leave_request)
    balance_field = _balance_field_for_leave_type(leave_request.leave_type)
    balance_remaining = None
    balance_sufficient = None
    if balance_field:
        balance = get_or_create_leave_balance(leave_request.employee_id)
        balance_remaining = getattr(balance, balance_field)
        balance_sufficient = balance_remaining >= days

    return {
        "id": leave_request.id,
        "employee_id": leave_request.employee_id,
        "employee": employee,
        "leave_type": leave_request.leave_type,
        "leave_type_label": leave_type_label(leave_request.leave_type),
        "date_range": f"{leave_request.from_date.strftime('%b %d')} - {leave_request.to_date.strftime('%b %d, %Y')}",
        "from_date": leave_request.from_date.isoformat(),
        "to_date": leave_request.to_date.isoformat(),
        "days": days,
        "balance_remaining": balance_remaining,
        "balance_sufficient": balance_sufficient,
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


def _leave_id_from_url_kwargs(kwargs):
    for key in ("leave_id", "pk", "id"):
        value = kwargs.get(key)
        if value is not None:
            return int(value)
    return None


def _normalized_leave_status(leave_request):
    return (leave_request.status or "").strip().upper()


def _pending_leave_requests_qs():
    return LeaveRequest.objects.filter(status__iexact=LeaveRequest.Status.PENDING)


def _validation_error_response(exc):
    detail = exc.detail
    message = "Validation failed."
    if isinstance(detail, dict):
        for value in detail.values():
            if isinstance(value, list) and value:
                message = str(value[0])
                break
            if isinstance(value, str) and value:
                message = value
                break
    elif detail:
        message = str(detail)
    return Response(
        {"success": False, "message": message, "errors": detail},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _get_leave_for_admin_action(request, **url_kwargs):
    leave_id = _leave_id_from_url_kwargs(url_kwargs)
    if leave_id is None:
        return None, Response(
            {"success": False, "message": "Leave request id is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    leave_request = LeaveRequest.objects.filter(pk=leave_id).first()
    if leave_request is None:
        return None, Response(
            {
                "success": False,
                "message": f"Leave request #{leave_id} was not found.",
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    resolver = resolver_from_request(request)
    seed_staff_resolver(resolver, token=request.headers.get("Authorization"))
    current_status = _normalized_leave_status(leave_request)

    if current_status == LeaveRequest.Status.APPROVED:
        return leave_request, Response(
            {
                "success": True,
                "message": "Leave request is already approved.",
                "data": leave_card(leave_request, resolver),
            }
        )

    if current_status == LeaveRequest.Status.REJECTED:
        return None, Response(
            {
                "success": False,
                "message": "This leave request was already rejected.",
            },
            status=status.HTTP_409_CONFLICT,
        )

    if current_status != LeaveRequest.Status.PENDING:
        return None, Response(
            {
                "success": False,
                "message": (
                    f"Leave request cannot be processed (status: {leave_request.status})."
                ),
            },
            status=status.HTTP_409_CONFLICT,
        )

    if leave_request.status != LeaveRequest.Status.PENDING:
        leave_request.status = LeaveRequest.Status.PENDING
        leave_request.save(update_fields=["status", "updated_at"])

    return leave_request, None, resolver


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
        notify_admins_leave_submitted(leave_request)
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
        leave_requests = apply_leave_history_retention(leave_requests)
        if status_filter in LeaveRequest.Status.values:
            leave_requests = leave_requests.filter(status=status_filter)
        balance = get_or_create_leave_balance(employee_id)
        request_cards = [leave_card(item) for item in leave_requests]
        return paginated_list_response(
            request,
            request_cards,
            message="Leave history fetched successfully.",
            list_key="requests",
            retention_months=LEAVE_HISTORY_RETENTION_MONTHS,
            balances=balance_cards(balance),
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
        holiday_items = [holiday_card(item) for item in holidays]
        return paginated_list_response(
            request,
            holiday_items,
            message="Holidays fetched successfully.",
            list_key="holidays",
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
        seed_staff_resolver(resolver, token=request.headers.get("Authorization"))
        pending = list(_pending_leave_requests_qs().order_by("-created_at"))
        pending_cards = [leave_card(item, resolver) for item in pending]
        return paginated_list_response(
            request,
            pending_cards,
            message="Pending leave requests fetched successfully.",
            list_key="requests",
            pending_count=len(pending),
        )


class AdminLeaveListAPIView(APIView):
    permission_classes = [IsAttendanceAdmin]

    @extend_schema(
        tags=["Admin Leaves"],
        summary="List leave requests by status",
    )
    def get(self, request):
        resolver = resolver_from_request(request)
        seed_staff_resolver(resolver, token=request.headers.get("Authorization"))
        status_filter = (request.query_params.get("status") or "").upper()
        leave_requests = apply_leave_history_retention(LeaveRequest.objects.all()).order_by(
            "-created_at"
        )
        if status_filter in LeaveRequest.Status.values:
            leave_requests = leave_requests.filter(status=status_filter)

        counts = {
            LeaveRequest.Status.PENDING: 0,
            LeaveRequest.Status.APPROVED: 0,
            LeaveRequest.Status.REJECTED: 0,
        }
        for row in apply_leave_history_retention(LeaveRequest.objects.all()).values(
            "status"
        ).annotate(total=Count("id")):
            counts[row["status"]] = row["total"]

        request_cards = [leave_card(item, resolver) for item in leave_requests]
        return paginated_list_response(
            request,
            request_cards,
            message="Leave requests fetched successfully.",
            list_key="requests",
            retention_months=LEAVE_HISTORY_RETENTION_MONTHS,
            counts=counts,
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
    def post(self, request, *args, **kwargs):
        leave_request, error_response, resolver = _get_leave_for_admin_action(
            request, **kwargs
        )
        if error_response is not None:
            return error_response

        serializer = LeaveApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            deduct_leave_balance(leave_request)
        except serializers.ValidationError as exc:
            return _validation_error_response(exc)

        leave_request.status = LeaveRequest.Status.APPROVED
        leave_request.approved_by = employee_id_from_request(request)
        leave_request.approved_at = timezone.now()
        leave_request.rejection_reason = ""
        leave_request.save(update_fields=["status", "approved_by", "approved_at", "rejection_reason", "updated_at"])
        notify_employee_leave_decision(leave_request, approved=True)

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
    def post(self, request, *args, **kwargs):
        leave_request, error_response, resolver = _get_leave_for_admin_action(
            request, **kwargs
        )
        if error_response is not None:
            return error_response

        serializer = LeaveApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rejection_reason = serializer.validated_data.get("rejection_reason", "")

        leave_request.status = LeaveRequest.Status.REJECTED
        leave_request.approved_by = employee_id_from_request(request)
        leave_request.approved_at = timezone.now()
        leave_request.rejection_reason = rejection_reason
        leave_request.save(update_fields=["status", "approved_by", "approved_at", "rejection_reason", "updated_at"])
        notify_employee_leave_decision(
            leave_request,
            approved=False,
            rejection_reason=rejection_reason,
        )

        return Response(
            {
                "success": True,
                "message": "Leave request rejected successfully.",
                "data": leave_card(leave_request, resolver),
            }
        )


class AdminHolidayListCreateAPIView(APIView):
    permission_classes = [IsAttendanceAdmin]

    @extend_schema(
        tags=["Admin Holidays"],
        summary="List company holidays in a date range",
    )
    def get(self, request):
        from django.utils.dateparse import parse_date

        from apps.attendance.calendar import holiday_info_for_date, iter_dates

        start_date = parse_date(request.query_params.get("start_date", ""))
        end_date = parse_date(request.query_params.get("end_date", ""))
        holidays = Holiday.objects.filter(is_active=True).order_by("holiday_date")
        if start_date:
            holidays = holidays.filter(holiday_date__gte=start_date)
        if end_date:
            holidays = holidays.filter(holiday_date__lte=end_date)

        admin_holidays = [holiday_card(item) for item in holidays]
        recurring = []
        if start_date and end_date:
            for day in iter_dates(start_date, end_date):
                is_holiday, name = holiday_info_for_date(day)
                if is_holiday and not Holiday.objects.filter(holiday_date=day, is_active=True).exists():
                    recurring.append(
                        {
                            "id": None,
                            "name": name,
                            "date": day.isoformat(),
                            "day": day.strftime("%A"),
                            "description": "Recurring office holiday",
                            "source": "recurring",
                        }
                    )

        admin_page, admin_paginator = paginate_request(request, admin_holidays)
        paged_admin_holidays = admin_page if admin_page is not None else admin_holidays
        payload = {
            "success": True,
            "message": "Holidays fetched successfully.",
            "admin_holidays": paged_admin_holidays,
            "recurring_holidays": recurring,
        }
        if admin_paginator is not None:
            payload["meta"] = admin_paginator._meta_payload()
        return Response(payload)

    @extend_schema(
        tags=["Admin Holidays"],
        summary="Mark a date as company holiday",
        request=HolidaySerializer,
    )
    def post(self, request):
        from apps.attendance.calendar import recurring_holiday_label

        serializer = HolidaySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        holiday_date = serializer.validated_data["holiday_date"]
        recurring = recurring_holiday_label(holiday_date)
        if recurring:
            return Response(
                {
                    "success": True,
                    "message": f"{holiday_date.isoformat()} is already a recurring holiday ({recurring}).",
                    "data": {
                        "date": holiday_date.isoformat(),
                        "name": recurring,
                        "source": "recurring",
                    },
                }
            )

        holiday, created = Holiday.objects.update_or_create(
            holiday_date=holiday_date,
            defaults={
                "name": serializer.validated_data["name"],
                "description": serializer.validated_data.get("description", ""),
                "is_active": True,
            },
        )
        action = "marked" if created else "updated"
        return Response(
            {
                "success": True,
                "message": f"Date {holiday_date.isoformat()} {action} as company holiday.",
                "data": holiday_card(holiday),
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class AdminHolidayDetailAPIView(APIView):
    permission_classes = [IsAttendanceAdmin]

    @extend_schema(
        tags=["Admin Holidays"],
        summary="Remove an admin-defined company holiday",
    )
    def delete(self, request, holiday_id):
        holiday = Holiday.objects.filter(pk=holiday_id).first()
        if holiday is None:
            return Response(
                {"success": False, "message": "Holiday not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        holiday.is_active = False
        holiday.save(update_fields=["is_active"])
        return Response(
            {
                "success": True,
                "message": f"Holiday {holiday.holiday_date.isoformat()} removed.",
            }
        )
