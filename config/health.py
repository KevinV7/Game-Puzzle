from django.contrib.auth.decorators import login_not_required
from django.db import Error, connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET


@login_not_required
@require_GET
def healthz(request):
    """Liveness plus database reachability; public so probes need no session."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Error:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
