from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancelog",
            name="auto_checked_out",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="attendancelog",
            name="capped_at_standard_hours",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="attendancelog",
            name="forgot_checkout_email_sent",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="attendancelog",
            name="last_activity_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
