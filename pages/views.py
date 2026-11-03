from django.contrib.auth.decorators import login_not_required
from django.views.generic import TemplateView

# Public entry pages: opt out of LoginRequiredMiddleware explicitly.
home = login_not_required(TemplateView.as_view(template_name="pages/home.html"))
instructions = login_not_required(TemplateView.as_view(template_name="pages/instructions.html"))

# The protected pages live in their own apps: Play in game/urls.py, Perfil and
# Clasificación in scoring/urls.py. No opt-out here on purpose.
