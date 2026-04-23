# Production image: build from repository root:
#   docker build -f deploy/Dockerfile -t yourdockerhub/palingames:tag .
#
# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings

RUN --mount=type=cache,target=/var/cache/apt \
    apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra web --no-install-project

COPY . /app/

# Build-time only: satisfy settings + build CSS + collect static for Whitenoise manifest
# (export so every `uv run` in the chain sees the same env)
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eux; \
    export DJANGO_SECRET_KEY="build-only-unsafe-secret-key-for-collectstatic"; \
    export DJANGO_DEBUG=False; \
    export DJANGO_USE_WHITENOISE=true; \
    export TAILWIND_CLI_USE_MINIFY=true; \
    export DATABASE_URL="sqlite:////tmp/build.db"; \
    export APP_DATA_ENCRYPTION_KEY="5AZwcbvUq7egV4dW9zPP_BHqp-KeQK3j16ZZ8S8_L4A="; \
    export GOOGLE_OAUTH_CLIENT_ID="build"; \
    export GOOGLE_OAUTH_CLIENT_SECRET="build"; \
    export YANDEX_OAUTH_CLIENT_ID="build"; \
    export YANDEX_OAUTH_CLIENT_SECRET="build"; \
    uv run python manage.py tailwind download_cli; \
    uv run python manage.py tailwind build; \
    uv run python manage.py collectstatic --noinput

ARG SENTRY_RELEASE=
ENV SENTRY_RELEASE=${SENTRY_RELEASE}

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "3", "--threads", "2", "--timeout", "120", "config.wsgi:application"]
