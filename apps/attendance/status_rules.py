from datetime import timedelta

from .constants import (
    ABSENT_MAX_HOURS,
    HALF_DAY_MAX_HOURS,
    PRESENT_MAX_HOURS,
    PRESENT_MIN_HOURS,
    THIRD_DAY_MAX_HOURS,
)

THIRD_DAY_STATUS_LABEL = "1/3"


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

    - Check-in before/at 11 AM + short hours → Present (not Late)
    - Check-in after 11 AM + short hours → Late
    - Check-in after 11 AM + 7–9 h (e.g. 9 h) → Present
    - > 9 h → Overtime
    - 5–7 h → 1/3, 2–5 h → Half Day

    No check-in: caller uses Absent (approved leave) or — (dash).
    """
    del day, auto_checked_out

    if not check_in_time:
        return "Absent"

    hours = worked_hours_value(total_work_hours)

    if hours > PRESENT_MAX_HOURS:
        return "Overtime"
    if hours >= PRESENT_MIN_HOURS:
        return "Present"
    if HALF_DAY_MAX_HOURS < hours < THIRD_DAY_MAX_HOURS:
        return THIRD_DAY_STATUS_LABEL
    if ABSENT_MAX_HOURS < hours <= HALF_DAY_MAX_HOURS:
        return "Half Day"
    if is_late:
        return "Late"
    return "Present"
