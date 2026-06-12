## ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.11-slim AS base

LABEL org.opencontainers.image.title="HPC Pilot Manager"
LABEL org.opencontainers.image.description="Flask manager app for HPC Pilot"

# ── System packages ───────────────────────────────────────────────────────────
# curl + bash needed to install Helm; then cleaned up to keep the image small.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        bash \
        git \
        openssh-client \
    && apt-get remove -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

RUN curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# ── Python dependencies ───────────────────────────────────────────────────────
WORKDIR /app

# ── Application source ────────────────────────────────────────────────────────
COPY manager/. .

RUN pip install --no-cache-dir -r requirements.txt

# ── Runtime config ────────────────────────────────────────────────────────────
# FLASK_SECRET_KEY must be overridden at runtime (e.g. via a Kubernetes Secret).
ENV FLASK_PORT=5000
ENV FLASK_DEBUG=0
ENV FLASK_SECRET_KEY=dev-secret-change-in-production

EXPOSE 5000

# Use gunicorn for production; falls back gracefully if not installed.
CMD ["python", "main.py"]
