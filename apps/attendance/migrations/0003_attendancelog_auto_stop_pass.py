from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0002_attendance_enhancements"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancelog",
            name="auto_stop_pass",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
