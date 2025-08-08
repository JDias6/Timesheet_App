from datetime import date

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import LeaveEntitlement, LeaveType

User = get_user_model()


@receiver(post_save, sender=User)
def create_user_leave_entitlements(sender, instance, created, **kwargs):
    """Automatically create leave entitlements for new users"""
    if created:  # Only for new users
        current_year = date.today().year

        # Standard allocation
        STANDARD_ENTITLEMENTS = {
            "AL": 25,  # Annual Leave
            "SL": 10,  # Sick Leave
            "PL": 5,  # Personal Leave
        }

        for leave_code, allocated_days in STANDARD_ENTITLEMENTS.items():
            try:
                leave_type = LeaveType.objects.get(code=leave_code, active=True)
                LeaveEntitlement.objects.get_or_create(
                    user=instance,
                    leave_type=leave_type,
                    year=current_year,
                    defaults={"allocated_days": allocated_days},
                )
            except LeaveType.DoesNotExist:
                # Log error or handle missing leave types
                pass
