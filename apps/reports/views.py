import csv
from datetime import timedelta

from django.db.models import Count, Sum
from django.http import HttpResponse
from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.attendance.calendar import REPORT_RETENTION_MONTHS, resolve_report_date_range
from apps.attendance.models import AttendanceLog
from apps.attendance.views import format_time, parse_staff_ids_param
from apps.authentication.permissions import IsAttendanceAdmin
from apps.common.employee_profiles import resolver_from_request, seed_staff_resolver
from apps.leaves.models import LeaveRequest
from apps.reports.generators import (
    build_attendance_report_rows,
    build_attendance_summary_row,
    build_leave_report_rows,
    compute_attendance_status_totals,
)

REPORT_TYPE_ATTENDANCE = "attendance_summary"
REPORT_TYPE_LEAVE = "leave_summary"
REPORT_TYPE_COMBINED = "combined_summary"
EXPORT_FORMAT_CSV = "csv"
EXPORT_FORMAT_EXCEL = "excel"
EXPORT_FORMAT_PDF = "pdf"
PREVIEW_ROW_LIMIT = 200
EXPORT_ROW_LIMIT = 5000


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


def _format_date(value):
    if not value:
        return ""
    return value.strftime("%d %b %Y")


def _employee_name(resolver, employee_id, cache):
    employee_id = int(employee_id)
    if employee_id in cache:
        return cache[employee_id]
    if resolver is None:
        name = f"Employee {employee_id}"
    else:
        name = resolver.display_name(employee_id)
    cache[employee_id] = name
    return name


def _apply_employee_filters(queryset, *, employee_id=None, staff_ids=None):
    if staff_ids:
        return queryset.filter(employee_id__in=staff_ids)
    if employee_id:
        return queryset.filter(employee_id=employee_id)
    return queryset


def _write_csv_response(filename, columns, rows, *, excel_compatible=False):
    content_type = "text/csv"
    if excel_compatible:
        content_type = "application/vnd.ms-excel"

    response = HttpResponse(content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")

    writer = csv.writer(response)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(column, "") for column in columns])
    return response


def _escape_pdf_text(value):
    return str(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _write_pdf_response(filename, title, columns, rows):
    lines = [title, "", " | ".join(columns)]
    lines.extend(" | ".join(str(row.get(column, "")) for column in columns) for row in rows[:120])
    if len(rows) > 120:
        lines.append(f"... and {len(rows) - 120} more rows. Download CSV/Excel for full data.")

    content_lines = ["BT", "/F1 9 Tf", "40 780 Td"]
    for index, line in enumerate(lines):
        if index:
            content_lines.append("0 -14 Td")
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
                description="Optional export format. Supported: pdf, csv, excel.",
            ),
        ],
    )
    def get(self, request):
        raw_start = parse_date(request.query_params.get("start_date", ""))
        raw_end = parse_date(request.query_params.get("end_date", ""))
        report_type = request.query_params.get("report_type", REPORT_TYPE_ATTENDANCE)
        allow_future_end = report_type in {REPORT_TYPE_LEAVE, REPORT_TYPE_COMBINED}
        start_date, end_date, warnings = resolve_report_date_range(
            raw_start,
            raw_end,
            allow_future_end=allow_future_end,
        )

        employee_id = request.query_params.get("employee_id")
        staff_ids = parse_staff_ids_param(request.query_params.get("staff_ids", ""))
        export_format = request.query_params.get("export", "").lower()
        needs_leave_stats = report_type in {REPORT_TYPE_LEAVE, REPORT_TYPE_COMBINED}

        attendance = AttendanceLog.objects.filter(
            attendance_date__gte=start_date,
            attendance_date__lte=end_date,
        )
        attendance = _apply_employee_filters(
            attendance,
            employee_id=employee_id,
            staff_ids=staff_ids,
        )

        leaves = LeaveRequest.objects.none()
        if needs_leave_stats:
            leaves = LeaveRequest.objects.filter(
                from_date__lte=end_date,
                to_date__gte=start_date,
            )
            leaves = _apply_employee_filters(
                leaves,
                employee_id=employee_id,
                staff_ids=staff_ids,
            )
            if report_type == REPORT_TYPE_LEAVE:
                leaves = leaves.filter(
                    status__in=[
                        LeaveRequest.Status.APPROVED,
                        LeaveRequest.Status.REJECTED,
                    ],
                )

        resolver = resolver_from_request(request)
        seed_staff_resolver(resolver, token=request.headers.get("Authorization"))
        name_cache = {}

        attendance_summary = attendance.aggregate(
            total_records=Count("id"),
            total_work_hours=Sum("total_work_hours"),
        )
        total_work_hours = attendance_summary["total_work_hours"] or timedelta()

        status_counts = {
            item["status"]: item["total"]
            for item in attendance.values("status").annotate(total=Count("id")).order_by("status")
        }

        leave_summary = []
        leave_status_counts = {}
        leave_days_by_status = {}
        if needs_leave_stats:
            leave_summary = list(
                leaves.values("status").annotate(total=Count("id")).order_by("status"),
            )
            leave_status_counts = {item["status"]: item["total"] for item in leave_summary}
            if report_type == REPORT_TYPE_COMBINED:
                for leave in leaves.only("status", "from_date", "to_date"):
                    leave_days_by_status[leave.status] = (
                        leave_days_by_status.get(leave.status, 0) + _leave_days(leave)
                    )

        attendance_status_totals = None

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
            full_rows = build_leave_report_rows(
                leaves_queryset=leaves,
                resolver=resolver,
                name_cache=name_cache,
            )
            report_title = "Leave Summary"
        elif report_type == REPORT_TYPE_COMBINED:
            preview_columns = ["metric", "value"]
            attendance_rows = build_attendance_report_rows(
                start_date=start_date,
                end_date=end_date,
                resolver=resolver,
                employee_id=employee_id,
                staff_ids=staff_ids,
                attendance_queryset=attendance,
            )
            status_totals = {}
            for row in attendance_rows:
                status_totals[row["status"]] = status_totals.get(row["status"], 0) + 1
            full_rows = [
                {"metric": "Attendance Records", "value": attendance_summary["total_records"]},
                {"metric": "Total Work Hours", "value": _format_duration(total_work_hours)},
                {"metric": "Present Days", "value": status_totals.get("Present", 0)},
                {"metric": "Late Days", "value": status_totals.get("Late", 0)},
                {"metric": "Absent Days", "value": status_totals.get("Absent", 0)},
                {"metric": "Holiday Days", "value": status_totals.get("Holiday", 0)},
                {"metric": "WFH Days", "value": status_totals.get("WFH", 0)},
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
                "day",
                "status",
                "note",
                "check_in",
                "check_out",
                "work_hours",
            ]
            full_rows = build_attendance_report_rows(
                start_date=start_date,
                end_date=end_date,
                resolver=resolver,
                employee_id=employee_id,
                staff_ids=staff_ids,
                attendance_queryset=attendance,
            )
            attendance_status_totals = compute_attendance_status_totals(
                full_rows,
                start_date=start_date,
                end_date=end_date,
            )
            report_title = "Attendance Summary"

        export_rows = full_rows[:EXPORT_ROW_LIMIT]
        preview_rows = full_rows[:PREVIEW_ROW_LIMIT]
        if report_type == REPORT_TYPE_ATTENDANCE and attendance_status_totals is not None:
            export_rows = export_rows + [build_attendance_summary_row(attendance_status_totals)]

        export_column_labels = {
            "employee_name": "Employee Name",
            "leave_type": "Leave Type",
            "from_date": "From Date",
            "to_date": "To Date",
            "days": "Days",
            "status": "Status",
            "applied_on": "Applied On",
            "date": "Date",
            "day": "Day",
            "note": "Note",
            "check_in": "Check In",
            "check_out": "Check Out",
            "work_hours": "Work Hours",
            "metric": "Metric",
            "value": "Value",
        }
        export_headers = [export_column_labels.get(col, col.replace("_", " ").title()) for col in preview_columns]

        if export_format in {EXPORT_FORMAT_CSV, EXPORT_FORMAT_EXCEL}:
            extension = "csv" if export_format == EXPORT_FORMAT_CSV else "xls"
            filename = f"{report_type}_{start_date}_{end_date}.{extension}"
            return _write_csv_response(
                filename,
                export_headers,
                export_rows,
                excel_compatible=export_format == EXPORT_FORMAT_EXCEL,
            )
        if export_format == EXPORT_FORMAT_PDF:
            filename = f"{report_type}_{start_date}_{end_date}.pdf"
            return _write_pdf_response(
                filename,
                f"{report_title} ({start_date} - {end_date})",
                export_headers,
                export_rows,
            )

        return Response(
            {
                "filters": {
                    "start_date": start_date,
                    "end_date": end_date,
                    "employee_id": employee_id,
                    "staff_ids": staff_ids,
                    "report_type": report_type,
                    "retention_months": REPORT_RETENTION_MONTHS,
                },
                "warnings": warnings,
                "report": {
                    "title": report_title,
                    "preview_title": f"{report_title} ({start_date} - {end_date})",
                    "columns": preview_columns,
                    "rows": preview_rows,
                    "row_count": len(full_rows),
                    "summary_totals": attendance_status_totals,
                    "export_formats": [EXPORT_FORMAT_PDF, EXPORT_FORMAT_EXCEL, EXPORT_FORMAT_CSV],
                },
                "summary": {
                    "total_attendance_records": attendance_summary["total_records"],
                    "total_work_hours": total_work_hours,
                    "total_work_hours_display": _format_duration(total_work_hours),
                    "total_work_hours_decimal": _duration_to_hours(total_work_hours),
                    "attendance_by_status": status_counts,
                    "total_leave_requests": leaves.count() if needs_leave_stats else 0,
                    "leave_requests_by_status": leave_summary,
                    "leave_days_by_status": leave_days_by_status,
                },
            }
        )
