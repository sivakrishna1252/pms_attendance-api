from datetime import timedelta

STANDARD_WORK_HOURS = timedelta(hours=9)
SATURDAY_WORK_HOURS = timedelta(hours=7)
OVERTIME_AFTER = timedelta(hours=9)
SHIFT_START_HOUR = 9
SHIFT_START_MINUTE = 0
STANDARD_LOGOUT_HOUR = 18
SATURDAY_LOGOUT_HOUR = 16
LATE_CHECK_IN_HOUR = 11
LATE_CHECK_IN_MINUTE = 0

# Auto Stop schedule (local time)
AUTO_STOP_FIRST_HOUR = 20
AUTO_STOP_FIRST_MINUTE = 0
AUTO_STOP_FINAL_HOUR = 21
AUTO_STOP_FINAL_MINUTE = 0
AUTO_STOP_FIRST_INACTIVITY = timedelta(hours=1)
AUTO_STOP_FINAL_INACTIVITY = timedelta(minutes=30)
AUTO_STOP_HARD_CLOSE_AFTER_FINAL = timedelta(hours=1)

# Legacy alias
AUTO_CHECKOUT_HOUR = AUTO_STOP_FIRST_HOUR
AUTO_CHECKOUT_MINUTE = AUTO_STOP_FIRST_MINUTE

# Work-day display thresholds (hours)
ABSENT_MAX_HOURS = 5  # 0–5 h → Absent
PRESENT_MIN_HOURS = 8  # 8+ h (manual checkout) → Present
PRESENT_MAX_HOURS = 9  # >9 h (manual checkout) → Overtime
