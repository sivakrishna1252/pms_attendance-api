# Attendance Service — Implementation Updates

This document summarizes the backend changes for employee attendance, leaves, and admin dashboard APIs.

## Business rules implemented

### 1. One check-in / check-out per day
- Each employee can have **only one** attendance record per calendar day (DB unique constraint).
- After **check-out**, check-in is blocked until **next working day 9:00 AM** (local timezone, default `Asia/Kolkata`).
- API responses include `state.next_check_in_at` and clear error messages.

### 2. Forgot check-out (9-hour auto check-out)
- If an employee stays checked in for **9+ hours** without manual check-out:
  - System sets check-out time to `check_in + 9 hours`
  - Records exactly **9 hours** work (`capped_at_standard_hours=true`, `auto_checked_out=true`)
  - Sends email: *"You forgot to check out..."*
- Runs automatically when calling: `today`, `history`, `activity`, admin APIs, and via cron:
  ```bash
  python manage.py auto_checkout_forgotten
  ```
- **Manual** check-out keeps actual hours (can show extra/less work on admin dashboard).

### 3. Activity heartbeat
- `POST /api/attendance/activity/` — frontend should call periodically while the app is active.
- Updates `last_activity_at` and triggers auto check-out rules if 9 hours elapsed.

### 4. Leaves & holidays
- `GET /api/leaves/holidays/` — list company holidays (add holidays in Django admin).
- Apply leave rejects dates that overlap **holidays** or **pending/approved** leave.
- **Approve** deducts leave balance and sends approval email.
- **Reject** sends rejection email with reason.
- `GET /api/leaves/history/?status=PENDING|APPROVED|REJECTED` — filter employee history.

### 5. Admin dashboard
- `GET /api/attendance/admin/dashboard/?date=YYYY-MM-DD` — today's check-ins/outs, extra/less work counts, leave pending/approved/rejected lists.
- `GET /api/attendance/admin/history/` — includes `work_analysis` per record (`extra_work`, `less_work`, `on_time`).
- `GET /api/admin/leaves/?status=PENDING` — all leave requests with counts.

## New / updated API endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/attendance/activity/` | Activity heartbeat |
| GET | `/api/attendance/admin/dashboard/` | Admin dashboard (attendance + leaves) |
| GET | `/api/leaves/holidays/` | Company holidays |
| GET | `/api/admin/leaves/` | Admin leave list with status filter |

## Response fields (work analysis)

Every attendance record in history/dashboard includes:

```json
"work_analysis": {
  "work_hours": 9.5,
  "work_hours_display": "9h 30m",
  "expected_hours": 9,
  "expected_hours_display": "9h 00m",
  "variance": "extra_work",
  "variance_hours": 0.5,
  "variance_display": "+0.50h extra work",
  "is_capped": false,
  "auto_checked_out": false
}
```

## Setup

1. Run migrations:
   ```bash
   cd attendance_service
   python manage.py migrate
   ```
2. Copy `.env.example` → `.env` and set `EMAIL_*`, `PMS_SERVICE_TOKEN` (admin JWT for user email lookup).
3. Add holidays in Django admin: `/admin/` → Holidays.
4. Schedule auto check-out (optional cron every 15–30 min):
   ```bash
   python manage.py auto_checkout_forgotten
   ```

## Suggested frontend integration

- Poll `POST /api/attendance/activity/` every 5–10 minutes while the employee portal is open.
- After check-out, disable check-in until `state.next_check_in_at`.
- Admin UI: use `/api/attendance/admin/dashboard/` for the main dashboard widget.
