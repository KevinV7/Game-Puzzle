from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render

from .forms import SignupForm


@login_not_required
def signup(request):
    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect(settings.LOGIN_REDIRECT_URL)
    else:
        form = SignupForm()
    return render(request, "accounts/signup.html", {"form": form})


# Django's views, opted out of LoginRequiredMiddleware explicitly so that being
# public never depends on framework defaults. LogoutView rejects GET with 405.
login_view = login_not_required(LoginView.as_view())
logout_view = login_not_required(LogoutView.as_view())
