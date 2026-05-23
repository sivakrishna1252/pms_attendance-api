from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.attendance.services import run_scheduled_auto_stop_pass


class Command(BaseCommand):
    help = (
        "Smart Auto Stop for open attendance sessions.\n"
        "  --pass first  : 8 PM job (inactive > 1 hour)\n"
        "  --pass final  : 9 PM job (inactive > 30 min, or force all)\n"
        "  --pass auto   : detect pass from current time (default)\n"
        "Schedule: run at 20:00 with --pass first, and at 21:00 with --pass final."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--pass",
            choices=["auto", "first", "final"],
            default="auto",
            help="Which Auto Stop pass to run (default: auto-detect from clock).",
        )

    def handle(self, *args, **options):
        now_local = timezone.localtime(timezone.now())
        processed, phase = run_scheduled_auto_stop_pass(
            pass_name=options["pass"],
            notify=True,
        )
        if phase is None:
            self.stdout.write(
                self.style.WARNING(
                    f"[{now_local:%Y-%m-%d %H:%M}] Before 8 PM — no Auto Stop pass ran."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"[{now_local:%Y-%m-%d %H:%M}] Pass={phase or options['pass']} — "
                f"Auto Stop applied to {processed} record(s)."
            )
        )
