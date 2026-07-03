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
- [ ] `TELEGRAM_*` для notifications + incidents threads (см. раздел «Алертинг»)
- [ ] cron или внешний job для `pg_dump`
- [ ] off-site копии дампов (retention ≥ 7–30 дней)
- [ ] S3 versioning или второй bucket
- [ ] документировано, кто и как делает restore
