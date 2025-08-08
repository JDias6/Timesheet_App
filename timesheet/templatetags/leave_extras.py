from django import template

register = template.Library()


@register.filter
def get_leave_detail(date, leave_details):
    # Get leave details for a specific date
    if date in leave_details:
        return leave_details[date]["type"]
    return None


@register.filter
def is_leave_day(date, approved_leave_days):
    # Check if a date is an approved leave day
    return date in approved_leave_days
