from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0003_attendancelog_auto_stop_pass"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancelog",
            name="admin_display_status",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="attendancelog",
            name="admin_overridden_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="attendancelog",
            name="admin_overridden_by",
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
