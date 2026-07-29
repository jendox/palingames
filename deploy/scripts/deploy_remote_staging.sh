#!/usr/bin/env bash
# Staging deploy: web + celery only (no telegram-bot — один TELEGRAM_BOT_TOKEN на prod).
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE=(docker compose -f docker-compose.prod.yml -f docker-compose.override.yml)
DEPLOY_SERVICES=(postgres redis web celery-worker celery-beat)
STATE_FILE=".deploy-state"
HEALTH_URL="http://127.0.0.1:8000/health/ready/"
MAX_RETRIES=12
RETRY_INTERVAL=5

# --- 1 ---
prompt_image_ref() {
  local var_name="$1"
  local prompt_text="$2"
  local default_value="${3:-}"

  if [[ -n "${!var_name:-}" ]]; then
    return 0
  fi

  if [[ ! -t 0 ]]; then
    echo "ERROR: $var_name is not set and stdin is not a TTY." >&2
    echo "Set it explicitly, e.g.:" >&2
    echo "  $var_name=yourdockerhub/palingames:abc123 $0" >&2
    exit 1
  fi

  if [[ -n "$default_value" ]]; then
    read -r -p "$prompt_text [$default_value]: " reply
    reply="${reply:-$default_value}"
  else
    read -r -p "$prompt_text: " reply
  fi

  if [[ -z "$reply" ]]; then
    echo "ERROR: $var_name cannot be empty." >&2
    exit 1
  fi

  printf -v "$var_name" '%s' "$reply"
  export "$var_name"
}

prompt_image_ref PALINGAMES_WEB_REF "Web image (PALINGAMES_WEB_REF)"

NEW_WEB="$PALINGAMES_WEB_REF"

# --- 2 ---
PREV_WEB=$(grep '^CURRENT_WEB_REF=' "$STATE_FILE" 2>/dev/null | cut -d= -f2- || true)

# --- 3 ---
wait_for_ready() {
  local i
  for i in $(seq 1 "$MAX_RETRIES"); do
    if "${COMPOSE[@]}" exec -T web python -c "
import urllib.request
urllib.request.urlopen('${HEALTH_URL}')
"; then
      echo "Health check OK (attempt $i/$MAX_RETRIES)"
      return 0
    fi
    echo "Waiting for ready... ($i/$MAX_RETRIES)"
    sleep "$RETRY_INTERVAL"
  done
  return 1
}

rollback() {
  if [[ -z "$PREV_WEB" ]]; then
    echo "No previous web ref in $STATE_FILE — rollback impossible." >&2
    return 1
  fi
  echo "Rolling back to web=$PREV_WEB"
  export PALINGAMES_WEB_REF="$PREV_WEB"
  "${COMPOSE[@]}" pull web celery-worker celery-beat
  "${COMPOSE[@]}" up -d "${DEPLOY_SERVICES[@]}"
}

# --- 4 ---
echo "Deploy plan (staging, no telegram-bot):"
echo "  web: $NEW_WEB (prev: ${PREV_WEB:-none})"
if [[ -t 0 ]]; then
  read -r -p "Continue? [y/N]: " confirm
  [[ "$confirm" =~ ^[Yy]$ ]] || exit 0
fi

"${COMPOSE[@]}" pull web celery-worker celery-beat
"${COMPOSE[@]}" run --rm web python manage.py migrate --noinput
"${COMPOSE[@]}" up -d "${DEPLOY_SERVICES[@]}"

# --- 5 ---
if ! wait_for_ready; then
  echo "ERROR: health check failed for $NEW_WEB" >&2

  if [[ -t 0 ]] && [[ -n "$PREV_WEB" ]]; then
    read -r -p "Health check failed. Rollback to $PREV_WEB? [y/N]: " ans
    if [[ "$ans" =~ ^[Yy]$ ]]; then
      if rollback; then
        if wait_for_ready; then
          echo "Deploy FAILED; rollback restored previous version ($PREV_WEB)." >&2
        else
          echo "Deploy FAILED; rollback also failed (health) — manual intervention required." >&2
        fi
      else
        echo "Deploy FAILED; rollback command failed — manual intervention required." >&2
      fi
    fi
  else
    [[ -z "$PREV_WEB" ]] && echo "No previous ref — rollback skipped." >&2
    [[ ! -t 0 ]] && echo "Non-interactive — rollback skipped." >&2
  fi

  exit 1
fi

# --- 6 ---
"${COMPOSE[@]}" exec -T web python manage.py setup_periodic_tasks

cat > "$STATE_FILE" <<EOF
PREVIOUS_WEB_REF=$PREV_WEB
CURRENT_WEB_REF=$NEW_WEB
DEPLOYED_AT=$(date -Iseconds)
EOF

echo "Deploy OK (staging): web=$NEW_WEB"
