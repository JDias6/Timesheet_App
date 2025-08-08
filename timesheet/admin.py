from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from timesheet.models import Project, TimeEntry, User

from .forms import CustomUserChangeForm, CustomUserCreationForm
from .models import LeaveEntitlement, LeaveRequest, LeaveType, User

# Register your models here.


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = User

    # These two tuples tell the admin which fields to show when adding vs editing
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "employee_id",
                    "email",
                    "password1",
                    "password2",
                    "manager",
                ),
            },
        ),
    )

    fieldsets = BaseUserAdmin.fieldsets + (
        ("Employee Data", {"fields": ("employee_id", "manager")}),
    )

    list_display = (
        "username",
        "employee_id",
        "email",
        "manager",
        "is_staff",
    )  # which columns to show on the change list page
    search_fields = ("username", "employee_id", "email")


@admin.register(TimeEntry)
class TimeEntryAdmin(admin.ModelAdmin):
    list_display = ("user", "date", "hours")
    list_filter = ("user", "date")
    search_fields = (
        "user__username",
    )  # search by username of the user who made the entry


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "active")
    list_filter = ("active",)
    search_fields = ("code", "name")
    filter_horizontal = ("members",)


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "requires_approval", "active"]
    list_filter = ["requires_approval", "active"]
    search_fields = ["name", "code"]


@admin.register(LeaveEntitlement)
class LeaveEntitlementAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "leave_type",
        "year",
        "allocated_days",
        "get_used_days",
        "get_remaining_days",
    ]
    list_filter = ["year", "leave_type"]
    search_fields = ["user__username", "user__first_name", "user__last_name"]
    readonly_fields = ["get_used_days", "get_remaining_days"]

    def get_used_days(self, obj):
        return obj.used_days

    get_used_days.short_description = "Used Days"

    def get_remaining_days(self, obj):
        return obj.remaining_days

    get_remaining_days.short_description = "Remaining Days"


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "leave_type",
        "start_date",
        "end_date",
        "total_days",
        "status",
        "created",
    ]
    list_filter = ["status", "leave_type", "created"]
    search_fields = ["user__username", "user__first_name", "user__last_name"]
    readonly_fields = ["total_days", "created", "updated"]
    date_hierarchy = "start_date"

    fieldsets = (
        (
            "Request Details",
            {
                "fields": (
                    "user",
                    "leave_type",
                    "start_date",
                    "end_date",
                    "total_days",
                    "comments",
                )
            },
        ),
        (
            "Status & Approval",
            {"fields": ("status", "approved_by", "approved_at", "rejection_reason")},
        ),
        (
            "Timestamps",
            {"fields": ("created", "updated"), "classes": ("collapse",)},
        ),
    )

    def save_model(self, request, obj, form, change):
        if not change:  # New object
            obj.user = request.user
        super().save_model(request, obj, form, change)
