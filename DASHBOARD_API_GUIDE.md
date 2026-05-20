# AttendSync Dashboard API Guide

Base URL:

```text
http://127.0.0.1:8000
```

All employee and admin APIs require the PMS access token:

```http
Authorization: Bearer <PMS_ACCESS_TOKEN>
Content-Type: application/json
```

## Employee Dashboard APIs

### 1. Check In

Used by the employee Check-In/Out screen.

```http
POST /api/attendance/check-in/
```

Body:

```json
{
  "location": "Apparatus solutions pune"
}
```

Response:

```json
{
  "success": true,
  "message": "Checked in successfully.",
  "data": {
    "date": "2026-05-13",
    "office": {
      "name": "Apparatus solutions pune",
      "shift": "Standard Shift: 9:00 AM - 6:00 PM",
      "status": "In Office",
      "is_inside_office": true
    },
    "state": {
      "checked_in": true,
      "checked_out": false,
      "next_action": "check_out"
    },
    "today_log": {
      "check_in": "09:02 AM",
      "check_out": "-",
      "duration": "-",
      "timeline": [
        {
          "label": "Checked In",
          "time": "09:02 AM",
          "location": "Apparatus solutions pune"
        },
        {
          "label": "Awaiting Check Out",
          "time": null,
          "location": null
        }
      ]
    },
    "attendance": {
      "id": 1,
      "employee_id": 5,
      "attendance_date": "2026-05-13",
      "check_in_time": "2026-05-13T09:02:00Z",
      "check_out_time": null,
      "status": "PRESENT",
      "total_work_hours": null
    }
  }
}
```

### 2. Check Out

```http
POST /api/attendance/check-out/
```

Body:

```json
{
  "location": "Apparatus solutions pune"
}
```

Response:

```json
{
  "success": true,
  "message": "Checked out successfully.",
  "data": {
    "state": {
      "checked_in": true,
      "checked_out": true,
      "next_action": "done"
    },
    "today_log": {
      "check_in": "09:02 AM",
      "check_out": "06:05 PM",
      "duration": "9h 03m"
    }
  }
}
```

### 3. Today's Check-In State

Use this when the page loads to decide whether to show Check In, Check Out, or Done.

```http
GET /api/attendance/today/
```

Response:

```json
{
  "success": true,
  "message": "Today's attendance fetched successfully.",
  "data": {
    "state": {
      "checked_in": true,
      "checked_out": false,
      "next_action": "check_out"
    },
    "today_log": {}
  }
}
```

### 4. Employee Attendance History

Only returns the logged-in employee's own attendance records.

```http
GET /api/attendance/history/?start_date=2026-05-01&end_date=2026-05-31
```

Response:

```json
{
  "success": true,
  "message": "Attendance history fetched successfully.",
  "summary": {
    "present": 22,
    "absent": 0,
    "leave": 0,
    "holiday": 0,
    "wfh": 0,
    "checked_out": 20,
    "total_records": 22
  },
  "records": [
    {
      "id": 1,
      "employee_id": 5,
      "date": "2026-05-15",
      "day": "Friday",
      "status": "CHECKED_OUT",
      "status_label": "Checked Out",
      "check_in": "09:02 AM",
      "check_out": "06:05 PM",
      "duration": "9h 03m",
      "location": "HQ - Bangalore, Karnatak"
    }
  ]
}
```

### 5. Leave Balance

Used for the leave balance cards.

```http
GET /api/leaves/balance/
```

Response:

```json
{
  "success": true,
  "message": "Leave balances fetched successfully.",
  "balances": [
    {
      "key": "annual_leave",
      "label": "Annual Leave",
      "remaining": 18,
      "total": 18
    },
    {
      "key": "sick_leave",
      "label": "Sick Leave",
      "remaining": 12,
      "total": 12
    }
  ]
}
```

### 6. Apply Leave

```http
POST /api/leaves/apply/
```

Body:

```json
{
  "leave_type": "ANNUAL",
  "from_date": "2026-05-20",
  "to_date": "2026-05-24",
  "duration_type": "FULL_DAY",
  "reason": "Family vacation"
}
```

Response:

```json
{
  "success": true,
  "message": "Leave application submitted successfully.",
  "data": {
    "leave_request": {
      "id": 12,
      "employee_id": 5,
      "leave_type": "ANNUAL",
      "leave_type_label": "Annual",
      "date_range": "May 20 - May 24, 2026",
      "from_date": "2026-05-20",
      "to_date": "2026-05-24",
      "days": 5,
      "reason": "Family vacation",
      "status": "PENDING",
      "applied_on": "2026-05-13"
    },
    "balances": []
  }
}
```

### 7. Employee Leave History

```http
GET /api/leaves/history/
```

Response:

```json
{
  "success": true,
  "message": "Leave history fetched successfully.",
  "balances": [],
  "requests": [
    {
      "id": 12,
      "date_range": "May 20 - May 24, 2026",
      "leave_type_label": "Annual",
      "days": 5,
      "status": "PENDING",
      "applied_on": "2026-05-13"
    }
  ]
}
```

## Admin Dashboard APIs

### 1. Admin Attendance History

Admin can see everyone. Optional filters are supported.

```http
GET /api/attendance/admin/history/?start_date=2026-05-01&end_date=2026-05-31&employee_id=5
```

Response:

```json
{
  "success": true,
  "message": "Admin attendance history fetched successfully.",
  "filters": {
    "employee_id": "5",
    "start_date": "2026-05-01",
    "end_date": "2026-05-31"
  },
  "summary": {
    "present": 22,
    "absent": 0,
    "leave": 0,
    "holiday": 0,
    "wfh": 0,
    "checked_out": 20,
    "total_records": 22
  },
  "records": []
}
```

### 2. Pending Leave Approvals

Used by the Leave Approvals page.

```http
GET /api/admin/leaves/pending/
```

Response:

```json
{
  "success": true,
  "message": "Pending leave requests fetched successfully.",
  "pending_count": 4,
  "requests": [
    {
      "id": 12,
      "employee": {
        "id": 5,
        "name": "Employee 5",
        "department": "Not synced",
        "initials": "E5"
      },
      "leave_type_label": "Annual",
      "date_range": "May 20 - May 24, 2026",
      "days": 5,
      "reason": "Family vacation",
      "status": "PENDING",
      "applied_on": "2026-05-13"
    }
  ]
}
```

### 3. Approve Leave

```http
POST /api/admin/leaves/{id}/approve/
```

Body:

```json
{
  "decision_note": "Approved"
}
```

Response:

```json
{
  "success": true,
  "message": "Leave request approved successfully.",
  "data": {
    "id": 12,
    "status": "APPROVED",
    "approved_by": 1,
    "approved_at": "2026-05-13T10:30:00Z"
  }
}
```

### 4. Reject Leave

```http
POST /api/admin/leaves/{id}/reject/
```

Body:

```json
{
  "rejection_reason": "Project deadline conflict"
}
```

Response:

```json
{
  "success": true,
  "message": "Leave request rejected successfully.",
  "data": {
    "id": 12,
    "status": "REJECTED",
    "rejection_reason": "Project deadline conflict"
  }
}
```

### 5. Reports & Analytics

Used by the Reports page. This endpoint uses query params, not a POST body.

```http
GET /api/admin/reports/?report_type=attendance_summary&department=all&start_date=2026-05-01&end_date=2026-05-31
```

Supported report types:

```text
attendance_summary
leave_summary
combined_summary
```

Response:

```json
{
  "filters": {
    "start_date": "2026-05-01",
    "end_date": "2026-05-31",
    "report_type": "attendance_summary",
    "department": "all"
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
      "csv"
    ]
  },
  "summary": {
    "total_attendance_records": 10,
    "total_work_hours_display": "90h 30m",
    "attendance_by_status": {}
  }
}
```

Export examples:

```http
GET /api/admin/reports/?report_type=attendance_summary&start_date=2026-05-01&end_date=2026-05-31&export=pdf
GET /api/admin/reports/?report_type=attendance_summary&start_date=2026-05-01&end_date=2026-05-31&export=excel
GET /api/admin/reports/?report_type=attendance_summary&start_date=2026-05-01&end_date=2026-05-31&export=csv
```

## Notes

- Employee history APIs always use the employee id from the PMS JWT token.
- Admin APIs require `role=ADMIN`, `is_staff=true`, or the id listed in `ADMIN_EMPLOYEE_IDS`.
- Department filtering in reports currently supports `all`. To filter by department later, employee department data must be synced into this microservice.
- The current backend stores attendance records only when employees check in. Automatic absent/holiday generation is not implemented yet.
