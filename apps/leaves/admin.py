from django.contrib import admin

from .models import Holiday, LeaveBalance, LeaveRequest


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ("employee_id", "leave_type", "from_date", "to_date", "status", "approved_by")
    list_filter = ("status", "leave_type", "from_date")
    search_fields = ("employee_id",)


@admin.register(LeaveBalance)
class LeaveBalanceAdmin(admin.ModelAdmin):
    list_display = ("employee_id", "annual_leave", "sick_leave", "casual_leave", "compensatory_leave")
    search_fields = ("employee_id",)


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ("name", "holiday_date", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)
