from datetime import timedelta

from .constants import ABSENT_MAX_HOURS, PRESENT_MAX_HOURS, PRESENT_MIN_HOURS


def worked_hours_value(total_work_hours) -> float:
    """Match format_duration (whole hours + minutes) so 8h 00m → 8.0 for status."""
    if not total_work_hours:
        return 0.0
    total_seconds = int(total_work_hours.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return round(hours + minutes / 60, 2)


def resolve_work_day_status(
    *,
    day,
    check_in_time,
    total_work_hours,
    is_late=False,
    auto_checked_out=False,
    has_check_out=True,
):
    """
    Display status for employees who checked in.

    Before check-out (still working):
    - Check-in at or before 11:00 AM → Present
    - Check-in after 11:00 AM → Late

    Manual checkout:
    - 0–5 h → Absent
    - >5 h and below 8 h → Half Day
    - 8 h or more (up to 9 h) → Present (Late if check-in after 11 AM)
    - above 9 h → Overtime

    Auto Stop (forgot check-out):
    - ≤5 h → Absent
    - >5 h → Auto Stop Half Day (never Present/Overtime, even if 8+ h recorded)
    """
    del day

    if not check_in_time:
        return "Absent"

    if not has_check_out and not auto_checked_out:
        return "Late" if is_late else "Present"

    hours = worked_hours_value(total_work_hours)

    if auto_checked_out:
        if hours <= ABSENT_MAX_HOURS:
            return "Absent"
        return "Auto Stop Half Day"

    if hours <= ABSENT_MAX_HOURS:
        return "Absent"
    if hours > PRESENT_MAX_HOURS:
        return "Overtime"
    if hours >= PRESENT_MIN_HOURS:
        return "Late" if is_late else "Present"
    return "Half Day"
