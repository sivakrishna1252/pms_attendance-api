from django.core.management.base import BaseCommand

from apps.leaves.services import LEAVE_HISTORY_RETENTION_MONTHS, purge_expired_leave_history


class Command(BaseCommand):
    help = (
        "Delete approved/rejected leave requests older than "
        f"{LEAVE_HISTORY_RETENTION_MONTHS} months."
    )

    def handle(self, *args, **options):
        deleted = purge_expired_leave_history()
        self.stdout.write(
            self.style.SUCCESS(
                f"Purged {deleted} leave request(s) older than "
                f"{LEAVE_HISTORY_RETENTION_MONTHS} months."
            )
        )
