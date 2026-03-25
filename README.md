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

### 3. Подготовить `.env`

Скопировать `.env.example` в `.env` и скорректировать значения.

Ключевые переменные:
- `DATABASE_URL`
- `REDIS_URL`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `S3_ENDPOINT_URL`
- `S3_ACCESS_KEY_ID`
- `S3_SECRET_ACCESS_KEY`
- `S3_BUCKET_NAME`
- `S3_PRESIGNED_EXPIRE_SECONDS`
- `SITE_BASE_URL`
- `GUEST_ACCESS_EXPIRE_HOURS`
- `GUEST_ACCESS_MAX_DOWNLOADS`
- `APP_DATA_ENCRYPTION_KEY`

`APP_DATA_ENCRYPTION_KEY` должен быть валидным `Fernet` key.

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

Рекомендуется создать periodic task в Django Admin через `django-celery-beat`:
- task: `apps.access.tasks.cleanup_guest_access_email_outbox_task`
- schedule: `daily` или `nightly`

Retention настраивается через:
- `GUEST_ACCESS_EMAIL_OUTBOX_SENT_RETENTION_DAYS`
- `GUEST_ACCESS_EMAIL_OUTBOX_FAILED_RETENTION_DAYS`

Рекомендуемые значения по умолчанию:
- `SENT`: `30` дней
- `FAILED`: `90` дней

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
