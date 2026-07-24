#!/usr/bin/env bash
# Example VPS deploy: pull published image and restart stack (run on the server).
set -euo pipefail

cd "$(dirname "$0")/.."

: "${PALINGAMES_WEB_REF:?Set PALINGAMES_WEB_REF=yourdockerhub/palingames:tag}"

: "${PALINGAMES_BOT_REF:?Set PALINGAMES_BOT_REF=yourdockerhub/palingames-bot:tag}"

docker compose -f docker-compose.prod.yml pull web celery-worker celery-beat telegram-bot
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml exec -T web python manage.py migrate --noinput
docker compose -f docker-compose.prod.yml exec -T web python manage.py setup_periodic_tasks
