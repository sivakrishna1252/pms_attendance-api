from django.core.management.base import BaseCommand

from apps.attendance.services import process_open_attendance_records


class Command(BaseCommand):
    help = "Auto check-out employees who stayed checked in for 9+ hours without manual check-out."

    def handle(self, *args, **options):
        processed = process_open_attendance_records(notify=True)
        self.stdout.write(self.style.SUCCESS(f"Auto check-out completed for {processed} record(s)."))
