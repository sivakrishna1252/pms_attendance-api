# Attendance Microservice API Testing Guide

Base URL:

```text
http://localhost:9000
```

Swagger:

```text
http://localhost:9000/api/docs/
```

ReDoc:

```text
http://localhost:9000/api/redoc/
```

OpenAPI schema:

```text
http://localhost:9000/api/schema/
```

## Authentication

Login happens in the PMS backend, not in this service.

First call PMS login:

```http
POST http://localhost:8000/api/auth/login
```

Use the PMS `access` token for every attendance API:

```http
Authorization: Bearer <PMS_ACCESS_TOKEN>
Content-Type: application/json
```

The attendance backend validates this token and reads PMS `user_id` as `employee_id`.

## 1. Health Check

```http
GET /api/health/
```

No token required.

Response:

```json
{
  "status": "ok",
  "service": "attendance"
}
```

## 2. Employee Check-In

```http
POST /api/attendance/check-in/
```

Headers:

```http
Authorization: Bearer <PMS_ACCESS_TOKEN>
```

Body:

```json
{}
```

Success response `201`:

```json
{
  "id": 1,
  "employee_id": 5,
  "attendance_date": "2026-05-13",
  "check_in_time": "2026-05-13T09:02:00Z",
  "check_out_time": null,
  "status": "PRESENT",
  "total_work_hours": null,
  "created_at": "2026-05-13T09:02:00Z",
  "updated_at": "2026-05-13T09:02:00Z"
}
```

Already checked-in response `400`:

```json
{
  "detail": "Already checked in today."
}
```

## 3. Employee Check-Out

```http
POST /api/attendance/check-out/
```

Headers:

```http
Authorization: Bearer <PMS_ACCESS_TOKEN>
```

Body:

```json
{}
```

Success response `200`:

```json
{
  "id": 1,
  "employee_id": 5,
  "attendance_date": "2026-05-13",
  "check_in_time": "2026-05-13T09:02:00Z",
  "check_out_time": "2026-05-13T18:05:00Z",
  "status": "CHECKED_OUT",
  "total_work_hours": "09:03:00",
  "created_at": "2026-05-13T09:02:00Z",
  "updated_at": "2026-05-13T18:05:00Z"
}
```

If check-in was not done:

```json
{
  "detail": "Check-in is required before check-out."
}
```

If already checked-out:

```json
{
  "detail": "Already checked out today."
}
```

## 4. Today's Attendance

```http
GET /api/attendance/today/
```

Headers:

```http
Authorization: Bearer <PMS_ACCESS_TOKEN>
```

Success response `200`:

```json
{
  "id": 1,
  "employee_id": 5,
  "attendance_date": "2026-05-13",
  "check_in_time": "2026-05-13T09:02:00Z",
  "check_out_time": null,
  "status": "PRESENT",
  "total_work_hours": null,
  "created_at": "2026-05-13T09:02:00Z",
  "updated_at": "2026-05-13T09:02:00Z"
}
```

No record response `404`:

```json
{
  "detail": "No attendance record for today."
}
```

## 5. Attendance History

```http
GET /api/attendance/history/
```

Headers:

```http
Authorization: Bearer <PMS_ACCESS_TOKEN>
```

Response `200`:

```json
[
  {
    "id": 1,
    "employee_id": 5,
    "attendance_date": "2026-05-13",
    "check_in_time": "2026-05-13T09:02:00Z",
    "check_out_time": "2026-05-13T18:05:00Z",
    "status": "CHECKED_OUT",
    "total_work_hours": "09:03:00",
    "created_at": "2026-05-13T09:02:00Z",
    "updated_at": "2026-05-13T18:05:00Z"
  }
]
```

## 6. Apply Leave

```http
POST /api/leaves/apply/
```

Headers:

```http
Authorization: Bearer <PMS_ACCESS_TOKEN>
Content-Type: application/json
```

Body:

```json
{
  "leave_type": "ANNUAL",
  "from_date": "2026-05-20",
  "to_date": "2026-05-24",
  "reason": "Family vacation"
}
```

Allowed `leave_type` values:

```text
ANNUAL
SICK
CASUAL
COMPENSATORY
```

Success response `201`:

```json
{
  "id": 1,
  "employee_id": 5,
  "leave_type": "ANNUAL",
  "from_date": "2026-05-20",
  "to_date": "2026-05-24",
  "reason": "Family vacation",
  "status": "PENDING",
  "approved_by": null,
  "approved_at": null,
  "rejection_reason": "",
  "created_at": "2026-05-13T10:00:00Z",
  "updated_at": "2026-05-13T10:00:00Z"
}
```

Invalid date response `400`:

```json
{
  "to_date": [
    "To date cannot be before from date."
  ]
}
```

## 7. Leave History

```http
GET /api/leaves/history/
```

Headers:

```http
Authorization: Bearer <PMS_ACCESS_TOKEN>
```

Response `200`:

```json
[
  {
    "id": 1,
    "employee_id": 5,
    "leave_type": "ANNUAL",
    "from_date": "2026-05-20",
    "to_date": "2026-05-24",
    "reason": "Family vacation",
    "status": "PENDING",
    "approved_by": null,
    "approved_at": null,
    "rejection_reason": "",
    "created_at": "2026-05-13T10:00:00Z",
    "updated_at": "2026-05-13T10:00:00Z"
  }
]
```

## 8. Admin Pending Leave Requests

```http
GET /api/admin/leaves/pending/
```

Headers:

```http
Authorization: Bearer <ADMIN_PMS_ACCESS_TOKEN>
```

Response `200`:

```json
[
  {
    "id": 1,
    "employee_id": 5,
    "leave_type": "ANNUAL",
    "from_date": "2026-05-20",
    "to_date": "2026-05-24",
    "reason": "Family vacation",
    "status": "PENDING",
    "approved_by": null,
    "approved_at": null,
    "rejection_reason": "",
    "created_at": "2026-05-13T10:00:00Z",
    "updated_at": "2026-05-13T10:00:00Z"
  }
]
```

Employee token response:

```json
{
  "detail": "You do not have permission to perform this action."
}
```

## 9. Admin Approve Leave

```http
POST /api/admin/leaves/1/approve/
```

Headers:

```http
Authorization: Bearer <ADMIN_PMS_ACCESS_TOKEN>
```

Body:

```json
{}
```

Response `200`:

```json
{
  "id": 1,
  "employee_id": 5,
  "leave_type": "ANNUAL",
  "from_date": "2026-05-20",
  "to_date": "2026-05-24",
  "reason": "Family vacation",
  "status": "APPROVED",
  "approved_by": 1,
  "approved_at": "2026-05-13T10:30:00Z",
  "rejection_reason": "",
  "created_at": "2026-05-13T10:00:00Z",
  "updated_at": "2026-05-13T10:30:00Z"
}
```

## 10. Admin Reject Leave

```http
POST /api/admin/leaves/1/reject/
```

Headers:

```http
Authorization: Bearer <ADMIN_PMS_ACCESS_TOKEN>
Content-Type: application/json
```

Body:

```json
{
  "rejection_reason": "Project deadline conflict"
}
```

Response `200`:

```json
{
  "id": 1,
  "employee_id": 5,
  "leave_type": "ANNUAL",
  "from_date": "2026-05-20",
  "to_date": "2026-05-24",
  "reason": "Family vacation",
  "status": "REJECTED",
  "approved_by": 1,
  "approved_at": "2026-05-13T10:30:00Z",
  "rejection_reason": "Project deadline conflict",
  "created_at": "2026-05-13T10:00:00Z",
  "updated_at": "2026-05-13T10:30:00Z"
}
```

## 11. Admin Reports

```http
GET /api/admin/reports/
```

Optional query params:

```text
start_date=2026-05-01
end_date=2026-05-31
employee_id=5
report_type=attendance_summary
department=all
export=csv
```

Example:

```http
GET /api/admin/reports/?report_type=attendance_summary&department=all&start_date=2026-05-01&end_date=2026-05-31
```

Headers:

```http
Authorization: Bearer <ADMIN_PMS_ACCESS_TOKEN>
```

Response `200`:

```json
{
  "filters": {
    "start_date": "2026-05-01",
    "end_date": "2026-05-31",
    "employee_id": null,
    "report_type": "attendance_summary",
    "department": "all",
    "department_filter_supported": true
  },
  "report": {
    "title": "Attendance Summary",
    "preview_title": "Attendance Summary (2026-05-01 - 2026-05-31)",
    "columns": [
      "employee_id",
      "date",
      "status",
      "check_in",
      "check_out",
      "work_hours"
    ],
    "rows": [],
    "row_count": 0,
    "export_formats": [
      "pdf",
      "excel",
      "csv",
    ]
  },
  "summary": {
    "total_attendance_records": 10,
    "total_work_hours": "90:30:00",
    "total_work_hours_display": "90h 30m",
    "total_work_hours_decimal": 90.5,
    "attendance_by_status": {
      "PRESENT": 4,
      "CHECKED_OUT": 6
    },
    "total_leave_requests": 3,
    "leave_requests_by_status": [
      {
        "status": "APPROVED",
        "total": 2
      },
      {
        "status": "PENDING",
        "total": 1
      }
    ],
    "leave_days_by_status": {
      "APPROVED": 2,
      "PENDING": 1
    }
  },
  "attendance": [],
  "leave_requests": [],
  "warnings": []
}
```

Export examples:

```http
GET /api/admin/reports/?report_type=attendance_summary&start_date=2026-05-01&end_date=2026-05-31&export=csv
GET /api/admin/reports/?report_type=leave_summary&start_date=2026-05-01&end_date=2026-05-31&export=excel
GET /api/admin/reports/?report_type=combined_summary&start_date=2026-05-01&end_date=2026-05-31&export=pdf
```

## Common Errors

Missing token:

```json
{
  "detail": "Authentication credentials were not provided."
}
```

Invalid or expired token:

```json
{
  "detail": "Given token not valid for any token type"
}
```

Employee trying admin API:

```json
{
  "detail": "You do not have permission to perform this action."
}
```

## Frontend Mapping

Use PMS backend:

```text
Login, employees, tasks, projects
http://localhost:8000
```

Use Attendance backend:

```text
Check-in/out, attendance history, leave, leave approvals, reports
http://localhost:9000
```
