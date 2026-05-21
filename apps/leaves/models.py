from django.db import models


class LeaveRequest(models.Model):
    class LeaveType(models.TextChoices):
        ANNUAL = "ANNUAL", "Annual"
        SICK = "SICK", "Sick"
        CASUAL = "CASUAL", "Casual"
        COMPENSATORY = "COMPENSATORY", "Compensatory"
        WFH = "WFH", "Work From Home"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    employee_id = models.IntegerField(db_index=True)
    leave_type = models.CharField(max_length=50, choices=LeaveType.choices)
    from_date = models.DateField()
    to_date = models.DateField()
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    approved_by = models.IntegerField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.employee_id} - {self.leave_type} - {self.status}"


class Holiday(models.Model):
    name = models.CharField(max_length=120)
    holiday_date = models.DateField(unique=True, db_index=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["holiday_date"]

    def __str__(self):
        return f"{self.name} ({self.holiday_date})"


class LeaveBalance(models.Model):
    employee_id = models.IntegerField(unique=True)
    annual_leave = models.PositiveIntegerField(default=0)
    sick_leave = models.PositiveIntegerField(default=0)
    casual_leave = models.PositiveIntegerField(default=0)
    compensatory_leave = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Leave balance for {self.employee_id}"
