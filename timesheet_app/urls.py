"""
URL configuration for timesheet_app project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path
from django.views.generic import TemplateView
from timesheet.views import weekly_timesheet


def home_view(request):
    """Smart redirect based on login status"""
    if request.user.is_authenticated:
        # Redirect logged-in users to timesheet
        return redirect("timesheet:weekly")
    else:
        # Redirect anonymous users to login
        return redirect("login")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("timesheet.urls", namespace="timesheet")),
    path("leave_request/", include("timesheet.urls", namespace="leave_request")),
    path("", home_view, name="home"),
]
