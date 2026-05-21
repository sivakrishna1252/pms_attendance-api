import csv
from datetime import timedelta

from django.db.models import Count, Sum
from django.http import HttpResponse
from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.attendance.models import AttendanceLog
from apps.attendance.serializers import AttendanceLogSerializer
from apps.attendance.views import parse_staff_ids_param
from apps.authentication.permissions import IsAttendanceAdmin
from apps.common.employee_profiles import resolver_from_request, seed_staff_resolver
from apps.leaves.models import LeaveRequest
from apps.leaves.serializers import LeaveRequestSerializer


REPORT_TYPE_ATTENDANCE = "attendance_summary"
REPORT_TYPE_LEAVE = "leave_summary"
REPORT_TYPE_COMBINED = "combined_summary"
EXPORT_FORMAT_CSV = "csv"
EXPORT_FORMAT_EXCEL = "excel"
EXPORT_FORMAT_PDF = "pdf"


def _duration_to_hours(value):
    if not value:
        return 0.0
    return round(value.total_seconds() / 3600, 2)


def _format_duration(value):
    if not value:
        return "0h 0m"

    total_seconds = int(value.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes}m"


def _leave_days(leave):
    return max((leave.to_date - leave.from_date).days + 1, 1)


def _employee_name(resolver, employee_id):
    if resolver is None:
        return f"Employee {employee_id}"
    return resolver.display_name(employee_id)


def _apply_employee_filters(queryset, *, employee_id=None, staff_ids=None):
    if staff_ids:
        return queryset.filter(employee_id__in=staff_ids)
    if employee_id:
        return queryset.filter(employee_id=employee_id)
    return queryset


def _write_csv_response(filename, columns, rows):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(column, "") for column in columns])
    return response


def _escape_pdf_text(value):
    return str(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _write_pdf_response(filename, title, columns, rows):
    lines = [title, "", " | ".join(columns)]
    lines.extend(" | ".join(str(row.get(column, "")) for column in columns) for row in rows[:40])

    content_lines = ["BT", "/F1 10 Tf", "40 780 Td"]
    for index, line in enumerate(lines):
        if index:
            content_lines.append("0 -16 Td")
        content_lines.append(f"({_escape_pdf_text(line[:115])}) Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("utf-8")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")

    xref_start = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode("ascii")
    )

    response = HttpResponse(bytes(pdf), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


class AttendanceReportsAPIView(APIView):
    permission_classes = [IsAttendanceAdmin]

    @extend_schema(
        tags=["Admin Reports"],
        summary="Attendance and leave reports",
        responses={200: OpenApiTypes.OBJECT},
        parameters=[
            OpenApiParameter("start_date", OpenApiTypes.DATE, description="Filter from date, example: 2026-05-01"),
            OpenApiParameter("end_date", OpenApiTypes.DATE, description="Filter to date, example: 2026-05-31"),
            OpenApiParameter("employee_id", OpenApiTypes.INT, description="Optional single PMS user id"),
            OpenApiParameter(
                "staff_ids",
                OpenApiTypes.STR,
                description="Comma-separated employee ids for multi-employee reports",
            ),
            OpenApiParameter(
                "report_type",
                OpenApiTypes.STR,
                description="attendance_summary, leave_summary, or combined_summary",
            ),
            OpenApiParameter(
                "export",
                OpenApiTypes.STR,
                description="Optional export format. Supported: pdf, csv, excel. Excel returns CSV-compatible data.",
            ),
        ],
    )
    def get(self, request):
        attendance = AttendanceLog.objects.all()
        leaves = LeaveRequest.objects.all()

        start_date = parse_date(request.query_params.get("start_date", ""))
        end_date = parse_date(request.query_params.get("end_date", ""))
        employee_id = request.query_params.get("employee_id")
        staff_ids = parse_staff_ids_param(request.query_params.get("staff_ids", ""))
        report_type = request.query_params.get("report_type", REPORT_TYPE_ATTENDANCE)
        export_format = request.query_params.get("export", "").lower()

        if start_date:
            attendance = attendance.filter(attendance_date__gte=start_date)
            leaves = leaves.filter(from_date__gte=start_date)
        if end_date:
            attendance = attendance.filter(attendance_date__lte=end_date)
            leaves = leaves.filter(to_date__lte=end_date)

        attendance = _apply_employee_filters(
            attendance,
            employee_id=employee_id,
            staff_ids=staff_ids,
        )
        leaves = _apply_employee_filters(
            leaves,
            employee_id=employee_id,
            staff_ids=staff_ids,
        )

        resolver = resolver_from_request(request)
        seed_staff_resolver(resolver)

        attendance_summary = attendance.aggregate(
            total_records=Count("id"),
            total_work_hours=Sum("total_work_hours"),
        )
        total_work_hours = attendance_summary["total_work_hours"] or timedelta()
        leave_summary = leaves.values("status").annotate(total=Count("id")).order_by("status")
        leave_days_by_status = {}
        for leave in leaves:
            leave_days_by_status[leave.status] = leave_days_by_status.get(leave.status, 0) + _leave_days(leave)

        status_counts = {
            item["status"]: item["total"]
            for item in attendance.values("status").annotate(total=Count("id")).order_by("status")
        }
        leave_status_counts = {item["status"]: item["total"] for item in leave_summary}

        if report_type == REPORT_TYPE_LEAVE:
            preview_columns = [
                "employee_name",
                "leave_type",
                "from_date",
                "to_date",
                "days",
                "status",
                "applied_on",
            ]
            preview_rows = [
                {
                    "employee_name": _employee_name(resolver, leave.employee_id),
                    "leave_type": leave.leave_type,
                    "from_date": leave.from_date.isoformat(),
                    "to_date": leave.to_date.isoformat(),
                    "days": _leave_days(leave),
                    "status": leave.status,
                    "applied_on": leave.created_at.date().isoformat(),
                }
                for leave in leaves.order_by("-created_at")[:200]
            ]
            report_title = "Leave Summary"
        elif report_type == REPORT_TYPE_COMBINED:
            preview_columns = ["metric", "value"]
            preview_rows = [
                {"metric": "Attendance Records", "value": attendance_summary["total_records"]},
                {"metric": "Total Work Hours", "value": _format_duration(total_work_hours)},
                {"metric": "Present Records", "value": status_counts.get(AttendanceLog.Status.PRESENT, 0)},
                {"metric": "Checked Out Records", "value": status_counts.get(AttendanceLog.Status.CHECKED_OUT, 0)},
                {"metric": "Leave Requests", "value": leaves.count()},
                {"metric": "Approved Leaves", "value": leave_status_counts.get(LeaveRequest.Status.APPROVED, 0)},
                {"metric": "Pending Leaves", "value": leave_status_counts.get(LeaveRequest.Status.PENDING, 0)},
                {"metric": "Rejected Leaves", "value": leave_status_counts.get(LeaveRequest.Status.REJECTED, 0)},
            ]
            report_title = "Combined Attendance and Leave Summary"
        else:
            report_type = REPORT_TYPE_ATTENDANCE
            preview_columns = [
                "employee_name",
                "date",
                "status",
                "check_in",
                "check_out",
                "work_hours",
            ]
            preview_rows = [
                {
                    "employee_name": _employee_name(resolver, log.employee_id),
                    "date": log.attendance_date.isoformat(),
                    "status": log.status,
                    "check_in": log.check_in_time.isoformat() if log.check_in_time else "",
                    "check_out": log.check_out_time.isoformat() if log.check_out_time else "",
                    "work_hours": _format_duration(log.total_work_hours),
                }
                for log in attendance.order_by("-attendance_date", "-check_in_time")[:200]
            ]
            report_title = "Attendance Summary"

        export_column_labels = {
            "employee_name": "Employee Name",
            "leave_type": "Leave Type",
            "from_date": "From Date",
            "to_date": "To Date",
            "days": "Days",
            "status": "Status",
            "applied_on": "Applied On",
            "date": "Date",
            "check_in": "Check In",
            "check_out": "Check Out",
            "work_hours": "Work Hours",
            "metric": "Metric",
            "value": "Value",
        }
        export_headers = [export_column_labels.get(col, col.replace("_", " ").title()) for col in preview_columns]

        if export_format in {EXPORT_FORMAT_CSV, EXPORT_FORMAT_EXCEL}:
            extension = "csv" if export_format == EXPORT_FORMAT_CSV else "xls"
            filename = f"{report_type}_{start_date or 'start'}_{end_date or 'end'}.{extension}"
            return _write_csv_response(filename, export_headers, preview_rows)
        if export_format == EXPORT_FORMAT_PDF:
            filename = f"{report_type}_{start_date or 'start'}_{end_date or 'end'}.pdf"
            return _write_pdf_response(filename, report_title, export_headers, preview_rows)

        return Response(
            {
                "filters": {
                    "start_date": start_date,
                    "end_date": end_date,
                    "employee_id": employee_id,
                    "staff_ids": staff_ids,
                    "report_type": report_type,
                },
                "report": {
                    "title": report_title,
                    "preview_title": f"{report_title} ({start_date or 'All'} - {end_date or 'All'})",
                    "columns": preview_columns,
                    "rows": preview_rows,
                    "row_count": len(preview_rows),
                    "export_formats": [EXPORT_FORMAT_PDF, EXPORT_FORMAT_EXCEL, EXPORT_FORMAT_CSV],
                },
                "summary": {
                    "total_attendance_records": attendance_summary["total_records"],
                    "total_work_hours": total_work_hours,
                    "total_work_hours_display": _format_duration(total_work_hours),
                    "total_work_hours_decimal": _duration_to_hours(total_work_hours),
                    "attendance_by_status": status_counts,
                    "total_leave_requests": leaves.count(),
                    "leave_requests_by_status": list(leave_summary),
                    "leave_days_by_status": leave_days_by_status,
                },
                "attendance": AttendanceLogSerializer(attendance[:200], many=True).data,
                "leave_requests": LeaveRequestSerializer(leaves[:200], many=True).data,
            }
        )
