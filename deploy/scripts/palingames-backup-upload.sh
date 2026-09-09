#!/usr/bin/env bash
set -euo pipefail
umask 077

ENV_NAME="${1:?Usage: $0 prod|staging}"
GPG_PASS_FILE="/root/.config/palingames/backup-gpg.pass"
TG_ENV_FILE="/root/.config/palingames/backup-telegram.env"
RCLONE_REMOTE="gdrive:palingames-backups"

notify_failure() {
  local text="$1"
  [[ -f "$TG_ENV_FILE" ]] || return 0
  # shellcheck disable=SC1090
  set -a; source "$TG_ENV_FILE"; set +a
  curl -sS -m 10 -o /dev/null \
    -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TG_CHAT_ID}" \
    --data-urlencode "message_thread_id=${TG_THREAD_ID}" \
    --data-urlencode "text=${text}" || true
}

on_exit() {
  local code=$?
  rm -f "${DUMP_PLAIN:-}"
  if (( code != 0 )); then
    notify_failure "❌ Backup ${ENV_NAME} FAILED (exit ${code}) on $(hostname) at $(date -Is)"
  fi
}
trap on_exit EXIT

case "$ENV_NAME" in
  prod)
    DEPLOY_DIR="/opt/palingames-prod/deploy"
    COMPOSE_PROJECT_NAME="palingames-prod"
    DB_NAME="palingames"
    PREFIX="prod"
    ;;
  staging)
    DEPLOY_DIR="/opt/palingames-staging/deploy"
    COMPOSE_PROJECT_NAME="palingames-staging"
    DB_NAME="palingames_staging"
    PREFIX="staging"
    ;;
  *)
    echo "Unknown env: $ENV_NAME" >&2
    exit 1
    ;;
esac

exec 9>"/var/lock/palingames-backup-${ENV_NAME}.lock"
if ! flock -n 9; then
  echo "$(date -Is) SKIP ${ENV_NAME}: another run in progress"
  exit 0
fi

if [[ ! -f "$GPG_PASS_FILE" ]]; then
  echo "Missing GPG passphrase file: $GPG_PASS_FILE" >&2
  exit 1
fi

cd "$DEPLOY_DIR"
export COMPOSE_PROJECT_NAME
mkdir -p backups

compose() {
  docker compose -f docker-compose.prod.yml -f docker-compose.override.yml "$@"
}

DATE_TAG="$(date +%Y%m%d)"
DUMP_PLAIN="backups/${PREFIX}-${DATE_TAG}.dump"
DUMP_ENC="${DUMP_PLAIN}.gpg"
LOG_FILE="/var/log/palingames-${PREFIX}-backup-upload.log"

compose exec -T postgres \
  pg_dump -U palingames -d "$DB_NAME" --no-owner --format=custom \
  > "$DUMP_PLAIN"

test -s "$DUMP_PLAIN"

gpg --batch --yes --pinentry-mode loopback \
  --passphrase-file "$GPG_PASS_FILE" \
  --symmetric --cipher-algo AES256 \
  -o "$DUMP_ENC" "$DUMP_PLAIN"

# проверяем сам зашифрованный артефакт: расшифровывается той же парольной
# фразой и содержит читаемый дамп
gpg --batch --quiet --pinentry-mode loopback \
  --passphrase-file "$GPG_PASS_FILE" --decrypt "$DUMP_ENC" \
  | compose exec -T postgres pg_restore --list > /dev/null

rclone copy "$DUMP_ENC" "${RCLONE_REMOTE}/${PREFIX}/" \
  --log-file "$LOG_FILE" --log-level INFO

find backups -name "${PREFIX}-*.dump.gpg" -mtime +7 -delete

rclone delete "${RCLONE_REMOTE}/${PREFIX}/" \
  --min-age 30d \
  --include "${PREFIX}-*.dump.gpg" \
  --log-file "$LOG_FILE" --log-level INFO

echo "$(date -Is) OK ${PREFIX} -> Drive (${DUMP_ENC})"
