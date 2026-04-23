# Palin Games

Интернет-магазин цифровых развивающих игр для детей на Django.

Проект уже покрывает полный базовый цикл:
- витрина и каталог;
- карточка товара и корзина;
- checkout и создание заказа;
- интеграция с Express Pay;
- выдача доступа к цифровым товарам после оплаты;
- хранение архивов игр в S3-compatible storage;
- скачивание купленных файлов для авторизованных пользователей;
- гостевой сценарий с email-ссылками на скачивание.
- operational incident alerts в Telegram с threshold/dedup/recovery semantics.

## Что реализовано

- server-rendered UI на Django templates;
- `htmx` для частичных обновлений каталога и корзины;
- каталог категорий и товаров;
- карточка товара, отзывы, preview из каталога;
- корзина для гостя и авторизованного пользователя;
- merge guest cart в пользовательскую корзину при логине;
- checkout на основе реальной корзины;
- кастомная модель пользователя и auth через `django-allauth headless`;
- создание заказов и order items;
- интеграция с Express Pay и webhook-обработка платежей;
- выдача постоянного `UserProductAccess` для оплаченных товаров авторизованным пользователям;
- выдача временного `GuestAccess` для guest checkout;
- backend download endpoints с проверкой доступа;
- приватное хранение product files в S3-compatible storage;
- загрузка файлов из Django Admin напрямую в S3;
- presigned download URLs для скачивания архивов;
- guest email outbox с шифрованным payload и отправкой через Celery;
- structured JSON logging.
- incident alerts для payment, fulfillment, notification delivery и storage failures.

## Архитектура

### Приложения

- `apps/users` — пользователи и интеграция с allauth.
- `apps/pages` — контентные страницы и личный кабинет.
- `apps/products` — категории, товары, изображения, файлы, каталог, product detail, S3 upload/download services.
- `apps/cart` — корзина для guest/user, merge, cart page.
- `apps/orders` — checkout, заказы и order items.
- `apps/payments` — инвойсы, webhook/payment processing, orchestration after successful payment.
- `apps/access` — доступ к оплаченным продуктам, guest download grants, guest email outbox.
- `apps/core` — logging, middleware, shared infra.

### Основные доменные сущности

- `Product` — товар.
- `ProductFile` — архив игры в приватном S3 storage.
- `Order` / `OrderItem` — заказ и состав заказа.
- `Invoice` / `PaymentEvent` — платежная часть.
- `UserProductAccess` — постоянный доступ авторизованного пользователя к продукту.
- `GuestAccess` — ограниченный по времени и количеству скачиваний доступ для guest order.
- `GuestAccessEmailOutbox` — зашифрованная очередь писем с guest download links.

## Как работает выдача файлов

### Авторизованный пользователь

После успешной оплаты создаётся `UserProductAccess`.

Скачать оплаченный архив можно из:
- каталога;
- страницы товара;
- раздела заказов в личном кабинете.

Все UI-точки ведут в backend endpoint, который:
- требует логин;
- проверяет `UserProductAccess`;
- проверяет наличие активного `ProductFile`;
- генерирует короткоживущую presigned URL;
- делает redirect на S3 / MinIO.

TTL для таких ссылок задаётся через `S3_PRESIGNED_EXPIRE_SECONDS`.

### Гостевой заказ

После успешной оплаты:
- для каждого товара создаётся `GuestAccess`;
- создаётся `GuestAccessEmailOutbox` с зашифрованным payload;
- Celery task отправляет письмо со списком ссылок.

Письмо содержит не S3 URL, а backend links вида:
- `/downloads/guest/<token>/`

При открытии такой ссылки backend:
- валидирует `GuestAccess`;
- проверяет срок действия и лимит скачиваний;
- ищет активный `ProductFile`;
- генерирует короткую presigned URL;
- увеличивает `downloads_count`;
- делает redirect на storage.

Параметры guest-доступа:
- срок действия: `GUEST_ACCESS_EXPIRE_HOURS`
- лимит скачиваний: `GUEST_ACCESS_MAX_DOWNLOADS`

## Хранение файлов

Product files хранятся в приватном S3-compatible object storage.

Текущий dev-ориентированный сценарий:
- локально используется `MinIO`;
- в production предполагается `Contabo Object Storage`.

Поддерживаемая схема ключей:
- bucket: `products`
- object key: `{product_slug}/{uuid4}.{ext}`

`ProductFile` хранит только метаданные и `file_key`, а не локальный файл.

### Что делает админка

В Django Admin:
- файл можно загрузить как `ProductFile`;
- файл уходит напрямую в S3;
- в БД сохраняются `file_key`, имя файла, MIME type, размер и checksum;
- на продукт допускается только один активный архив.

## Стек

- Python `3.13`
- Django `5.2`
- PostgreSQL `16`
- Redis
- Celery
- `django-celery-beat`
- Django Allauth (`headless`, `socialaccount`)
- Django REST Framework
- Tailwind CSS `v4`
- `htmx`
- Pydantic `v2`
- `boto3`
- Ruff
- `uv`

## Локальный запуск

### 1. Установить зависимости

```bash
uv sync
```

### 2. Поднять dev-инфраструктуру

```bash
make up-develop
```

Это поднимет:
- PostgreSQL на `5433`;
- Redis на `6379`;
- `smtp4dev` для просмотра писем.
- Prometheus на `9090`;
- Grafana на `3000`.

### 3. Подготовить `.env`

Скопировать `.env.example` в `.env` и скорректировать значения.

Ключевые переменные:
- `DATABASE_URL`
- `REDIS_URL`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `PAYMENTS_STATUS_SYNC_BATCH_SIZE`
- `PAYMENTS_STATUS_SYNC_MIN_INTERVAL_SECONDS`
- `S3_ENDPOINT_URL`
- `S3_ACCESS_KEY_ID`
- `S3_SECRET_ACCESS_KEY`
- `S3_BUCKET_NAME`
- `S3_PRESIGNED_EXPIRE_SECONDS`
- `SITE_BASE_URL`
- `GUEST_ACCESS_EXPIRE_HOURS`
- `GUEST_ACCESS_MAX_DOWNLOADS`
- `APP_DATA_ENCRYPTION_KEY`
- `SENTRY_DSN`
- `SENTRY_ENVIRONMENT`
- `SENTRY_RELEASE`
- `SENTRY_TRACES_SAMPLE_RATE`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_FORUM_CHAT_ID`
- `TELEGRAM_NOTIFICATIONS_THREAD_ID`
- `TELEGRAM_SUPPORT_THREAD_ID`
- `TELEGRAM_INCIDENTS_THREAD_ID`
- `INCIDENT_ALERT_DEDUPE_TTL_SECONDS`
- `PAYMENT_WEBHOOK_INCIDENT_THRESHOLD`
- `PAYMENT_WEBHOOK_INCIDENT_WINDOW_SECONDS`
- `PAYMENT_STATUS_SYNC_INCIDENT_THRESHOLD`
- `PAYMENT_STATUS_SYNC_INCIDENT_WINDOW_SECONDS`
- `DOWNLOAD_DELIVERY_INCIDENT_THRESHOLD`
- `DOWNLOAD_DELIVERY_INCIDENT_WINDOW_SECONDS`
- `NOTIFICATION_OUTBOX_INCIDENT_THRESHOLD`
- `NOTIFICATION_OUTBOX_INCIDENT_WINDOW_SECONDS`
- `STORAGE_INCIDENT_THRESHOLD`
- `STORAGE_INCIDENT_WINDOW_SECONDS`

`APP_DATA_ENCRYPTION_KEY` должен быть валидным `Fernet` key.

Для фоновой синхронизации статусов инвойсов с ограничением нагрузки на Express Pay:
- `PAYMENTS_STATUS_SYNC_BATCH_SIZE` — максимум инвойсов за один запуск задачи;
- `PAYMENTS_STATUS_SYNC_MIN_INTERVAL_SECONDS` — минимальный интервал между повторными проверками одного и того же `Invoice`.

Для базовой интеграции с Sentry:
- `SENTRY_DSN` — DSN проекта в Sentry;
- `SENTRY_ENVIRONMENT` — обычно `development`, `staging` или `production`;
- `SENTRY_RELEASE` — идентификатор релиза или git SHA;
- `SENTRY_TRACES_SAMPLE_RATE` — доля транзакций для performance tracing, например `0.0` или `0.1`.

Для Telegram incident alerts:
- `TELEGRAM_INCIDENTS_THREAD_ID` — отдельный forum topic для production incidents;
- `INCIDENT_ALERT_DEDUPE_TTL_SECONDS` — окно dedupe для повторных incident alerts;
- thresholds per family управляют, после скольких ошибок за окно алерт считается incident-worthy.

### 4. Применить миграции

```bash
uv run python manage.py migrate
```

### 5. Создать суперпользователя

```bash
uv run python manage.py createsuperuser
```

### 6. Загрузить fixture для тегов

```bash
uv run python manage.py loaddata tags_fixture.json
```

### 7. Запустить Django

```bash
uv run python manage.py runserver
```

### 8. Запустить Tailwind watcher

```bash
make tailwind
```

### 9. Запустить Celery worker

```bash
uv run celery -A config worker -l info
```

### 10. Запустить Celery beat

```bash
uv run celery -A config beat -l info
```

## Локальный запуск тестов

Для этого проекта основной режим проверки тестов должен быть на PostgreSQL, а не на SQLite.

Почему:
- production-стек использует PostgreSQL;
- часть поведения ORM, транзакций и блокировок отличается между SQLite и PostgreSQL;
- платежный контур и синхронизация инвойсов используют сценарии, где важна именно postgres-совместимая семантика.

Минимальный локальный сценарий:

1. Поднять dev-инфраструктуру

```bash
make up-develop
```

2. Убедиться, что `DATABASE_URL` в `.env` указывает на локальный PostgreSQL, например:

```env
DATABASE_URL=postgres://palingames_user:palingames_pass@localhost:5433/palingames_dev
```

3. Запустить тесты:

```bash
./.venv/bin/python manage.py test
```

Для проверки только платежного контура:

```bash
./.venv/bin/python manage.py test apps.payments
```

Если нужен только быстрый smoke-check без гарантии postgres-совместимого поведения, можно использовать временную SQLite-конфигурацию, но это не должно заменять основной прогон тестов на PostgreSQL перед merge или релизом.

## Observability

### Health endpoints

Для базовой operational-проверки доступны:

```text
/health/live/
/health/ready/
/metrics/
```

- `live` отвечает только за то, что Django-процесс жив;
- `ready` проверяет доступность PostgreSQL, Redis и S3-compatible storage.
- `metrics` отдаёт Prometheus-метрики приложения.

### Telegram Incident Alerts

В проекте Telegram-сигналы разделены на два класса:
- business/admin notifications;
- production incidents.

Incident alerts отправляются в отдельный topic через отдельный operational layer, а не через business `NotificationType`.

Текущие incident keys:
- `payments.webhook.failures`
- `payments.status_sync.failures`
- `downloads.delivery.failures`
- `notifications.outbox.failures`
- `storage.s3.unavailable`

Resolved alerts сейчас поддерживаются для:
- `payments.status_sync.failures`
- `downloads.delivery.failures`
- `notifications.outbox.failures`
- `storage.s3.unavailable`

Подробнее:
- [docs/observability.md](/home/jendox/PycharmProjects/palingames/docs/observability.md)
- [docs/runbooks.md](/home/jendox/PycharmProjects/palingames/docs/runbooks.md)

### Sentry

Sentry подключается опционально:
- если `SENTRY_DSN` пустой, приложение работает без Sentry;
- если `sentry-sdk` еще не установлен локально, приложение не падает, а просто не активирует интеграцию.

В Sentry scope автоматически передаются базовые поля observability-контекста:
- `request_id`
- `task_id`
- `task_name`
- `task_state`
- `http_method`
- `path`
- `status_code`

Это нужно для того, чтобы в ошибке или trace было проще понять:
- к какому HTTP-запросу она относится;
- из какой Celery-задачи пришла;
- на каком endpoint или шаге пайплайна произошел сбой.

### Prometheus metrics

Для реального экспорта метрик нужен пакет `prometheus-client`.

После изменений в зависимостях выполнить:

```bash
uv sync
```

После этого endpoint `/metrics/` начнет отдавать реальные Prometheus-метрики приложения.

### Локальный Prometheus/Grafana

Для dev-окружения monitoring stack уже включён в `docker-compose.develop.yml`.

После запуска:

- Prometheus: `http://127.0.0.1:9090`
- Grafana: `http://127.0.0.1:3000`
- Django metrics endpoint: `http://127.0.0.1:8000/metrics/`

Как это работает:
- Django запускается у тебя на хосте;
- контейнер Prometheus скрапит `http://host.docker.internal:8000/metrics/`;
- Grafana получает готовый datasource на локальный Prometheus через provisioning.

Что нужно сделать:

1. Поднять dev stack:

```bash
make up-develop
```

2. Запустить Django локально на `127.0.0.1:8000`

```bash
make runserver-observability
```

Это важно: Prometheus работает в контейнере и ходит на хост через `host.docker.internal:8000`. Поэтому для scrape в Linux dev-окружении Django должен слушать `0.0.0.0:8000`, а не только дефолтный loopback-интерфейс `127.0.0.1:8000`.

3. Проверить, что `http://127.0.0.1:8000/metrics/` отвечает

4. Открыть Grafana:

```text
http://127.0.0.1:3000
```

Стандартный логин Grafana по умолчанию:
- `admin`
- `admin`

При первом входе Grafana попросит сменить пароль.

Имеет ли смысл поднимать это локально:
- `Да`, если ты отлаживаешь observability, метрики, фоновые задачи, платежный поток или хочешь собрать первый dashboard.
- `Да`, если нужно быстро убедиться, что `/metrics/` реально экспортирует полезные сигналы.
- `Не обязательно`, если ты просто верстаешь шаблоны или меняешь неинфраструктурный код и тебе не нужны графики в моменте.

Практически:
- для обычной feature-разработки Prometheus/Grafana на локалке не обязательны;
- для платежей, Celery, readiness и operational work это вполне оправдано даже в dev.

Подробный operational-гайд по event taxonomy и alert rules:
- [docs/observability.md](/home/jendox/PycharmProjects/palingames/docs/observability.md)

Практические runbooks для типовых инцидентов:
- [docs/runbooks.md](/home/jendox/PycharmProjects/palingames/docs/runbooks.md)

План метрик и дешбордов:
- [docs/metrics.md](/home/jendox/PycharmProjects/palingames/docs/metrics.md)

Отдельная инструкция по локальному использованию Prometheus и Grafana:
- [docs/monitoring-local.md](/home/jendox/PycharmProjects/palingames/docs/monitoring-local.md)

Готовые PromQL-запросы и панели для первого локального dashboard:
- [docs/dashboard-local.md](/home/jendox/PycharmProjects/palingames/docs/dashboard-local.md)

## Настройка MinIO для разработки

В `.env` можно использовать, например:

```env
S3_ENDPOINT_URL=http://127.0.0.1:9000
S3_ACCESS_KEY_ID=minioadmin
S3_SECRET_ACCESS_KEY=minioadmin
S3_REGION_NAME=us-east-1
S3_BUCKET_NAME=products
S3_ADDRESSING_STYLE=path
S3_USE_SSL=False
```

Важно:
- bucket должен существовать;
- файлы должны храниться приватно;
- backend сам генерирует presigned URLs.

## Celery и периодические задачи

### Guest email outbox

Task отправки писем:

```text
apps.access.tasks.send_guest_access_email_outbox_task
```

В Celery broker уходит только `outbox_id`. Raw tokens остаются только внутри зашифрованного payload в БД.

### Cleanup старых outbox записей

Task очистки:

```text
apps.access.tasks.cleanup_guest_access_email_outbox_task
```

### Периодическая синхронизация статусов инвойсов

Task синхронизации:

```text
apps.payments.tasks.sync_waiting_invoice_statuses_task
```

Что делает задача:
- выбирает только `PENDING` инвойсы, связанные с заказами в статусах `CREATED` или `WAITING_FOR_PAYMENT`;
- не опрашивает один и тот же инвойс чаще, чем разрешено `PAYMENTS_STATUS_SYNC_MIN_INTERVAL_SECONDS`;
- ограничивает число запросов к Express Pay через `PAYMENTS_STATUS_SYNC_BATCH_SIZE`;
- обновляет локальные статусы заказа и инвойса тем же доменным кодом, что и webhook-обработка.

### Как добавить periodic tasks в Celery Beat через Django Admin

Предполагается, что `Celery worker` и `Celery beat` уже запущены.

В админке открыть:

```text
Periodic tasks -> Periodic tasks -> Add periodic task
```

Рекомендуемые задачи:

1. Очистка просроченных сессий

```text
Task (registered): apps.core.tasks.clear_expired_sessions_task
Schedule: crontab, например каждый день ночью
Enabled: yes
```

2. Очистка старых записей guest email outbox

```text
Task (registered): apps.access.tasks.cleanup_guest_access_email_outbox_task
Schedule: crontab, например каждый день ночью
Enabled: yes
```

3. Синхронизация статусов pending-инвойсов

```text
Task (registered): apps.payments.tasks.sync_waiting_invoice_statuses_task
Schedule: interval, например каждые 5 минут
Enabled: yes
```

Рекомендуемый стартовый профиль для production:
- `PAYMENTS_STATUS_SYNC_BATCH_SIZE=25`
- `PAYMENTS_STATUS_SYNC_MIN_INTERVAL_SECONDS=300`
- interval task: каждые 5 минут

Такой режим даёт верхнюю границу около 25 запросов к Express Pay за один запуск beat-задачи и не позволяет бесконечно перепроверять один и тот же инвойс.

Рекомендуется создать periodic task в Django Admin через `django-celery-beat`:
- task: `apps.access.tasks.cleanup_guest_access_email_outbox_task`
- `Crontab`:
  - `Minute`: `20`
  - `Hour`: `3`
  - `Day of week`: `*`
  - `Day of month`: `*`
  - `Month of year`: `*`

Retention настраивается через:
- `GUEST_ACCESS_EMAIL_OUTBOX_SENT_RETENTION_DAYS`
- `GUEST_ACCESS_EMAIL_OUTBOX_FAILED_RETENTION_DAYS`

Рекомендуемые значения по умолчанию:
- `SENT`: `30` дней
- `FAILED`: `90` дней

### Очистка устаревших сессий

Для guest cart и guest checkout имеет смысл также чистить просроченные Django sessions.

Task:

```text
apps.core.tasks.clear_expired_sessions_task
```

Это нормальная практика. Альтернатива — системный cron с `manage.py clearsessions`, но раз в проекте уже есть Celery Beat, удобнее держать housekeeping-задачи в одном месте.

Рекомендуемая periodic task в `django-celery-beat`:
- task: `apps.core.tasks.clear_expired_sessions_task`
- `Crontab`:
  - `Minute`: `40`
  - `Hour`: `3`
  - `Day of week`: `*`
  - `Day of month`: `*`
  - `Month of year`: `*`

### Рекомендуемый набор periodic tasks

Минимально стоит завести две задачи:
- `apps.access.tasks.cleanup_guest_access_email_outbox_task`
- `apps.core.tasks.clear_expired_sessions_task`

Рекомендуемый набор в админке `django-celery-beat`:

1. `Cleanup guest access email outbox`
- task: `apps.access.tasks.cleanup_guest_access_email_outbox_task`
- `03:20` ежедневно

2. `Clear expired Django sessions`
- task: `apps.core.tasks.clear_expired_sessions_task`
- `03:40` ежедневно

Обе задачи лучше разводить по времени на 10-30 минут, чтобы housekeeping не стартовал одновременно без необходимости.

## Полезные команды

### Линтинг

```bash
make lint
```

### Автоисправление

```bash
make fix
```

### Миграции

```bash
make makemigrations
make migrate
```

### Запуск тестов

Основной вариант:

```bash
uv run python manage.py test
```

Если в локальном окружении недоступен PostgreSQL test DB, для части тестов можно использовать временную SQLite-базу:

```bash
DATABASE_URL=sqlite:////tmp/palingames-test.sqlite3 uv run python manage.py test apps.access.tests apps.payments.tests
```

Это подходит для unit/integration тестов, которые не опираются на PostgreSQL-specific behaviour.

### Остановка dev-инфраструктуры

```bash
make down-develop
```

### Остановка с удалением volumes

```bash
make down-v
```

## Логирование

В проекте используется structured JSON logging.

Что есть сейчас:
- события пишутся в stdout;
- используется событийная модель логов;
- есть `request_id` для HTTP и Celery chains;
- чувствительные поля редактируются автоматически.

Примеры событий:
- `order.paid`
- `guest_access.granted`
- `guest_access.email.sent`
- `guest_access.email_outbox.created`
- `guest_access.email_outbox.sent`
- `guest_access.email_outbox.cleanup.completed`

Ключевые файлы:
- [apps/core/logging.py](/home/jendox/PycharmProjects/palingames/apps/core/logging.py)
- [apps/core/middleware.py](/home/jendox/PycharmProjects/palingames/apps/core/middleware.py)
- [apps/core/celery_logging.py](/home/jendox/PycharmProjects/palingames/apps/core/celery_logging.py)
