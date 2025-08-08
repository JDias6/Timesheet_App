from datetime import date, timedelta

from django import forms
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.core.exceptions import ValidationError

from .models import LeaveEntitlement, LeaveRequest, LeaveType, User


class CustomUserCreationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ("username", "employee_id", "email")


class CustomUserChangeForm(UserChangeForm):
    class Meta:
        model = User
        fields = ("username", "employee_id", "email", "is_active", "is_staff")


class LeaveRequestForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ["leave_type", "start_date", "end_date", "comments"]
        widgets = {
            "start_date": forms.DateInput(
                attrs={
                    "type": "date",
                    "class": "form-control",
                    "min": date.today().isoformat(),
                }
            ),
            "end_date": forms.DateInput(
                attrs={
                    "type": "date",
                    "class": "form-control",
                    "min": date.today().isoformat(),
                }
            ),
            "leave_type": forms.Select(attrs={"class": "form-control"}),
            "comments": forms.Textarea(
                attrs={
                    "rows": 3,
                    "class": "form-control",
                    "placeholder": "Additional comments for your leave request....",
                }
            ),
        }
        labels = {
            "leave_type": "Leave Type",
            "start_date": "Start Date",
            "end_date": "End Date",
            "comments": "Additional Comments",
        }
        help_texts = {
            "start_date": "Select the first day of your leave",
            "end_date": "Select the last day of your leave",
            "comments": "Additional context for your request",
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        # only show active leave types
        self.fields["leave_type"].queryset = LeaveType.objects.filter(active=True)
        self.fields["leave_type"].empty_label = "Select leave type....."

        # Add CSS classes for styling
        for field_name, field in self.fields.items():
            field.widget.attrs.update(
                {"class": f"{field.widget.attrs.get('class', '')} form-control".strip()}
            )

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        leave_type = cleaned_data.get("leave_type")

        if not start_date or not end_date:
            return cleaned_data

        # Validating the date range
        if start_date > end_date:
            raise ValidationError("Start date cannot be after end date.")
        if start_date < date.today():
            raise ValidationError("Start date cannot be before today.")

        # check for overlapping requests
        if self.user:
            overlapping = LeaveRequest.objects.filter(
                user=self.user,
                status__in=["PENDING", "APPROVED"],
                start_date__lte=end_date,
                end_date__gte=start_date,
            )
            if self.instance.pk:
                overlapping = overlapping.exclude(pk=self.instance.pk)
            if overlapping.exists():
                raise ValidationError(
                    "You have an overlapping leave request. Please adjust your dates."
                )

        # Calculating and validating business days
        if start_date and end_date:
            temp_request = LeaveRequest(start_date=start_date, end_date=end_date)
            total_days = temp_request.calculate_business_days()
            cleaned_data["total_days"] = total_days

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)

        if hasattr(self, "cleaned_data") and "total_days" in self.cleaned_data:
            instance.total_days = self.cleaned_data["total_days"]

        if commit:
            instance.save()
        return instance


class LeaveFilterForm(forms.Form):
    STATUS_CHOICES = [("", "All Requests")] + LeaveRequest.STATUS_CHOICES

    status = forms.ChoiceField(
        choices=STATUS_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    leave_type = forms.ModelChoiceField(
        queryset=LeaveType.objects.filter(active=True),
        required=False,
        empty_label="All Leave Types",
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        label="From Date",
    )

    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        label="To Date",
    )
