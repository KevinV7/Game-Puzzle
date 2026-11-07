#!/usr/bin/env bash
# Smoke test against the running stack through the proxy.
# Usage: scripts/smoke.sh   (BASE_URL defaults to http://localhost:${PROXY_PORT:-8080})
set -u

BASE_URL="${BASE_URL:-http://localhost:${PROXY_PORT:-8080}}"
JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT
FAILURES=0

check() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "PASS  $label ($actual)"
  else
    echo "FAIL  $label (expected $expected, got $actual)"
    FAILURES=$((FAILURES + 1))
  fi
}

status() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

# 1. Every service reports healthy (allow up to 60s for healthchecks to settle).
unhealthy=""
for _ in $(seq 1 30); do
  unhealthy="$(docker compose ps --format '{{.Service}} {{.Health}}' | awk '$2 != "healthy"')"
  [ -z "$unhealthy" ] && break
  sleep 2
done
check "all services healthy" "" "$unhealthy"

# 2. Health endpoint and static files come through the proxy.
check "GET /healthz/" 200 "$(status "$BASE_URL/healthz/")"
check "static CSS served by nginx" 200 "$(status "$BASE_URL/static/admin/css/base.css")"
ctype="$(curl -s -o /dev/null -w '%{content_type}' "$BASE_URL/static/admin/css/base.css")"
check "static CSS content type" "text/css" "${ctype%%;*}"

# 3. Site pages, access guard and a CSRF login POST through the proxy.
check "GET /" 200 "$(status "$BASE_URL/")"
check "static site.css served by nginx" 200 "$(status "$BASE_URL/static/css/site.css")"
check "anonymous /jugar/ redirects" 302 "$(status "$BASE_URL/jugar/")"

smoke_pass="smoke-$(date +%s)-Aa1!"
docker compose exec -T web python manage.py shell -c "
from django.contrib.auth import get_user_model
U = get_user_model()
u, _ = U.objects.get_or_create(username='smoke_user', defaults={'email': 'smoke_user@example.com'})
u.set_password('$smoke_pass')
u.save()
" >/dev/null

curl -s -c "$JAR" -o /dev/null "$BASE_URL/cuentas/entrar/"
token="$(awk '$6 == "csrftoken" {print $7}' "$JAR")"
code="$(curl -s -b "$JAR" -c "$JAR" -o /dev/null -w '%{http_code}' \
  -H "Origin: $BASE_URL" -H "Referer: $BASE_URL/cuentas/entrar/" \
  --data-urlencode "csrfmiddlewaretoken=$token" \
  --data-urlencode "username=smoke_user" \
  --data-urlencode "password=$smoke_pass" \
  "$BASE_URL/cuentas/entrar/")"
check "login POST with CSRF through proxy" 302 "$code"
check "authenticated /jugar/ is reachable" 200 "$(status -b "$JAR" "$BASE_URL/jugar/")"

if [ "$FAILURES" -gt 0 ]; then
  echo "$FAILURES check(s) failed"
  exit 1
fi
echo "Smoke test passed"
