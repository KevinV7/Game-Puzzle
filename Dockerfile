FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Non-root runtime user. staticfiles is created here so the named volume mounted
# on it inherits this ownership on first use.
RUN useradd --system --create-home --uid 1000 app \
    && mkdir -p /app/staticfiles \
    && chown app:app /app/staticfiles

COPY --chown=app:app . .
RUN chmod +x docker-entrypoint.sh

USER app
EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "-c", "gunicorn.conf.py"]
