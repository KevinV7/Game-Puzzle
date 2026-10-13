from django.contrib.auth.decorators import login_not_required
from django.views.generic import TemplateView


def placeholder(title, message="Aún no disponible."):
    return TemplateView.as_view(
        template_name="pages/placeholder.html",
        extra_context={"title": title, "message": message},
    )


# Public entry pages: opt out of LoginRequiredMiddleware explicitly.
home = login_not_required(TemplateView.as_view(template_name="pages/home.html"))

# Protected by LoginRequiredMiddleware: no opt-out here on purpose.
play = placeholder("Jugar")
leaderboard = placeholder("Clasificación")

# Public stand-in so the navigation resolves until the instructions page lands.
instructions = login_not_required(placeholder("Instrucciones", "Página en construcción."))
