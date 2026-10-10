from django.contrib.auth.decorators import login_not_required
from django.views.generic import TemplateView


def placeholder(title, message="Aún no disponible."):
    return TemplateView.as_view(
        template_name="pages/placeholder.html",
        extra_context={"title": title, "message": message},
    )


# Protected by LoginRequiredMiddleware: no opt-out here on purpose.
play = placeholder("Jugar")
leaderboard = placeholder("Clasificación")

# Public stand-ins so the navigation and the post-login redirect resolve.
# The home and instructions pages replace these views in the pages slice.
home = login_not_required(placeholder("Inicio", "Página en construcción."))
instructions = login_not_required(placeholder("Instrucciones", "Página en construcción."))
