# Generated for attendance_service initial schema.
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="LeaveBalance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("employee_id", models.IntegerField(unique=True)),
                ("annual_leave", models.PositiveIntegerField(default=0)),
                ("sick_leave", models.PositiveIntegerField(default=0)),
                ("casual_leave", models.PositiveIntegerField(default=0)),
                ("compensatory_leave", models.PositiveIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="LeaveRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("employee_id", models.IntegerField(db_index=True)),
                (
                    "leave_type",
                    models.CharField(
                        choices=[
                            ("ANNUAL", "Annual"),
                            ("SICK", "Sick"),
                            ("CASUAL", "Casual"),
                            ("COMPENSATORY", "Compensatory"),
                        ],
                        max_length=50,
                    ),
                ),
                ("from_date", models.DateField()),
                ("to_date", models.DateField()),
                ("reason", models.TextField()),
                (
                    "status",
                    models.CharField(
                        choices=[("PENDING", "Pending"), ("APPROVED", "Approved"), ("REJECTED", "Rejected")],
                        default="PENDING",
                        max_length=20,
                    ),
                ),
                ("approved_by", models.IntegerField(blank=True, null=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("rejection_reason", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
