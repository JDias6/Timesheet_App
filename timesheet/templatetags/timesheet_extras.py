from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    # This will lookup a key in a given dictionary and produce its value, else retun None if missing
    return dictionary.get(key)


@register.simple_tag
def get_entry(entries, project_id, day):
    # This will return the entry for a given project and day

    return entries.get((project_id, day), "")


@register.simple_tag
def get_pending_requests_count(user):
    # Get count of pending leave requests for manager
    if not user.direct_reports.exists():
        return 0

    from timesheet.models import LeaveRequest

    return LeaveRequest.objects.filter(user__manager=user, status="PENDING").count()


@register.simple_tag
def is_manager(user):
    # Check if user is a manager
    return user.direct_reports.exists()
