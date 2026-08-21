# Production deploy (Docker + VPS)

Содержимое каталога: **образ приложения**, **prod docker-compose**, **Caddy (standalone)**, **Prometheus/Grafana для сервера**, скрипты и пример переменных окружения.

Локальный dev-стек по-прежнему в корне: `docker-compose.develop.yml` и `monitoring/`.

## VPS с общим reverse proxy (`/opt/proxy`)

На production VPS PalinGames **не** публикует порты 80/443 и **не** запускает сервис `caddy` из [`docker-compose.prod.yml`](docker-compose.prod.yml). Edge TLS — отдельный проект **`/opt/proxy`** (контейнер `proxy-caddy-1`, Docker-сеть **`proxy`**).

Подробный runbook: локальный [`.cursor/plans/Dev and Prod deployment.md`](../.cursor/plans/Dev%20and%20Prod%20deployment.md) §4.0–§4.4, [Path B](../.cursor/plans/Staging%20and%20Prod%20parallel%20-%20Path%20B.md).

```bash
# один раз на VPS
docker network create proxy
cd /opt/proxy && docker compose up -d

# PalinGames prod (пример)
cd /opt/palingames-prod/deploy
export COMPOSE_PROJECT_NAME=palingames-prod
COMPOSE="docker compose -f docker-compose.prod.yml -f docker-compose.override.yml"
$COMPOSE up -d postgres redis web celery-worker celery-beat telegram-bot
# override на VPS: web + telegram-bot в external network proxy
```

Site configs: `/opt/proxy/sites/palingames-prod.caddy`, `/opt/proxy/sites/palingames-staging.caddy`. После правок:

```bash
docker exec proxy-caddy-1 caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker exec proxy-caddy-1 caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

Сервис `caddy` в базовом compose — для **standalone** VPS (единственный сайт). См. § «Быстрый старт» ниже.

### Origin firewall (Cloudflare only)

На production VPS HTTP/HTTPS к origin — **только** с официальных IP Cloudflare:

- **UFW** — SSH (`22/tcp LIMIT`); не открывать 80/443 в UFW
- **DOCKER-USER + ipset** — published ports `proxy-caddy-1` (80/tcp, 443/tcp, 443/udp DROP для не-CF)
- `/usr/local/sbin/cloudflare-origin-firewall` + systemd timer (ежедневное обновление диапазонов)

Runbook: [Dev and Prod deployment §4.4](../.cursor/plans/Dev%20and%20Prod%20deployment.md). После деплоя — §4.4.11; reboot-тест — §4.4.12 (вручную, не автоматически на prod).

## Быстрый старт на VPS (standalone — PalinGames единственный сайт)

1. Установите Docker и Docker Compose plugin.
2. Склонируйте репозиторий (или скопируйте только каталог `deploy/` и при необходимости `docker-compose.prod.yml` + конфиги).
3. `cd deploy`
4. `cp env.example .env` и заполните секреты (в т.ч. `DJANGO_SECRET_KEY`, `APP_DATA_ENCRYPTION_KEY`, OAuth, `DATABASE_URL` с паролем).
   `PALINGAMES_WEB_REF` / `PALINGAMES_BOT_REF` в `.env` **не обязательны** — задаются при запуске [`deploy_remote.sh`](#деплой-обновлений-scriptsdeploy_remote-sh).
5. Выставьте боевой домен: `CADDY_DOMAIN=shop.example.com` (для Let’s Encrypt не указывайте схему `https://`).
6. Поднимите стек:
   - **Сборка на сервере:**
     `docker compose -f docker-compose.prod.yml up -d --build`
   - **Только образ с Docker Hub:** после первичного bootstrap используйте [`scripts/deploy_remote_staging.sh`](#деплой-обновлений-staging-scriptsdeploy_remote_staging-sh) (staging) или [`scripts/deploy_remote.sh`](#деплой-обновлений-prod-scriptsdeploy_remote-sh) (prod).
   - **Сборка на сервере (редко):**
     `docker compose -f docker-compose.prod.yml up -d --build`
7. **Первичный bootstrap** (один раз): migrate, `setup_periodic_tasks`, `createsuperuser` — см. команды ниже или § «Деплой обновлений» для последующих релизов.
   ```bash
   docker compose -f docker-compose.prod.yml exec web python manage.py migrate --noinput
   docker compose -f docker-compose.prod.yml exec web python manage.py setup_periodic_tasks
   docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser
   ```
8. Periodic tasks: при каждом деплое через `deploy_remote.sh` выполняются автоматически; вручную — `setup_periodic_tasks` (идемпотентно).
9. Справочник каталога — см. раздел [«Справочник каталога (tags_fixture.json)»](#справочник-каталога-tags_fixturejson) (staging и prod, один раз после migrate).

## Справочник каталога (`tags_fixture.json`)

Файл `tags_fixture.json` в **корне репозитория** (локально) содержит эталонные данные каталога:

- категории (`products.category`);
- подтипы (`products.subtype`);
- возрастные группы (`products.agegrouptag`);
- области развития (`products.developmentareatag`);
- темы (`products.theme`).

Файл в `.gitignore` — **в Docker-образ из CI не попадает**. На VPS его нужно скопировать вручную (staging и prod — отдельно, если БД разные).

**Когда загружать:** после `migrate`, лучше на **пустой БД** (до массового импорта товаров). Повторный `loaddata` на уже заполненную БД может упасть с `IntegrityError` (дубли PK/slug).

**Локально** (см. также [README](../README.md)):

```bash
uv run python manage.py loaddata tags_fixture.json
```

**Staging / prod (Docker на VPS):**

1. Скопировать файл на сервер (с локальной машины, где лежит актуальный `tags_fixture.json`):

```bash
scp tags_fixture.json user@YOUR_VPS:/opt/palingames-stagging/tags_fixture.json
```

Для prod замените путь на каталог prod-деплоя (например `/opt/palingames-prod/`).

2. Положить файл в контейнер `web` и загрузить в БД:

```bash
ssh user@YOUR_VPS
cd /opt/palingames-stagging/deploy   # или ваш каталог с docker-compose.prod.yml

docker compose -f docker-compose.prod.yml cp \
  ../tags_fixture.json web:/app/tags_fixture.json

docker compose -f docker-compose.prod.yml exec web \
  python manage.py loaddata tags_fixture.json
```

3. Проверка в админке: **Категории**, **Подтипы**, **Темы**, **Возрастные группы**, **Области развития**. На сайте фильтры каталога и ссылки в footer (`?category=...`) должны совпадать со slug из фикстуры.

**Обновить эталон** с dev-БД (локально):

```bash
uv run python manage.py dumpdata products.category products.subtype \
  products.agegrouptag products.developmentareatag products.theme \
  --indent 2 -o tags_fixture.json
```

**Демо-товары (опционально):** если в репозитории есть `apps/products/fixtures/demo_products.json`, загружайте **после** `tags_fixture.json` — в M2M там ссылки на PK категорий/тегов из справочника:

```bash
# локально
uv run python manage.py loaddata demo_products

# VPS
docker compose -f docker-compose.prod.yml exec web \
  python manage.py loaddata demo_products
```

## Метрики и доступ

- Приложение **не публикует порт 8000** наружу: с интернета идёт только трафик через **Caddy** (80/443). На VPS с `/opt/proxy` — через `proxy-caddy-1`.
- **Prometheus** скрапит `http://web:8000/metrics/` во внутренней сети compose.
- В конфиге Caddy путь `/metrics` отвечает **404** (standalone: [`Caddyfile`](Caddyfile); VPS: `/opt/proxy/sites/palingames-*.caddy`).
- UI **Prometheus** проброшен только на localhost VPS: `127.0.0.1:9090`; **Grafana** — `127.0.0.1:3000` (доступ по SSH-туннелю или с хоста).

Подробный operational contract: [docs/observability.md](../docs/observability.md).

## Алертинг (MVP: Telegram-only)

**Решение для MVP:** paging оператору — через **app-level Telegram incidents**, не через Alertmanager.

| Слой | Назначение | MVP |
|------|------------|-----|
| **Prometheus + Grafana** | метрики, dashboards, rule evaluation | ✅ подняты в compose |
| **Prometheus alert rules** (`deploy/prometheus/alerts.yml`) | тренды, readiness, массовые симптомы | ✅ считаются; смотреть в UI / Grafana |
| **App incidents** (`apps/core/alerts.py` → `TELEGRAM_INCIDENTS_THREAD_ID`) | платежи, sync, downloads, outbox, storage | ✅ **основной канал внимания** |
| **Alertmanager** | маршрутизация Prometheus alerts в email/Telegram | ❌ **не настраиваем на MVP** (stub в репозитории) |

Что заполнить в prod `.env` для алертинга:

Исходящие (admin notifications + incidents) идут через **Redis Streams** → сервис **`telegram-bot`** (не через прямой Bot API из `web`/`celery`). См. § «Образ Telegram bot».

**Django / Celery** (publish + ack, **без** `TELEGRAM_BOT_TOKEN`):

- `TELEGRAM_REDIS_URL`, `TELEGRAM_FORUM_CHAT_ID`
- `TELEGRAM_NOTIFICATIONS_THREAD_ID` — business/admin (outbox → Notifications)
- `TELEGRAM_INCIDENTS_THREAD_ID` — incidents и recovery
- `TELEGRAM_OUTBOUND_STREAM`, `TELEGRAM_OUTBOUND_ACK_STREAM`, `TELEGRAM_OUTBOUND_FAILED_STREAM`
- `TELEGRAM_OUTBOUND_FEEDBACK_CONSUMER_GROUP`, `TELEGRAM_OUTBOX_DELIVERING_TIMEOUT_MINUTES`

**`telegram-bot`** (единственный сервис с `TELEGRAM_BOT_TOKEN`):

- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OUTBOUND_ENABLED=true`
- те же `TELEGRAM_FORUM_CHAT_ID` и `TELEGRAM_*_THREAD_ID` (routing в forum)

Полный список — [`env.example`](env.example) § Telegram.

Sentry (`SENTRY_DSN`) — для traceback и grouping, не дублировать все exceptions в Telegram.

### Alertmanager (после MVP, опционально)

Если понадобится paging по **infra-level** правилам Prometheus (например readiness без срабатывания app incidents):

1. Настроить receivers в [alertmanager/alertmanager.yml](alertmanager/alertmanager.yml).
2. Добавить `alerting.alertmanagers` в [prometheus/prometheus.yml](prometheus/prometheus.yml).
3. Поднять профиль: `docker compose -f docker-compose.prod.yml --profile alerting up -d`.

Избегайте дублирования: payment/webhook/sync alerts уже идут из приложения в Telegram incidents. В Alertmanager имеет смысл оставить в основном readiness и infra-симптомы.

1. Создайте репозиторий образа на Docker Hub и access token.
2. В GitHub: секреты `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`.
3. Workflow [.github/workflows/ci-cd.yml](../.github/workflows/ci-cd.yml) при push в `test_express_pay_client.py` выполняет `ruff`, Django tests (PostgreSQL + Redis) и собирает/push образы `jendox/palingames:<sha>` и `jendox/palingames-bot:<sha>` (см. переменные в workflow).
4. На сервере задеployте тот же SHA:
   - **prod:** [`scripts/deploy_remote.sh`](#деплой-обновлений-prod-scriptsdeploy_remote-sh)
   - **staging:** [`scripts/deploy_remote_staging.sh`](#деплой-обновлений-staging-scriptsdeploy_remote_staging-sh) (без telegram-bot)

## Деплой обновлений (prod: `scripts/deploy_remote.sh`)

Скрипт для **обновления уже поднятого prod stack** после push образов в Docker Hub. Не заменяет первичный bootstrap (§ «Быстрый старт», createsuperuser, loaddata).

**Расположение:** `deploy/scripts/deploy_remote.sh` (рабочая директория после запуска — `deploy/`).

**Compose:** `docker-compose.prod.yml` + **`docker-compose.override.yml`** (обязателен на VPS с `/opt/proxy`; сервис `caddy` из базового compose не поднимается).

### Что делает скрипт (prod)

```text
1. PALINGAMES_WEB_REF / PALINGAMES_BOT_REF — из env или интерактивный prompt
2. Deploy plan + подтверждение Continue? [y/N] (только при TTY)
3. pull web, celery-worker, celery-beat, telegram-bot
4. migrate --noinput  (docker compose run --rm web — до переключения контейнеров)
5. up -d
6. /health/ready/ — retry до 60 с
7. setup_periodic_tasks (идемпотентно)
8. .deploy-state — CURRENT/PREVIOUS refs для следующего rollback
```

При failed health check (интерактивно): предложение rollback на предыдущие refs из `.deploy-state` → `exit 1` (даже если rollback восстановил сервис).

**Важно:** rollback откатывает **только Docker-образ**, не миграции БД. Держите миграции backward-compatible.

### Запуск (prod)

**Интерактивно (рекомендуется):**

```bash
ssh -t root@YOUR_VPS 'cd /opt/palingames-prod/deploy && export COMPOSE_PROJECT_NAME=palingames-prod && ./scripts/deploy_remote.sh'
```

Скрипт запросит web/bot image tags (git SHA из CI) и подтверждение деплоя.

**Non-interactive:**

```bash
cd /opt/palingames-prod/deploy
export COMPOSE_PROJECT_NAME=palingames-prod
export PALINGAMES_WEB_REF=jendox/palingames:<git-sha>
export PALINGAMES_BOT_REF=jendox/palingames-bot:<git-sha>
./scripts/deploy_remote.sh
```

Refs **не обязаны** лежать в `deploy/.env` — достаточно export перед запуском. На prod предпочитайте **git SHA**, не `latest`.

## Деплой обновлений (staging: `scripts/deploy_remote_staging.sh`)

Тот же flow, что prod, но **без telegram-bot** (на staging бот не запускается — один `TELEGRAM_BOT_TOKEN` только на prod).

```text
1. PALINGAMES_WEB_REF — из env или prompt (bot ref не нужен)
2. pull web, celery-worker, celery-beat
3. migrate → up -d postgres redis web celery-worker celery-beat  (явный список, без telegram-bot)
4. health → setup_periodic_tasks → .deploy-state (только web refs)
```

**Интерактивно:**

```bash
ssh -t root@YOUR_VPS 'cd /opt/palingames-staging/deploy && export COMPOSE_PROJECT_NAME=palingames-staging && ./scripts/deploy_remote_staging.sh'
```

**Non-interactive:**

```bash
cd /opt/palingames-staging/deploy
export COMPOSE_PROJECT_NAME=palingames-staging
export PALINGAMES_WEB_REF=jendox/palingames:<git-sha>
./scripts/deploy_remote_staging.sh
```

### Exit codes (оба скрипта)

| Код | Значение |
|-----|----------|
| `0` | Deploy OK, или отмена на «Continue?» |
| `1` | Ошибка pull/migrate/up/health; deploy новой версии не принят (rollback мог восстановить старую) |

### Файл состояния

`deploy/.deploy-state` (не в git; на staging и prod — **отдельные** файлы в разных каталогах):

**Prod:**

```env
PREVIOUS_WEB_REF=...
CURRENT_WEB_REF=...
PREVIOUS_BOT_REF=...
CURRENT_BOT_REF=...
DEPLOYED_AT=...
```

**Staging** (только web):

```env
PREVIOUS_WEB_REF=...
CURRENT_WEB_REF=...
DEPLOYED_AT=...
```

При первом успешном деплое `PREVIOUS_*` пустые — rollback недоступен до второго деплоя.

### Staging vs prod

| | Staging | Prod |
|---|---------|------|
| Скрипт | `deploy_remote_staging.sh` | `deploy_remote.sh` |
| Каталог | `/opt/palingames-staging/deploy` | `/opt/palingames-prod/deploy` |
| `COMPOSE_PROJECT_NAME` | `palingames-staging` | `palingames-prod` |
| `telegram-bot` | **не деплоится** | да |
| Override | `docker-compose.override.yml` → сеть `proxy` | + alias для `telegram-bot` |

Подробный VPS runbook: [`.cursor/plans/Dev and Prod deployment.md`](../.cursor/plans/Dev%20and%20Prod%20deployment.md) §13.

### Standalone VPS (без `/opt/proxy`)

Скрипты рассчитаны на override. Для standalone (единственный сайт, встроенный `caddy` из compose) используйте ручной flow из § «Быстрый старт» или добавьте локальный override без сети `proxy`.

## Образ приложения

Файл [Dockerfile](Dockerfile) рассчитан на контекст **корня репозитория**:

```bash
docker build -f deploy/Dockerfile -t youruser/palingames:mytag .
docker push youruser/palingames:mytag
```

Сборка выполняет `tailwind build`, `collectstatic` (Whitenoise manifest), CMD — **gunicorn**.

## Образ Telegram bot

Отдельный slim-образ на [Dockerfile.bot](Dockerfile.bot) — `bot/telegram_bot` (aiogram):

- **Outbound:** consumer Redis Streams (`telegram:outbound`) → sendMessage в forum topics; ack/failed → Django
- **Support:** webhook, inbound → Support thread, staff reply → личка клиенту (Redis mapping)

`web` / `celery-*` **не** вызывают Telegram Bot API; только `XADD` в outbound stream и чтение ack/failed (Celery beat).

```bash
# из корня репозитория
docker build -f deploy/Dockerfile.bot -t youruser/palingames-bot:mytag .
docker push youruser/palingames-bot:mytag
```

Или через Makefile: `make prod-bot-build` (тег `palingames-bot:local`).

### Prod compose

В `deploy/.env` (общий файл; см. split env ниже):

```env
PALINGAMES_BOT_REF=youruser/palingames-bot:mytag

# --- telegram-bot service ---
TELEGRAM_BOT_TOKEN=...
TELEGRAM_OUTBOUND_ENABLED=true
TELEGRAM_SUPPORT_ENABLED=true
TELEGRAM_REDIS_URL=redis://redis:6379/1
TELEGRAM_CONSUMER_GROUP=telegram-bot
TELEGRAM_WEBHOOK_BASE_URL=https://palingames.by          # staging: https://dev.palingames.by
TELEGRAM_WEBHOOK_SECRET_PATH=...      # длинный random path segment
TELEGRAM_WEBHOOK_SECRET_TOKEN=...       # optional, рекомендуется
TELEGRAM_WEBHOOK_DELETE_ON_SHUTDOWN=false

# --- web / celery (publish + ack; TELEGRAM_BOT_TOKEN не нужен) ---
TELEGRAM_FORUM_CHAT_ID=...
TELEGRAM_NOTIFICATIONS_THREAD_ID=...
TELEGRAM_INCIDENTS_THREAD_ID=...
TELEGRAM_SUPPORT_THREAD_ID=...
TELEGRAM_OUTBOUND_STREAM=telegram:outbound
TELEGRAM_OUTBOUND_ACK_STREAM=telegram:outbound:ack
TELEGRAM_OUTBOUND_FAILED_STREAM=telegram:outbound:failed
TELEGRAM_OUTBOUND_FEEDBACK_CONSUMER_GROUP=django-telegram-feedback
TELEGRAM_OUTBOX_DELIVERING_TIMEOUT_MINUTES=30
```

**Deploy (обновления):** [`scripts/deploy_remote.sh`](#деплой-обновлений-scriptsdeploy_remote-sh) — pull **оба** образа, `migrate` (до `up -d`), health check на `/health/ready/`, `setup_periodic_tasks`, `.deploy-state` для rollback.

Caddy проксирует `https://{domain}/telegram/webhook/*` → `telegram-bot:8080` (standalone: compose-`caddy`; VPS: `/opt/proxy/sites/palingames-prod.caddy` → alias `palingames-prod-telegram-bot:8080`).

Перед первым запуском bot: BotFather `/setprivacy` → **Disable**; бот — admin forum-группы.

Health check бота: `GET http://telegram-bot:8080/health` (внутри compose-сети).

Логи outbound: `telegram.outbound.delivered` / `telegram.outbound.failed`. Outbox Telegram: статус `DELIVERING` → `SENT` после ack.

**Standalone** (встроенный Caddy в compose):

```bash
cd deploy
docker compose -f docker-compose.prod.yml pull telegram-bot
docker compose -f docker-compose.prod.yml up -d telegram-bot caddy
```

**VPS с `/opt/proxy`** — только `telegram-bot` + override на сеть `proxy` (без `caddy`):

```bash
cd /opt/palingames-prod/deploy
COMPOSE="docker compose -f docker-compose.prod.yml -f docker-compose.override.yml"
$COMPOSE pull telegram-bot
$COMPOSE up -d telegram-bot
```

Логи: `docker compose -f docker-compose.prod.yml logs -f telegram-bot`

## CI/CD (Docker Hub)

В продакшене при `DEBUG=False` Django не раздаёт `MEDIA_URL` через `urls.py`. Файлы из админки должны храниться в **S3-совместимом хранилище** (как в типичной конфигурации проекта), а не на локальном volume контейнера `web`.

## Product preview images (`previews/*`)

Превью товаров (`ProductImage`) хранятся в том же bucket, что и download-файлы, но в отдельном prefix **`previews/`** с public-read только на этом prefix. Download-архивы (`ProductFile`, ключи `{slug}/{uuid}.ext`) остаются private и отдаются через presigned URL.

### Переменные окружения

В `deploy/.env`:

```env
S3_PRODUCT_IMAGES_ENABLED=true
S3_PRODUCT_IMAGES_PREFIX=previews
# Опционально, если public URL отличается от path-style endpoint/bucket:
# S3_PRODUCT_IMAGES_PUBLIC_BASE_URL=https://eu2.contabostorage.com/palingames.products
```

Остальные `S3_*` — как для download-файлов (`S3_ENDPOINT_URL`, credentials, `S3_BUCKET_NAME`, `S3_ADDRESSING_STYLE=path`).

Public URL превью (Contabo path-style):

```text
https://eu2.contabostorage.com/palingames.products/previews/{product-slug}/{uuid}.png
```

### Bucket policy (Contabo / S3-compatible)

Public read **только** для `previews/*`. Остальные объекты bucket — private.

Пример policy для bucket `palingames.products`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"AWS": ["*"]},
      "Action": ["s3:GetObject"],
      "Resource": ["arn:aws:s3:::palingames.products/previews/*"]
    }
  ]
}
```

Локально `make up-develop` поднимает MinIO и one-shot `minio-init`, который создаёт bucket `products` и включает anonymous read для `previews/*` (см. [README](../README.md) § MinIO).

### Миграция существующих превью

После deploy с `S3_PRODUCT_IMAGES_ENABLED=true` и применённых Django migrations:

```bash
cd deploy

# 1. Посмотреть план без изменений
docker compose -f docker-compose.prod.yml exec web \
  python manage.py migrate_product_images_to_s3 --dry-run

# 2. Перенести local/legacy keys -> previews/{slug}/{uuid}.ext
docker compose -f docker-compose.prod.yml exec web \
  python manage.py migrate_product_images_to_s3

# Опционально:
#   --product-slug=my-game
#   --limit=50
```

Команда idempotent: записи с `image.name`, уже начинающимся на `previews/`, пропускаются.

**Staging:** если старые файлы ещё лежат в `/app/media/products/...` внутри контейнера, запустите команду **до** следующего recreate контейнера без volume, иначе локальные файлы будут потеряны.

### Staging checklist (product images)

- [ ] Bucket policy: public read только на `{S3_PRODUCT_IMAGES_PREFIX}/*` (обычно `previews/*`)
- [ ] `deploy/.env`: `S3_PRODUCT_IMAGES_ENABLED=true`, S3 credentials, optional `S3_PRODUCT_IMAGES_PUBLIC_BASE_URL`
- [ ] Deploy через `scripts/deploy_remote.sh` (или migrate вручную после pull/up на bootstrap)
- [ ] `python manage.py migrate_product_images_to_s3 --dry-run`, затем без `--dry-run`
- [ ] Smoke: admin upload → URL `https://.../previews/...` открывается в браузере; catalog/product page показывает картинку

## Admin direct S3 upload (ProductFile + CustomGameFile)

Загрузка **больших** архивов (>100 MB) из Django admin **напрямую в object storage**, минуя Cloudflare body limit (~100 MB на proxied apex).

**Не меняет скачивание для покупателей:** кнопка «Скачать» по-прежнему идёт через `generate_presigned_download_url()` по `file_key` из БД. Уже загруженные файлы и покупки до включения фичи **остаются как есть** — меняется только **способ upload в admin**, не download flow.

### Переменные окружения

В `deploy/.env` (staging и prod — свои bucket):

```env
ADMIN_DIRECT_S3_UPLOAD_ENABLED=true
ADMIN_DIRECT_S3_UPLOAD_MAX_BYTES=524288000   # 500MB
ADMIN_DIRECT_S3_UPLOAD_PRESIGN_TTL_SECONDS=900
ADMIN_DIRECT_S3_UPLOAD_ALLOWED_EXTENSIONS=.zip,.pdf,.rar,.7z

S3_ENDPOINT_URL=https://eu2.contabostorage.com
S3_BUCKET_NAME=palingames.products          # staging: staging.palingames.products
S3_ADDRESSING_STYLE=path
```

После правки `.env`: `docker compose -f docker-compose.prod.yml up -d web`

При `ADMIN_DIRECT_S3_UPLOAD_ENABLED=false` admin снова использует server-side upload через Django (подходит для файлов <100 MB).

### CORS на bucket (Contabo, один раз на bucket)

В Contabo UI CORS нет — через AWS CLI (`PutBucketCors`). Браузер после presign делает cross-origin **PUT** на `eu2.contabostorage.com`; без CORS finalize не сработает.

| Окружение | Bucket | `AllowedOrigins` |
|-----------|--------|------------------|
| Staging | `staging.palingames.products` | `https://dev.palingames.by` |
| Production | `palingames.products` | `https://palingames.by` |

Пример `~/cors-prod.json`:

```json
{
  "CORSRules": [{
    "AllowedOrigins": ["https://palingames.by"],
    "AllowedMethods": ["PUT", "HEAD"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3000
  }]
}
```

```bash
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_DEFAULT_REGION=eu2

aws s3api put-bucket-cors \
  --bucket palingames.products \
  --cors-configuration file://cors-prod.json \
  --endpoint-url https://eu2.contabostorage.com

aws s3api get-bucket-cors \
  --bucket palingames.products \
  --endpoint-url https://eu2.contabostorage.com
```

Проверка: `get-bucket-cors` возвращает нужный origin. `curl OPTIONS` на корень бакета на Contabo **ненадёжен** — ориентироваться на browser smoke.

Подробный runbook (типичные ошибки, staging JSON): локальный план `.cursor/plans/Admin Direct S3 Upload-*.plan.md` §4.

### Admin flow (после deploy)

1. Staff → **ProductFile** или **CustomGameFile** → Add / Change
2. Выбрать продукт/заказ и файл — JS: presign → PUT в S3 (прогресс) → finalize → redirect
3. DevTools: `POST .../presign/` 200 → `PUT https://eu2.contabostorage.com/...` 200 → `POST .../finalize/` 200

API (staff + CSRF): `/admin-api/product-files/presign|finalize/`, `/admin-api/custom-game-files/presign|finalize/`

### Удаление файлов из S3 (admin delete)

При удалении строки **ProductFile** или **CustomGameFile** в admin (и при **CASCADE** при удалении **Product** / **CustomGameRequest**) Django вызывает `pre_delete`-сигнал: объект в S3 удаляется по `file_key` из этой строки (`delete_object`).

| Действие в admin | БД | S3 |
|------------------|----|----|
| Delete ProductFile | строка удалена | объект по `file_key` удалён |
| Delete Product | CASCADE → все ProductFile | каждый связанный объект удалён |
| Replace / upload нового active-файла | старый деактивирован или удалён | старый ключ удаляется в `save_model` / finalize (как раньше) |
| Delete CustomGameFile / CustomGameRequest | аналогично | аналогично |

**Скачивание для покупателей:**

- Доступ привязан к **продукту** (`UserProductAccess`), не к конкретному `file_key`.
- Кнопка «Скачать» берёт **текущий active** `ProductFile` продукта (`is_active=True`), не файл «на момент покупки».
- **Удалить весь Product** → скачивание недоступно (товара нет).
- **Удалить только старый файл и загрузить новый active** → скачивание **продолжит работать**; пользователи получат **новый** архив по новому `file_key`.
- **Удалить active-файл без замены** → 404 `active_file_not_found`, пока не появится новый active `ProductFile`.

**Защита и сбои:**

- Ключи с префиксом `previews/*` (превью-картинки) **не** удаляются этим механизмом.
- Если S3 недоступен при delete: строка в БД всё равно удаляется, в лог пишется WARNING (`*.row_delete.delete_failed`); объект может остаться в bucket до ручной чистки.
- **Orphan cleanup** (объекты в S3 без строки в БД после сорванного direct-upload) **не** включён — отдельная опциональная фаза.

Код: `apps/products/signals.py` (`pre_delete` на `ProductFile`, `CustomGameFile`).

### Staging / prod checklist

- [x] CORS на bucket (staging + prod, 2026-07-13)
- [x] `ADMIN_DIRECT_S3_UPLOAD_ENABLED=true` в staging и prod `.env`
- [x] Deploy образ с direct upload (`5854659` ProductFile, `0c962bd` CustomGameFile)
- [x] Staging smoke ProductFile >100 MB
- [x] Prod: фича включена; **старые купленные файлы скачиваются** (smoke на legacy access)
- [ ] Опционально: smoke CustomGameFile >100 MB на prod
- [ ] Sentry: нет всплеска `product_file.upload_url.failed` / finalize errors

**Bucket policy:** public write **не** добавлять. Upload только через presigned URL. Public read по-прежнему только `previews/*` (см. раздел выше).

## Backup и restore (MVP)

Политика хранится **вне приложения** — ниже минимальный чеклист для VPS + compose из этого каталога.

### PostgreSQL

Данные в volume `postgres_data`. Регулярный дамп (пример — ежедневно по cron на хосте):

```bash
cd deploy
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --format=custom \
  > "backups/palingames-$(date +%Y%m%d-%H%M).dump"
```

Restore в **новый** volume (остановите `web`/`worker`/`beat` на время):

```bash
docker compose -f docker-compose.prod.yml stop web celery-worker celery-beat
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists < backups/your.dump
docker compose -f docker-compose.prod.yml start web celery-worker celery-beat
```

Рекомендации:

- хранить дампы off-site (другой регион/облако), не только на том же VPS;
- периодически проверять restore на staging;
- перед major-миграциями — ручной snapshot.

### Redis

Volume `redis_data` (AOF). Для MVP достаточно пересоздания при потере (очереди Celery, кэш rate-limit, **Telegram outbound streams** — при flush сообщения в очереди теряются; support reply mapping тоже в Redis). Критичные данные — в Postgres.

### S3 (файлы продуктов и custom games)

Бэкап — на стороне провайдера: versioning, cross-region replication или периодический `sync`/`rclone` в второй bucket. В `.env` зафиксируйте bucket и ключи; восстановление = новый ключ доступа + те же объекты.

### Чеклист перед prod

- [ ] `setup_periodic_tasks` выполнен после первого `migrate` (в т.ч. Telegram feedback + reaper)
- [ ] SMTP: `EMAIL_HOST`, credentials, `DEFAULT_FROM_EMAIL`, `EMAIL_TIMEOUT=30`, `SERVER_EMAIL`
- [ ] DNS SPF/DKIM/DMARC для From-домена (см. `.cursor/plans/Email to Production.md`, фаза 1)
- [ ] `TELEGRAM_*`: forum + thread ids на web/celery; `TELEGRAM_BOT_TOKEN` + `TELEGRAM_OUTBOUND_ENABLED=true` на `telegram-bot` (см. «Алертинг», «Образ Telegram bot»)
- [ ] cron или внешний job для `pg_dump`
- [ ] off-site копии дампов (retention ≥ 7–30 дней)
- [ ] S3 versioning или второй bucket
- [ ] `S3_PRODUCT_IMAGES_ENABLED=true` и bucket policy для `previews/*` (см. раздел «Product preview images»)
- [ ] `ADMIN_DIRECT_S3_UPLOAD_ENABLED=true` + CORS на bucket (см. «Admin direct S3 upload»)
- [ ] документировано, кто и как делает restore
- [ ] `tags_fixture.json` загружен в БД (см. раздел «Справочник каталога»)
