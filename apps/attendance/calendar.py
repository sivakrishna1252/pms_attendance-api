"""Office working calendar: recurring holidays and Saturday half-day rules."""

from datetime import date, timedelta

from apps.leaves.models import Holiday

SATURDAY_ORDINAL_HOLIDAYS = {1, 3}


def saturday_ordinal(value: date) -> int | None:
    if value.weekday() != 5:
        return None
    return (value.day - 1) // 7 + 1


def recurring_holiday_label(value: date) -> str | None:
    if value.weekday() == 6:
        return "Sunday"
    if value.weekday() == 5:
        ordinal = saturday_ordinal(value)
        if ordinal in SATURDAY_ORDINAL_HOLIDAYS:
            suffix = {1: "st", 2: "nd", 3: "rd", 4: "th", 5: "th"}.get(ordinal, "th")
            return f"{ordinal}{suffix} Saturday"
    return None


def admin_holiday_for_date(value: date):
    return Holiday.objects.filter(is_active=True, holiday_date=value).first()


def holiday_info_for_date(value: date) -> tuple[bool, str]:
    recurring = recurring_holiday_label(value)
    if recurring:
        return True, recurring

    admin_holiday = admin_holiday_for_date(value)
    if admin_holiday:
        return True, admin_holiday.name

    return False, ""


def is_company_holiday(value: date) -> bool:
    return holiday_info_for_date(value)[0]


def is_working_saturday(value: date) -> bool:
    return value.weekday() == 5 and not is_company_holiday(value)


def is_working_day(value: date) -> bool:
    return not is_company_holiday(value)


def expected_work_hours(value: date):
    from .constants import SATURDAY_WORK_HOURS, STANDARD_WORK_HOURS

    if is_working_saturday(value):
        return SATURDAY_WORK_HOURS
    return STANDARD_WORK_HOURS


def expected_logout_hour(value: date) -> int:
    from .constants import SATURDAY_LOGOUT_HOUR, STANDARD_LOGOUT_HOUR

    if is_working_saturday(value):
        return SATURDAY_LOGOUT_HOUR
    return STANDARD_LOGOUT_HOUR


def shift_label_for_date(value: date) -> str:
    if is_company_holiday(value):
        is_holiday, name = holiday_info_for_date(value)
        return f"Holiday: {name}" if is_holiday else "Holiday"
    if is_working_saturday(value):
        return "Working Saturday: 9:00 AM - 4:00 PM (7 hours)"
    return "Standard Shift: 9:00 AM - 6:00 PM (9 hours)"


def iter_dates(from_date: date, to_date: date):
    current = from_date
    while current <= to_date:
        yield current
        current += timedelta(days=1)


def holiday_dates_between(from_date: date, to_date: date) -> set[date]:
    dates = set()
    for day in iter_dates(from_date, to_date):
        if is_company_holiday(day):
            dates.add(day)
    return dates


def months_ago(reference: date, months: int) -> date:
    month = reference.month - months
    year = reference.year
    while month <= 0:
        month += 12
        year -= 1

    import calendar

    day = min(reference.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


REPORT_RETENTION_MONTHS = 6


def resolve_report_date_range(start_date, end_date, *, today=None):
    from django.utils import timezone

    today = today or timezone.localdate()
    default_start = months_ago(today, REPORT_RETENTION_MONTHS)
    warnings = []

    if not start_date and not end_date:
        return default_start, today, warnings

    resolved_end = end_date or today
    resolved_start = start_date or default_start

    earliest_allowed = months_ago(today, REPORT_RETENTION_MONTHS)
    if resolved_start < earliest_allowed:
        warnings.append(
            f"Reports are limited to the last {REPORT_RETENTION_MONTHS} months. "
            f"Start date adjusted to {earliest_allowed.isoformat()}."
        )
        resolved_start = earliest_allowed

    if resolved_end > today:
        resolved_end = today

    if resolved_start > resolved_end:
        resolved_start = default_start
        resolved_end = today

    return resolved_start, resolved_end, warnings
