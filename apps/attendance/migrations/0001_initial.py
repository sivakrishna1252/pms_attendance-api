# Generated for attendance_service initial schema.
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="AttendanceLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("employee_id", models.IntegerField(db_index=True)),
                ("attendance_date", models.DateField(db_index=True)),
                ("check_in_time", models.DateTimeField(blank=True, null=True)),
                ("check_out_time", models.DateTimeField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[("PRESENT", "Present"), ("CHECKED_OUT", "Checked Out")],
                        default="PRESENT",
                        max_length=20,
                    ),
                ),
                ("total_work_hours", models.DurationField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-attendance_date", "-check_in_time"],
            },
        ),
        migrations.AddConstraint(
            model_name="attendancelog",
            constraint=models.UniqueConstraint(
                fields=("employee_id", "attendance_date"),
                name="unique_employee_attendance_date",
            ),
        ),
    ]
