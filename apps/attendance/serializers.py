from rest_framework import serializers

from .models import AttendanceLog


class AttendanceLogSerializer(serializers.ModelSerializer):
    total_work_hours = serializers.DurationField(read_only=True)

    class Meta:
        model = AttendanceLog
        fields = [
            "id",
            "employee_id",
            "attendance_date",
            "check_in_time",
            "check_out_time",
            "status",
            "total_work_hours",
            "auto_checked_out",
            "capped_at_standard_hours",
            "last_activity_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
