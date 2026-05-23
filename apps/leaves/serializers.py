from rest_framework import serializers

from .models import Holiday, LeaveBalance, LeaveRequest
from .services import leave_days_between, validate_leave_application


class LeaveRequestSerializer(serializers.ModelSerializer):
    duration_type = serializers.ChoiceField(
        choices=["FULL_DAY", "HALF_DAY"],
        default="FULL_DAY",
        required=False,
        write_only=True,
    )
    duration_days = serializers.SerializerMethodField()

    class Meta:
        model = LeaveRequest
        fields = [
            "id",
            "employee_id",
            "leave_type",
            "from_date",
            "to_date",
            "duration_type",
            "duration_days",
            "reason",
            "status",
            "approved_by",
            "approved_at",
            "rejection_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "employee_id",
            "duration_days",
            "status",
            "approved_by",
            "approved_at",
            "rejection_reason",
            "created_at",
            "updated_at",
        ]

    def get_duration_days(self, obj) -> int:
        return leave_days_between(obj.from_date, obj.to_date)

    def validate(self, attrs):
        employee_id = self.context.get("employee_id")
        if employee_id:
            validate_leave_application(
                employee_id=employee_id,
                leave_type=attrs.get("leave_type") or getattr(self.instance, "leave_type", ""),
                from_date=attrs["from_date"],
                to_date=attrs["to_date"],
                exclude_request_id=self.instance.pk if self.instance else None,
            )
        elif attrs["to_date"] < attrs["from_date"]:
            raise serializers.ValidationError(
                {"to_date": ["To date cannot be before from date."]}
            )
        return attrs

    def create(self, validated_data):
        validated_data.pop("duration_type", None)
        return super().create(validated_data)


class HolidaySerializer(serializers.ModelSerializer):
    class Meta:
        model = Holiday
        fields = ["id", "name", "holiday_date", "description", "is_active", "created_at"]
        read_only_fields = ["id", "created_at"]


class LeaveApprovalSerializer(serializers.Serializer):
    rejection_reason = serializers.CharField(required=False, allow_blank=True)
    decision_note = serializers.CharField(required=False, allow_blank=True)


class LeaveBalanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveBalance
        fields = [
            "id",
            "employee_id",
            "annual_leave",
            "sick_leave",
            "casual_leave",
            "compensatory_leave",
            "updated_at",
        ]
        read_only_fields = fields
