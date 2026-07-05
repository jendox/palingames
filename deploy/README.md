# Production deploy (Docker + VPS)

Содержимое каталога: **образ приложения**, **prod docker-compose**, **Caddy**, **Prometheus/Grafana для сервера**, скрипты и пример переменных окружения.

Локальный dev-стек по-прежнему в корне: `docker-compose.develop.yml` и `monitoring/`.

## Быстрый старт на VPS

1. Установите Docker и Docker Compose plugin.
2. Склонируйте репозиторий (или скопируйте только каталог `deploy/` и при необходимости `docker-compose.prod.yml` + конфиги).
3. `cd deploy`
4. `cp env.example .env` и заполните секреты (в т.ч. `DJANGO_SECRET_KEY`, `APP_DATA_ENCRYPTION_KEY`, OAuth, `DATABASE_URL` с паролем).
5. Выставьте боевой домен: `CADDY_DOMAIN=shop.example.com` (для Let’s Encrypt не указывайте схему `https://`).
6. Поднимите стек:
   - **Сборка на сервере:**  
     `docker compose -f docker-compose.prod.yml up -d --build`
   - **Только образ с Docker Hub:** задайте `PALINGAMES_WEB_REF=youruser/palingames:tag` в `.env` или в окружении, затем  
     `docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d`
7. Миграции:  
   `docker compose -f docker-compose.prod.yml exec web python manage.py migrate --noinput`
8. Periodic tasks (один раз после migrate и при каждом деплое — идемпотентно):  
   `docker compose -f docker-compose.prod.yml exec web python manage.py setup_periodic_tasks`  
   Создаёт три задачи в `django-celery-beat`: очистка notification outbox (03:20), `clearsessions` (03:40), sync pending-инвойсов (каждые 5 мин). Расписание можно править в админке.
9. Суперпользователь (один раз):  
   `docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser`
10. Справочник каталога — см. раздел [«Справочник каталога (tags_fixture.json)»](#справочник-каталога-tags_fixturejson) (staging и prod, один раз после migrate).

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

- Приложение **не публикует порт 8000** наружу: с интернета идёт только трафик через **Caddy** (80/443).
- **Prometheus** скрапит `http://web:8000/metrics/` во внутренней сети compose.
- В **Caddyfile** путь `/metrics` отвечает **404** (дополнительная защита).
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

- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_FORUM_CHAT_ID`
- `TELEGRAM_NOTIFICATIONS_THREAD_ID` — business/admin события (outbox)
- `TELEGRAM_INCIDENTS_THREAD_ID` — production incidents и recovery

Sentry (`SENTRY_DSN`) — для traceback и grouping, не дублировать все exceptions в Telegram.

### Alertmanager (после MVP, опционально)

Если понадобится paging по **infra-level** правилам Prometheus (например readiness без срабатывания app incidents):

1. Настроить receivers в [alertmanager/alertmanager.yml](alertmanager/alertmanager.yml).
2. Добавить `alerting.alertmanagers` в [prometheus/prometheus.yml](prometheus/prometheus.yml).
3. Поднять профиль: `docker compose -f docker-compose.prod.yml --profile alerting up -d`.

Избегайте дублирования: payment/webhook/sync alerts уже идут из приложения в Telegram incidents. В Alertmanager имеет смысл оставить в основном readiness и infra-симптомы.

1. Создайте репозиторий образа на Docker Hub и access token.
2. В GitHub: секреты `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`.
3. Workflow [.github/workflows/ci-cd.yml](../.github/workflows/ci-cd.yml) при push в `main` выполняет `ruff`, Django tests (PostgreSQL + Redis) и собирает/push образ (см. переменные в workflow).
4. На сервере задайте тот же тег, выполните `scripts/deploy_remote.sh` или команды из него вручную.

## Образ приложения

Файл [Dockerfile](Dockerfile) рассчитан на контекст **корня репозитория**:

```bash
docker build -f deploy/Dockerfile -t youruser/palingames:mytag .
```

Сборка выполняет `tailwind build`, `collectstatic` (Whitenoise manifest), CMD — **gunicorn**.

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
- [ ] Deploy image + `python manage.py migrate --noinput`
- [ ] `python manage.py migrate_product_images_to_s3 --dry-run`, затем без `--dry-run`
- [ ] Smoke: admin upload → URL `https://.../previews/...` открывается в браузере; catalog/product page показывает картинку

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

Volume `redis_data` (AOF). Для MVP достаточно пересоздания при потере (очереди Celery, кэш rate-limit). Критичные данные — в Postgres.

### S3 (файлы продуктов и custom games)

Бэкап — на стороне провайдера: versioning, cross-region replication или периодический `sync`/`rclone` в второй bucket. В `.env` зафиксируйте bucket и ключи; восстановление = новый ключ доступа + те же объекты.

### Чеклист перед prod

- [ ] `setup_periodic_tasks` выполнен после первого `migrate`
- [ ] SMTP: `EMAIL_HOST`, credentials, `DEFAULT_FROM_EMAIL`, `EMAIL_TIMEOUT=30`, `SERVER_EMAIL`
- [ ] DNS SPF/DKIM/DMARC для From-домена (см. `.cursor/plans/Email to Production.md`, фаза 1)
- [ ] `TELEGRAM_*` для notifications + incidents threads (см. раздел «Алертинг»)
- [ ] cron или внешний job для `pg_dump`
- [ ] off-site копии дампов (retention ≥ 7–30 дней)
- [ ] S3 versioning или второй bucket
- [ ] `S3_PRODUCT_IMAGES_ENABLED=true` и bucket policy для `previews/*` (см. раздел «Product preview images»)
- [ ] документировано, кто и как делает restore
- [ ] `tags_fixture.json` загружен в БД (см. раздел «Справочник каталога»)
