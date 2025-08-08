from django.apps import AppConfig


class TimesheetConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "timesheet"

    def ready(self):
        import timesheet.signals  # importing signals for auto leave entitlement creation for newly added users
