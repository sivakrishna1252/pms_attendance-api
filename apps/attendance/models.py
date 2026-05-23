from django.db import models


class AttendanceLog(models.Model):
    class Status(models.TextChoices):
        PRESENT = "PRESENT", "Present"
        CHECKED_OUT = "CHECKED_OUT", "Checked Out"

    employee_id = models.IntegerField(db_index=True)
    attendance_date = models.DateField(db_index=True)
    check_in_time = models.DateTimeField(null=True, blank=True)
    check_out_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PRESENT)
    total_work_hours = models.DurationField(null=True, blank=True)
    auto_checked_out = models.BooleanField(default=False)
    auto_stop_pass = models.CharField(max_length=20, blank=True, default="")
    capped_at_standard_hours = models.BooleanField(default=False)
    forgot_checkout_email_sent = models.BooleanField(default=False)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["employee_id", "attendance_date"],
                name="unique_employee_attendance_date",
            )
        ]
        ordering = ["-attendance_date", "-check_in_time"]

    def __str__(self):
        return f"{self.employee_id} - {self.attendance_date}"
