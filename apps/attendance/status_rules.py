from datetime import timedelta

from .constants import ABSENT_MAX_HOURS, PRESENT_MAX_HOURS, PRESENT_MIN_HOURS


def worked_hours_value(total_work_hours) -> float:
    if not total_work_hours:
        return 0.0
    return round(total_work_hours.total_seconds() / 3600, 2)


def resolve_work_day_status(
    *,
    day,
    check_in_time,
    total_work_hours,
    is_late=False,
    auto_checked_out=False,
):
    """
    Display status for employees who checked in.

    Manual checkout:
    - 0–5 h → Absent
    - >5 h and <8 h → Half Day
    - 8–9 h → Present (Late if check-in after 11 AM)
    - >9 h → Overtime

    Auto Stop (forgot check-out):
    - ≤5 h → Absent
    - >5 h → Auto Stop Half Day (never Present/Overtime, even if 8+ h recorded)
    """
    del day

    if not check_in_time:
        return "Absent"

    hours = worked_hours_value(total_work_hours)

    if auto_checked_out:
        if hours <= ABSENT_MAX_HOURS:
            return "Absent"
        return "Auto Stop Half Day"

    if hours <= ABSENT_MAX_HOURS:
        return "Absent"
    if hours < PRESENT_MIN_HOURS:
        return "Half Day"
    if hours > PRESENT_MAX_HOURS:
        return "Overtime"
    if is_late:
        return "Late"
    return "Present"
