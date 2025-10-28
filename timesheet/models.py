import logging
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import models
from django.db.models import Sum
from django.utils import timezone

# Create your models here.
logger = logging.getLogger(__name__)


class User(AbstractUser):
    employee_id = models.CharField(
        max_length=20, unique=True, help_text="Company-assigned employee id."
    )

    manager = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="direct_reports",
    )

    def get_leave_balance(self, leave_type, year=None):
        if year is None:
            year = date.today().year
        try:
            entitlement = self.leave_entitlements.get(leave_type=leave_type, year=year)
            return entitlement.remaining_days
        except LeaveEntitlement.DoesNotExist:
            return 0

    def is_manager_of(self, user):
        # A check to see if this user is a manager of another user
        return user.manager == self

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.employee_id})"


# A model to allow users to log daily hours
class TimeEntry(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="time_entries"
    )

    project = models.ForeignKey(
        "Project", on_delete=models.CASCADE, null=True, blank=True
    )  # make sure to lock down this field by making it non-nullable after assigning a TimeEntry to a real project

    date = models.DateField(help_text="The calendar date for this entry.")

    hours = models.DecimalField(max_digits=4, decimal_places=2)

    # Adding a submitted flag
    submitted = models.BooleanField(
        default=False, help_text="Whether this entry has been submitted"
    )

    class Meta:
        unique_together = ("user", "project", "date")
        ordering = ["-date"]
        verbose_name = "Time Entry"

    def clean(self):
        super().clean()  # Run the parent clean() first

        from .models import LeaveRequest

        is_leave_day = LeaveRequest.objects.filter(
            user=self.user,
            status="APPROVED",
            start_date__lte=self.date,
            end_date__gte=self.date,
        ).exists()

        # Skip validation for approved leave days
        if is_leave_day:
            return

        # Sum existing hours for this user + date

        qs = TimeEntry.objects.filter(user=self.user, date=self.date)
        if self.pk:
            qs = qs.exclude(pk=self.pk)

        agg = qs.aggregate(total_hours=Sum("hours"))
        total = agg["total_hours"] or Decimal("0")

        # check the new total
        if total + self.hours > Decimal("7.5"):
            raise ValidationError(
                f"Logging {self.hours}h would exceed the total 7.5h for {self.date}."
            )

    def save(self, *args, **kwargs):
        # Ensuring that clean is called on save()
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} - {self.project.code} on {self.date}: {self.hours}h"


class Project(models.Model):
    code = models.CharField(
        max_length=20,
        unique=True,
    )

    name = models.CharField(max_length=100, help_text="Name of the project.")

    description = models.TextField(blank=True, help_text="optional longer description")

    active = models.BooleanField(
        default=True,
        help_text="Uncheck to archive a project without having to delete it",
    )

    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="projects",
        blank=True,
        help_text="which employees can book time on this project.",
    )

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "project"

    def __str__(self):
        return f"{self.code} - {self.name}"


# A model to represent the different types of leave (Annual, Sick, Maternity, Personal etc.)
class LeaveType(models.Model):
    name = models.CharField(max_length=50, unique=True)
    code = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    requires_approval = models.BooleanField(default=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ["name"]


# A model that creates leave entitlement information against each type of leave and user
class LeaveEntitlement(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="leave_entitlements",
    )
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE)
    year = models.PositiveIntegerField()
    allocated_days = models.DecimalField(max_digits=5, decimal_places=1)

    class Meta:
        unique_together = ("user", "leave_type", "year")
        ordering = ["-year", "leave_type__name"]

    @property
    def used_days(self):
        # calculating used leave days for this entitlement
        return (
            LeaveRequest.objects.filter(
                user=self.user,
                leave_type=self.leave_type,
                start_date__year=self.year,
                status="APPROVED",
            ).aggregate(total=models.Sum("total_days"))["total"]
            or 0
        )

    @property
    def remaining_days(self):
        return self.allocated_days - self.used_days

    def __str__(self):
        return f"{self.user} - {self.leave_type} {self.year}: {self.remaining_days} days remaining"


# The main model that will drive the backend process for leave requests
class LeaveRequest(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "PENDING"),
        ("APPROVED", "APPROVED"),
        ("REJECTED", "REJECTED"),
        ("CANCELLED", "Cancelled"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="leave_requests",
    )
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE)
    start_date = models.DateField()
    end_date = models.DateField()
    total_days = models.DecimalField(max_digits=6, decimal_places=1)
    comments = models.TextField(
        blank=True, help_text="Optional comments to add when requesting leave"
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="PENDING")

    # Approval workflow for managers
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_leave_requests",
    )

    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    # Timestamps
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]
        verbose_name = "Leave Request"

    def clean(self):
        super().clean()

        # Validating the dates
        if self.start_date and self.end_date:
            if self.start_date > self.end_date:
                raise ValidationError("Start date cannot be after end date.")

            if self.start_date < date.today():
                raise ValidationError("Cannot request leave from a past date.")

    def save(self, *args, **kwargs):
        # calculating total days if not set
        if self.start_date and self.end_date and not self.total_days:
            self.total_days = self.calculate_business_days()

            # self.full_clean()

        super().save(*args, **kwargs)

    def calculate_business_days(self):
        # A function to calculate the total business days between the start and end date
        if not self.start_date or not self.end_date:
            return 0

        current_date = self.start_date
        business_days = 0

        while current_date <= self.end_date:
            if current_date.weekday() < 5:  # Monday is 0 and Sunday is 6
                business_days += 1
            current_date += timedelta(days=1)
        return business_days

    def approve(self, approved_by_user):
        # Approve leave request
        old_status = self.status
        self.status = "APPROVED"
        self.approved_by = approved_by_user
        self.approved_at = timezone.now()
        self.save()

        # Send notification after successful save
        if old_status != "APPROVED":
            self.send_status_notification()

    def reject(self, rejected_by_user, reason=""):
        # Reject leave request
        old_status = self.status
        self.status = "REJECTED"
        self.approved_by = rejected_by_user
        self.approved_at = timezone.now()
        self.rejection_reason = reason
        self.save()

        if old_status != "REJECTED":
            self.send_status_notification()

    def send_confirmation_email(self):
        # Send a confirmation email to the user when the leave request is submitted
        if not self.user.email:
            logger.warning(f"No email address found for user {self.user}")
            return False

        subject = f"Leave Request Submitted - {self.start_date} to {self.end_date}"
        message = f"""
            Dear {self.user.get_full_name()},
            Your leave request has been submitted and is pending approval

            Details:
            - Type: {self.leave_type.name}
            - Dates: {self.start_date} to {self.end_date}
            - Total Days: {self.total_days}
            - Status: {self.get_status_display()}

            You will receive another email once your request has been reviewed.

            Thank You!
            """
        logger.info(f"CONFIRMATION EMAIL (Demo Mode)")
        logger.info(f"To: {self.user.email}")
        logger.info(f"Subject: {subject}")
        return True

        # try:
        #    logger.info(f"Attempting to send confirmatio email to {self.user.email}")
        #    send_mail(
        #        subject=subject,
        #       message=message,
        #       from_email=getattr(settings, "EMAIL_HOST_USER", "noreply@company.com"),
        #       recipient_list=[self.user.email],
        #       fail_silently=False,
        #   )
        #   logger.info(f"Confirmation email sent successfully to {self.user.email}")
        #   return True
        # except Exception as e:
        #   logger.error(
        #        f"Failed to send confirmation email to {self.user.email}: {str(e)}"
        #    )
        #    return False

    def send_status_notification(self):
        # Send a notification when the status of the leave request changes
        if not self.user.email:
            logger.warning(f"No email address found for user {self.user}")
            return False

        if self.status == "APPROVED":
            subject = f"Leave Request Approved - {self.start_date} to {self.end_date}"
            message = f""" 

            Dear {self.user.get_full_name()},
            Your leave request has been approved!

            Details:
            - Type: {self.leave_type.name}
            - Dates: {self.start_date} to {self.end_date}
            - Total Days: {self.total_days}
            - Approved by: {self.approved_by.get_full_name() if self.approved_by else "System"}

            Enjoy your time off!
            """
        elif self.status == "REJECTED":
            subject = f"Leave Request Rejected - {self.start_date} to {self.end_date}"
            message = f""" 

            Dear {self.user.get_full_name()},
            Unfortunately your leave request has been Rejected!

            Details:
            - Type: {self.leave_type.name}
            - Dates: {self.start_date} to {self.end_date}
            - Total Days: {self.total_days}
            - Reason: {self.rejection_reason or "No reason provided"}
            - Reviewed by: {self.approved_by.get_full_name() if self.approved_by else "System"}

            Please refer back to your manager for more information.
            """
        else:
            logger.info(f"No notification needed for status: {self.status}")
            return True

        logger.info(f" STATUS NOTIFICATION EMAIL (Demo Mode)")
        logger.info(f"To: {self.user.email}")
        logger.info(f"Subject: {subject}")
        logger.info(f"Status: {self.status}")
        return True

        # try:
        #    logger.info(f"Attempting to send status notification to {self.user.email}")
        #    send_mail(
        #        subject=subject,
        #        message=message,
        #        from_email=settings.DEFAULT_FROM_EMAIL,
        #        recipient_list=[
        #            self.user.email
        #       ],  # make sure settings.py is configured to use email settings
        #       fail_silently=False,
        #    )
        #   logger.info(f"Notification sent successfully to {self.user.email}")
        #    return True
        # except Exception as e:
        #    logger.error(
        #       f"Error sending status notification to {self.user.email: {str(e)}}"
        #    )
        #    return False

    def send_manager_notification(self):
        # Send a notification to the manager when leave request is submitted
        if not self.user.manager:
            logger.warning(f"No manager found for user {self.user}")
            return False
        if not self.user.manager.email:
            logger.warning(f"No email found for manager {self.user.manager}")
            return False
        subject = f"New Leave Request - {self.user.get_full_name()}"
        message = f"""
            Dear {self.user.manager.get_full_name()},
            A new leave request requires your approval.
            Employee: {self.user.get_full_name()} ({self.user.employee_id})
            Leave Type: {self.leave_type.name}
            Dates: {self.start_date} to {self.end_date}
            Total Days: {self.total_days}
            Status: {self.get_status_display()}
            {f"Comments: {self.comments}" if self.comments else "No additional comments provided."}
            Please log into the system to review and manage this request.

            Thank You!
        """
        logger.info(f" MANAGER NOTIFICATION EMAIL (Demo Mode)")
        logger.info(f"To: {self.user.manager.email}")
        logger.info(f"Manager: {self.user.manager.get_full_name()}")
        logger.info(f"Subject: {subject}")
        return True

        # try:
        #    logger.info(
        #        f"Attempting to send manager notification to {self.user.manager.email}"
        #    )
        #    send_mail(
        #        subject=subject,
        #        message=message,
        #        from_email=settings.DEFAULT_FROM_EMAIL,
        #        recipient_list=[self.user.manager.email],
        #        fail_silently=False,
        #    )
        #    logger.info(
        #        f"Manager notification sent successfully to {self.user.manager.email}"
        #    )
        #    return True
        # except Exception as e:
        #    logger.error(
        #        f"Error sending manager notification to {self.user.manager.email}: {str(e)}"
        #    )
        #    return False

    def __str__(self):
        return f"{self.user} - {self.leave_type} ({self.start_date} to {self.end_date}) - {self.status}"
