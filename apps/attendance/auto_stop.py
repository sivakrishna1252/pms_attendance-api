"""Smart Auto Stop: 8 PM (forgotten) and 9 PM (final) using last_activity_at."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from django.utils import timezone

from .constants import (
    AUTO_STOP_FINAL_HOUR,
    AUTO_STOP_FINAL_INACTIVITY,
    AUTO_STOP_FINAL_MINUTE,
    AUTO_STOP_FIRST_HOUR,
    AUTO_STOP_FIRST_INACTIVITY,
    AUTO_STOP_FIRST_MINUTE,
    AUTO_STOP_HARD_CLOSE_AFTER_FINAL,
)
from .models import AttendanceLog

PASS_8PM = "8PM"
PASS_9PM = "9PM"
PASS_9PM_FORCED = "9PM_FORCED"


def stop_cutoff_on(attendance_date, hour, minute=0):
    tz = timezone.get_current_timezone()
    return timezone.make_aware(
        datetime.combine(attendance_date, time(hour, minute)),
        tz,
    )


def resolve_auto_stop_phase(now_local=None, *, force_final=False):
    """
    None = before 8 PM.
    'first' = 8 PM–9 PM window (first pass rules).
    'final' = 9 PM+ (final pass rules).
    """
    if force_final:
        return "final"

    now_local = now_local or timezone.localtime(timezone.now())
    today = now_local.date()
    first_cutoff = stop_cutoff_on(today, AUTO_STOP_FIRST_HOUR, AUTO_STOP_FIRST_MINUTE)
    final_cutoff = stop_cutoff_on(today, AUTO_STOP_FINAL_HOUR, AUTO_STOP_FINAL_MINUTE)

    if now_local < first_cutoff:
        return None
    if now_local < final_cutoff:
        return "first"
    return "final"


def last_activity_at(attendance: AttendanceLog):
    return attendance.last_activity_at or attendance.check_in_time


def inactivity_duration(attendance: AttendanceLog, now=None):
    now = now or timezone.now()
    activity = last_activity_at(attendance)
    if not activity:
        return timedelta(days=1)
    return now - activity


def should_auto_stop_first_pass(attendance: AttendanceLog, now=None) -> bool:
    """8 PM: Auto Stop when inactive for 1 hour or more (e.g. last activity 6:50 or 7:00 PM)."""
    now = now or timezone.now()
    day_cutoff = stop_cutoff_on(
        attendance.attendance_date,
        AUTO_STOP_FIRST_HOUR,
        AUTO_STOP_FIRST_MINUTE,
    )
    if now < day_cutoff:
        return False
    return inactivity_duration(attendance, now) >= AUTO_STOP_FIRST_INACTIVITY


def should_auto_stop_final_pass(attendance: AttendanceLog, now=None) -> bool:
    """
    9 PM: stop if inactive > 30 minutes.
    From 10 PM onward: stop everyone still open (checkout recorded at 9 PM).
    """
    now = now or timezone.now()
    final_cutoff = stop_cutoff_on(
        attendance.attendance_date,
        AUTO_STOP_FINAL_HOUR,
        AUTO_STOP_FINAL_MINUTE,
    )
    hard_close = final_cutoff + AUTO_STOP_HARD_CLOSE_AFTER_FINAL

    if now < final_cutoff:
        return False
    if now >= hard_close:
        return True
    return inactivity_duration(attendance, now) > AUTO_STOP_FINAL_INACTIVITY


def system_stop_cutoff(attendance: AttendanceLog, pass_label: str):
    """When the Auto Stop job ran (8 PM or 9 PM) — for audit/email only."""
    if pass_label == PASS_8PM:
        hour, minute = AUTO_STOP_FIRST_HOUR, AUTO_STOP_FIRST_MINUTE
    else:
        hour, minute = AUTO_STOP_FINAL_HOUR, AUTO_STOP_FINAL_MINUTE
    cutoff = stop_cutoff_on(attendance.attendance_date, hour, minute)
    if attendance.check_in_time and attendance.check_in_time > cutoff:
        return timezone.now()
    return cutoff


def effective_work_end(attendance: AttendanceLog, pass_label: str, *, now=None):
    """
    When work actually ended for hour calculation.

    Forgot check-out (inactive): use last_activity_at (e.g. left at 6 PM).
    Still working until 9 PM final: use 9 PM cutoff.
    """
    now = now or timezone.now()
    activity = last_activity_at(attendance)
    final_cutoff = stop_cutoff_on(
        attendance.attendance_date,
        AUTO_STOP_FINAL_HOUR,
        AUTO_STOP_FINAL_MINUTE,
    )

    if pass_label == PASS_8PM:
        return activity

    if pass_label == PASS_9PM:
        return activity

    if pass_label == PASS_9PM_FORCED:
        if inactivity_duration(attendance, now) <= AUTO_STOP_FINAL_INACTIVITY:
            return final_cutoff
        return activity

    return activity


def decide_auto_stop(attendance: AttendanceLog, *, phase=None, now=None, force_final=False):
    """
    Returns (should_stop, pass_label, work_end_at, system_stop_at) or (False, None, None, None).
    """
    now = now or timezone.now()
    if attendance.check_out_time is not None or not attendance.check_in_time:
        return False, None, None, None

    phase = phase or resolve_auto_stop_phase(timezone.localtime(now), force_final=force_final)
    if phase is None:
        return False, None, None, None

    if phase == "first":
        if not should_auto_stop_first_pass(attendance, now):
            return False, None, None, None
        pass_label = PASS_8PM
    else:
        if not should_auto_stop_final_pass(attendance, now):
            return False, None, None, None
        final_cutoff = stop_cutoff_on(
            attendance.attendance_date,
            AUTO_STOP_FINAL_HOUR,
            AUTO_STOP_FINAL_MINUTE,
        )
        hard_close = final_cutoff + AUTO_STOP_HARD_CLOSE_AFTER_FINAL
        pass_label = PASS_9PM_FORCED if now >= hard_close else PASS_9PM

    system_stop_at = system_stop_cutoff(
        attendance,
        PASS_8PM if pass_label == PASS_8PM else PASS_9PM,
    )
    work_end_at = effective_work_end(attendance, pass_label, now=now)
    if attendance.check_in_time and work_end_at < attendance.check_in_time:
        work_end_at = attendance.check_in_time
    return True, pass_label, work_end_at, system_stop_at
