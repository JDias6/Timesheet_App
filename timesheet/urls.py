from django.urls import path

from . import views
from .views import add_row, confirm_add_row, weekly_timesheet

app_name = "timesheet"

urlpatterns = [
    # Show/edit this week
    path("timesheet/weekly/", weekly_timesheet, name="weekly"),
    # Optional: pass year/week for prev/next arrows
    path(
        "timesheet/weekly/<int:year>/<int:week_num>/",
        weekly_timesheet,
        name="weekly_with_date",
    ),
    path(
        "timesheet/remove-project/<int:project_id>/",
        views.remove_project,
        name="remove_project",
    ),
    path("timesheet/add-row/", add_row, name="add_row"),
    path("timesheet/confirm-add-row/", confirm_add_row, name="confirm_add_row"),
    # Employee Leave management
    path("leave_request/request/", views.leave_request_form, name="leave_request"),
    path("leave_request/my_requests/", views.my_leave_requests, name="my_requests"),
    # path("leave_request/balance/", views.leave_bala)
    # Manager functionality
    path("leave_request/manager/", views.manager_dashboard, name="manager_dashboard"),
    path(
        "leave_request/approve-reject/<int:request_id>/",
        views.approve_reject_leave,
        name="approve_reject",
    ),
    path(
        "leave_request/cancel-request/<int:request_id>/",
        views.cancel_leave_request,
        name="cancel_request",
    ),
    # HTMX endpoints
    path(
        "leave_request/calculate-days/",
        views.calculate_days_htmx,
        name="calculate_days_htmx",
    ),
    path(
        "leave_request/check-balance/",
        views.check_balance_htmx,
        name="check_balance_htmx",
    ),
]
