from django.contrib import admin

from .models import AttendanceLog


@admin.register(AttendanceLog)
class AttendanceLogAdmin(admin.ModelAdmin):
    list_display = ("employee_id", "attendance_date", "check_in_time", "check_out_time", "status")
    list_filter = ("status", "attendance_date")
    search_fields = ("employee_id",)
