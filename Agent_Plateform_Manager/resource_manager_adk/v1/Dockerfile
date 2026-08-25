FROM python:3.11-slim

LABEL org.opencontainers.image.title="Resource Manager ADK" \
      org.opencontainers.image.version="1.0.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY resource_manager ./resource_manager

RUN python -m pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

CMD ["sh", "-c", "exec uvicorn resource_manager.service:service_app --host 0.0.0.0 --port ${PORT:-8080}"]
