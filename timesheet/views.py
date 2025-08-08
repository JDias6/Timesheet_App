import json
import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Max, Q
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date

from .forms import LeaveFilterForm, LeaveRequestForm
from .models import LeaveEntitlement, LeaveRequest, LeaveType, Project, TimeEntry

HOURS_RE = re.compile(r"^hours_(\d+)_(\d{4}-\d{2}-\d{2})$")
logger = logging.getLogger(__name__)


@login_required
def weekly_timesheet(request, year=None, week_num=None):
    # 1) Determine the week window
    today = date.today()

    if request.method == "POST":
        viewing_year = request.POST.get("viewing_year")
        viewing_week = request.POST.get("viewing_week")
        if viewing_year and viewing_week:
            year, week_num = int(viewing_year), int(viewing_week)

    # For GET requests or if no form data, URL parameters can be used
    if year and week_num:
        year, week_num = int(year), int(week_num)
        week_start = date.fromisocalendar(year, week_num, 1)
    else:
        week_start = today - timedelta(days=today.weekday())

    # weekdays (Monday-Friday)
    days = [week_start + timedelta(days=i) for i in range(5)]
    print(f"DEBUG: days = {[d.strftime('%Y-%m-%d %A') for d in days]}")
    days_json = json.dumps([d.isoformat() for d in days])

    week_end = days[-1]

    # 2) Single session_key for this week
    iso_year, iso_week, _ = week_start.isocalendar()
    session_key = f"timesheet_draft_{iso_year}_{iso_week}"

    # Get approved leave days for this week
    approved_leave_days = set()
    leave_details = {}

    leave_requests = LeaveRequest.objects.filter(
        user=request.user,
        status="APPROVED",
        start_date__lte=week_end,
        end_date__gte=week_start,
    )

    for leave_request in leave_requests:
        # Calculate which days in this week are leave days
        current_date = max(leave_request.start_date, week_start)
        end_date = min(leave_request.end_date, week_end)

        while current_date <= end_date:
            # Only count business days (Monday-Friday) - skip weekends
            if current_date.weekday() < 5:  # 0=Monday, 4=Friday
                approved_leave_days.add(current_date)
                leave_details[current_date] = {
                    "type": leave_request.leave_type.code,
                    "request_id": leave_request.id,
                }
            current_date += timedelta(days=1)

    # 3) POST: Save draft or Submit
    if request.method == "POST":
        # DEBUG: see exactly what arrives
        print("===== FORM PAYLOAD =====")
        print(request.POST.dict())
        print("========================")

        action = request.POST.get("action")
        raw = {}
        daily_totals = {d: Decimal("0") for d in days}
        errors = []

        # parse fields hours_<pid>_<date>
        for name, val in request.POST.items():
            m = HOURS_RE.match(name)
            if not m or not val:
                continue
            pid_str, date_str = m.groups()

            dt = parse_date(date_str)
            if dt not in days:
                continue

            try:
                hrs = Decimal(val)
            except InvalidOperation:
                errors.append(f"Invalid hours for {date_str}.")
                continue

            daily_totals[dt] += hrs
            if daily_totals[dt] > Decimal("7.5"):
                errors.append(f"Cannot exceed 7.5 hrs on {dt:%a %m/%d}.")

            raw[(int(pid_str), dt)] = hrs

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            # --- SAVE DRAFT ---
            if action == "save":
                print(f"[DEBUG] raw → {raw}")
                print(f"[DEBUG] saving to database as drafts")  # noqa: F541

                with transaction.atomic():
                    for (pid, dt), hrs in raw.items():
                        project = get_object_or_404(
                            Project, pk=pid, members=request.user, active=True
                        )
                        TimeEntry.objects.update_or_create(
                            user=request.user,
                            project=project,
                            date=dt,
                            defaults={"hours": hrs, "submitted": False},
                        )
                request.session.pop(session_key, None)
                messages.success(request, "Timesheet saved successfully.")
                return redirect(request.path)

            # --- SUBMIT to DB ---
            if action == "submit":
                # Validation before submission
                if not raw:
                    messages.error(
                        request,
                        "Cannot submit an empty timesheet. Please enter your hours first.",
                    )
                    return redirect(request.path)

                # calculating total hours for the week
                manual_hours = sum(raw.values())
                leave_hours = len(approved_leave_days) * Decimal("7.5")
                total_week_hours = manual_hours + leave_hours
                expected_hours = Decimal("37.5")

                if total_week_hours < expected_hours:
                    messages.error(
                        request,
                        f"Timesheet incomplete. You have entered {total_week_hours} hours but need {expected_hours} hours for a full week.\n"
                        f"Please add {expected_hours - total_week_hours} more hours before submitting.",
                    )
                    return redirect(request.path)
                # if validation above passes then proceed with saving to the database
                with transaction.atomic():
                    for (pid, dt), hrs in raw.items():
                        project = get_object_or_404(
                            Project, pk=pid, members=request.user, active=True
                        )
                        TimeEntry.objects.update_or_create(
                            user=request.user,
                            project=project,
                            date=dt,
                            defaults={"hours": hrs, "submitted": True},
                        )
                request.session.pop(session_key, None)
                messages.success(request, "Timesheet submitted successfully.")
                return redirect(request.path)

    # GET (or POST-with-errors): load DB entries + overlay draft

    # IDs already in DB
    db_ids = set(
        TimeEntry.objects.filter(user=request.user, date__in=days)
        .values_list("project_id", flat=True)
        .distinct()
    )

    # IDs in session-draft
    draft = request.session.get(session_key, {})
    draft_ids = set()

    # Only use session data if there are no database entries for this week
    if not db_ids:
        for key in draft:
            if "|" not in key:
                continue
            pid_str, _ = key.split("|", 1)
            try:
                draft_ids.add(int(pid_str))
            except ValueError:
                pass

    # Union rows to show
    all_ids = db_ids | draft_ids

    print(f"[DEBUG GET] db_ids={db_ids} draft_ids={draft_ids} all_ids={all_ids}")

    projects = Project.objects.filter(id__in=all_ids, members=request.user, active=True)

    # Build entries map from DB + draft
    qs = TimeEntry.objects.filter(user=request.user, date__in=days)
    entries = {(e.project_id, e.date): e.hours for e in qs}

    # Only use session data if no database entries exist for the this week
    if not entries:
        for key, val in draft.items():
            if "|" not in key:
                continue
            pid_str, dt_str = key.split("|", 1)
            try:
                pid, dt = int(pid_str), parse_date(dt_str)
                hrs = Decimal(val)
            except (ValueError, InvalidOperation):
                continue
            entries[(pid, dt)] = hrs

    # Check for last submission timestamp
    # This helps record a form submission timestamp, in case a user decided to update a previous or current weeks hours
    last_submitted = TimeEntry.objects.filter(
        user=request.user, date__in=days, submitted=True
    ).aggregate(last_submitted=Max("date"))["last_submitted"]

    # 5) Totals
    project_totals = {
        p.id: sum(entries.get((p.id, d), 0) for d in days) for p in projects
    }
    # Calculate day totals including leave hours
    day_totals = {}
    for d in days:
        manual_hours = sum(entries.get((p.id, d), 0) for p in projects)
        leave_hours = Decimal("7.5") if d in approved_leave_days else Decimal("0")
        day_totals[d] = manual_hours + leave_hours

    # calculate week total including leave hours
    week_total = sum(day_totals.values())

    # 6) Projects available for “+ ADD ROW”
    available_projects = Project.objects.filter(
        members=request.user, active=True
    ).exclude(id__in=all_ids)

    context = {
        "week_start": week_start,
        "week_end": week_end,
        "days": days,
        "days_json": days_json,
        "projects": projects,
        "entries": entries,
        "project_totals": project_totals,
        "day_totals": day_totals,
        "week_total": week_total,
        "available_projects": available_projects,
        "prev_year": iso_year,
        "prev_week": iso_week - 1 or 52,
        "next_year": iso_year,
        "next_week": iso_week + 1,
        "last_submitted": last_submitted,
        "approved_leave_days": approved_leave_days,
        "leave_details": leave_details,
    }

    # Check if this is an HTMX request (for week navigation)
    if request.headers.get("HX-Request"):
        return render(
            request,
            "partials/timesheet_partial.html",
            context,
        )
    else:
        # Return the full page for regular requests
        return render(
            request,
            "timesheet/weekly_timesheet.html",
            context,
        )


@login_required
def remove_project(request, project_id):
    # If you needed to update the DB, you could do that here.
    # For now, just return “204 No Content” so HTMX removes the <tr>.
    if request.method in ["DELETE", "POST"]:
        # Return 200 status with empty content for HTMX remove swap to work
        return HttpResponse("", content_type="text/html", status=200)

    return HttpResponse("Method not allowed", status=405)


@login_required
def add_row(request):
    # same week logic as weekly_timesheet
    year = request.GET.get("year")
    week_num = request.GET.get("week")

    today = date.today()
    if year and week_num:
        year, week_num = int(year), int(week_num)
        week_start = date.fromisocalendar(year, week_num, 1)
    else:
        week_start = today - timedelta(days=today.weekday())

    days = [week_start + timedelta(days=i) for i in range(5)]

    # which projects already shown
    used_ids = set(
        TimeEntry.objects.filter(user=request.user, date__in=days)
        .values_list("project_id", flat=True)
        .distinct()
    )

    available_projects = Project.objects.filter(
        members=request.user, active=True
    ).exclude(id__in=used_ids)

    return render(
        request,
        "timesheet/add_row.html",
        {
            "days": days,
            "available_projects": available_projects,
        },
    )


@login_required
def confirm_add_row(request):
    pid = request.POST.get("project_id")
    if not pid:
        return HttpResponseBadRequest("no project_id")

    # pull in the project instance
    project = get_object_or_404(Project, pk=pid, members=request.user, active=True)

    year = request.GET.get("year")
    week_num = request.GET.get("week")

    # same week logic
    today = date.today()
    if year and week_num:
        year, week_num = int(year), int(week_num)
        week_start = date.fromisocalendar(year, week_num, 1)
    else:
        week_start = today - timedelta(days=today.weekday())
    days = [week_start + timedelta(days=i) for i in range(5)]

    # load DB entries and session draft exactly as in your main view...
    db_entries = {
        (e.project_id, e.date): e.hours
        for e in TimeEntry.objects.filter(user=request.user, date__in=days)
    }

    # session key must match weekly_timesheet’s key
    iso_year, iso_week, _ = week_start.isocalendar()
    session_key = f"timesheet_draft_{iso_year}_{iso_week}"
    draft = request.session.get(session_key, {})

    entries = db_entries.copy()
    for key, val in draft.items():
        pid_str, dt_str = key.split("|", 1)
        try:
            pid_i = int(pid_str)
            dt = parse_date(dt_str)
            entries[(pid_i, dt)] = Decimal(val)
        except Exception:
            continue

    # now compute totals exactly as your main GET does
    projects = Project.objects.filter(
        id__in={project.id},  # only the one we’re adding
        members=request.user,
        active=True,
    )

    project_totals = {
        p.id: sum(entries.get((p.id, d), 0) for d in days) for p in projects
    }

    # render the **full row**
    return render(
        request,
        "timesheet/row.html",
        {
            "project": project,
            "days": days,
            "entries": entries,
            "project_totals": project_totals,
        },
    )


@login_required
def leave_request_form(request):
    """Display leave request form with balance information"""
    current_year = date.today().year

    # Get user's leave entitlements for current year
    entitlements = LeaveEntitlement.objects.filter(
        user=request.user, year=current_year
    ).select_related("leave_type")

    if request.method == "POST":
        form = LeaveRequestForm(request.POST, user=request.user)
        if form.is_valid():
            # Save the form but don't commit to database yet
            leave_request = form.save(commit=False)
            # Set the user BEFORE any validation
            leave_request.user = request.user

            # Calculate business days if not already set
            if not leave_request.total_days:
                leave_request.total_days = leave_request.calculate_business_days()

            # Validate entitlement
            try:
                entitlement = LeaveEntitlement.objects.get(
                    user=leave_request.user,
                    leave_type=leave_request.leave_type,
                    year=leave_request.start_date.year,
                )

                if leave_request.total_days > entitlement.remaining_days:
                    messages.error(
                        request,
                        f"Insufficient leave balance. You have {entitlement.remaining_days} "
                        f"days remaining for {leave_request.leave_type.name}.",
                    )
                    # Re-render the form with error
                    return render(
                        request,
                        "leave_request/leave_request_form.html",
                        {
                            "form": form,
                            "entitlements": entitlements,
                            "current_year": current_year,
                        },
                    )

            except LeaveEntitlement.DoesNotExist:
                messages.error(
                    request,
                    f"No leave entitlement found for {leave_request.leave_type.name} in {leave_request.start_date.year}.",
                )
                return render(
                    request,
                    "leave_request/leave_request_form.html",
                    {
                        "form": form,
                        "entitlements": entitlements,
                        "current_year": current_year,
                    },
                )

            # Now save to database (skip model validation)
            leave_request.save()

            email_results = {"confirmation": False, "manager": False}

            # Send confirmation email
            try:
                email_results["confirmation"] = leave_request.send_confirmation_email()
            except Exception as e:
                logger.error(f"Unexpected error sending confirmation email: {str(e)}")

                # send confirmation to manager
            try:
                email_results["manager"] = leave_request.send_manager_notification()
            except Exception as e:
                logger.error(f"Unexpected error sending manager notification: {str(e)}")

            success_message = (
                f"Leave request submitted successfully! "
                f"You've requested {leave_request.total_days} days from "
                f"{leave_request.start_date} to {leave_request.end_date}."
            )

            if email_results["confirmation"] and email_results["manager"]:
                success_message += (
                    " Confirmation emails have been sent to you and your manager."
                )
            elif email_results["confirmation"]:
                success_message += " A confirmation email has been sent to you."
                messages.warning(
                    request, "Note: Could not send notification to your manager."
                )
            elif email_results["manager"]:
                success_message += " A notification has been sent to your manager."
                messages.warning(
                    request, "Note: Could not send confirmation email to you."
                )
            else:
                success_message += (
                    " However, email notifications could not be sent at this time."
                )
                messages.warning(
                    request,
                    "Email system is currently unavailable. Please contact HR to confirm your request was received.",
                )
            messages.success(request, success_message)
            messages.info(
                request,
                "You can view the status of your request anytime in 'My Requests'.",
            )

            return redirect("timesheet:leave_request")
    else:
        form = LeaveRequestForm(user=request.user)

    return render(
        request,
        "leave_request/leave_request_form.html",
        {"form": form, "entitlements": entitlements, "current_year": current_year},
    )


def my_leave_requests(request):
    """Display user's leave requests with filtering"""
    requests = LeaveRequest.objects.filter(user=request.user).order_by("-created")

    # Apply filters using form
    filter_form = LeaveFilterForm(request.GET)
    if filter_form.is_valid():
        status = filter_form.cleaned_data.get("status")
        leave_type = filter_form.cleaned_data.get("leave_type")
        date_from = filter_form.cleaned_data.get("date_from")
        date_to = filter_form.cleaned_data.get("date_to")

        if status:
            requests = requests.filter(status=status)
        if leave_type:
            requests = requests.filter(leave_type=leave_type)
        if date_from:
            requests = requests.filter(start_date__gte=date_from)
        if date_to:
            requests = requests.filter(end_date__lte=date_to)

    # Pagination
    paginator = Paginator(requests, 10)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "page_obj": page_obj,
        "filter_form": filter_form,
        "status_choices": LeaveRequest.STATUS_CHOICES,
    }

    if request.headers.get("HX-Request"):
        # Return only the table partial
        return render(request, "partials/leave_requests_table.html", context)
    else:
        return render(request, "leave_request/my_leave_request.html", context)


# Add these views to your existing views.py


@login_required
def calculate_days_htmx(request):
    """HTMX endpoint to calculate business days and show result"""
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")
    leave_type_id = request.GET.get("leave_type")

    if not start_date or not end_date:
        return render(
            request,
            "partials/leave_days_calculation.html",
            {"days": 0, "show_calculation": False},
        )

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()

        if start > end:
            return render(
                request,
                "partials/leave_days_calculation.html",
                {
                    "error": "Start date must be before end date",
                    "show_calculation": False,
                },
            )

        # Calculate business days
        temp_request = LeaveRequest(start_date=start, end_date=end)
        days = temp_request.calculate_business_days()

        # Check balance if leave type is selected
        balance_warning = False
        balance_message = ""

        if leave_type_id:
            try:
                leave_type = LeaveType.objects.get(id=leave_type_id)
                entitlement = LeaveEntitlement.objects.get(
                    user=request.user, leave_type=leave_type, year=start.year
                )

                if days > entitlement.remaining_days:
                    balance_warning = True
                    balance_message = f"You have {entitlement.remaining_days} days remaining for {leave_type.name}, but you're requesting {days} days."

            except (LeaveType.DoesNotExist, LeaveEntitlement.DoesNotExist):
                balance_warning = True
                balance_message = (
                    f"No leave entitlement found for this leave type in {start.year}."
                )

        return render(
            request,
            "partials/leave_days_calculation.html",
            {
                "days": days,
                "show_calculation": True,
                "balance_warning": balance_warning,
                "balance_message": balance_message,
            },
        )

    except (ValueError, TypeError):
        return render(
            request,
            "partials/leave_days_calculation.html",
            {"error": "Invalid date format", "show_calculation": False},
        )


@login_required
def check_balance_htmx(request):
    """HTMX endpoint to display leave balance information"""
    leave_type_id = request.GET.get("leave_type")
    current_year = date.today().year

    if not leave_type_id:
        return render(
            request, "partials/leave_balance_info.html", {"show_balance": False}
        )

    try:
        leave_type = LeaveType.objects.get(id=leave_type_id)
        entitlement = LeaveEntitlement.objects.get(
            user=request.user, leave_type=leave_type, year=current_year
        )

        return render(
            request,
            "partials/leave_balance_info.html",
            {
                "show_balance": True,
                "leave_type": leave_type,
                "entitlement": entitlement,
            },
        )

    except (LeaveType.DoesNotExist, LeaveEntitlement.DoesNotExist):
        return render(
            request,
            "partials/leave_balance_info.html",
            {
                "show_balance": True,
                "error": f"No leave entitlement found for this leave type in {current_year}.",
            },
        )


@login_required
def manager_dashboard(request):
    """Dashboard for managers to review leave requests"""
    # Check if user is a manager (has direct reports)
    if not request.user.direct_reports.exists():
        messages.error(
            request, "You don't have permission to access the manager dashboard."
        )
        return redirect("timesheet:my_requests")

    # Get pending requests for direct reports
    pending_requests = (
        LeaveRequest.objects.filter(user__manager=request.user, status="PENDING")
        .select_related("user", "leave_type")
        .order_by("created")
    )

    # Get recent decisions
    recent_decisions = (
        LeaveRequest.objects.filter(
            approved_by=request.user, status__in=["APPROVED", "REJECTED"]
        )
        .select_related("user", "leave_type")
        .order_by("-approved_at")[:10]
    )

    return render(
        request,
        "leave_request/manager_dashboard.html",
        {"pending_requests": pending_requests, "recent_decisions": recent_decisions},
    )


@login_required
def approve_reject_leave(request, request_id):
    """HTMX endpoint for approving/rejecting leave requests"""
    leave_request = get_object_or_404(LeaveRequest, pk=request_id)

    # Check permissions
    if not request.user.is_manager_of(leave_request.user):
        logger.warning(
            f"User {request.user} attempted to approve/reject request {request.id} wihout permission"
        )
        return JsonResponse({"error": "Permission denied"}, status=403)

    if request.method == "POST":
        action = request.POST.get("action")
        logger.info(
            f"Processing {action} action for request {request_id} by manager {request.user}"
        )

        if action == "approve":
            try:
                leave_request.approve(request.user)
                logger.info(f"Leave request {request_id} approved by {request.user}")
                messages.success(
                    request,
                    f"Leave request for {leave_request.user.get_full_name()} approved successfully.",
                )
            except Exception as e:
                logger.error(f"Error approving request {request_id}: {str(e)}")
                messages.error(request, "Failed to approve leave request.")
                return HttpResponse("Error approving request", status=500)

        elif action == "reject":
            reason = request.POST.get("rejection_reason", "").strip()
            if not reason:
                messages.error(request, "Please provide a rejection reason.")
                return HttpResponse("Rejection reason required", status=400)
            try:
                leave_request.reject(request.user, reason)
                logger.info(f"Leave request {request_id} rejected by {request.user}")
                messages.success(
                    request,
                    f"Leave request for {leave_request.user.get_full_name()} rejected.",
                )
            except Exception as e:
                logger.error(f"Error rejecting request {request_id}: {str(e)}")
                messages.error(request, "Failed to reject leave request.")
                return HttpResponse("Error rejecting request", status=500)

        else:
            logger.warning(f"Invalid action '{action}' for request {request_id}")
            return HttpResponse("Invalid action", status=400)

        # Return empty response to remove/update the request row
        return HttpResponse("", content_type="text/html", status=200)

    # GET requests aren't needed since the manager dashboard
    # handles the approve/reject forms inline
    return HttpResponse("Method not allowed", status=405)


@login_required
def cancel_leave_request(request, request_id):
    """HTMX endpoint to cancel a leave request"""
    leave_request = get_object_or_404(LeaveRequest, pk=request_id, user=request.user)

    if leave_request.status != "PENDING":
        return HttpResponse("Cannot cancel non-pending requests", status=400)

    leave_request.status = "CANCELLED"
    leave_request.save()

    # Return empty response to remove the row
    return HttpResponse(status=204)
