from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("leaves", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Holiday",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("holiday_date", models.DateField(db_index=True, unique=True)),
                ("description", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["holiday_date"],
            },
        ),
    ]
