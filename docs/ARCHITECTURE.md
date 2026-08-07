# PalinGames — архитектура

Краткая карта системы: как запрос пользователя превращается в заказ, оплату и выдачу цифрового файла.

Операционные детали (env, docker, runbooks) — в [README](../README.md) и [deploy/README.md](../deploy/README.md).

---

## Обзор

PalinGames — server-rendered Django-магазин цифровых товаров. Основной стек:

| Слой | Технологии |
|------|------------|
| Web | Django 5.2, templates, htmx, Tailwind |
| Данные | PostgreSQL 16, Redis |
| Фон | Celery + django-celery-beat |
| Файлы | S3-compatible storage (MinIO dev / Contabo prod) |
| Платежи | Express Pay (webhook + периодический sync) |
| Уведомления | `NotificationOutbox` → email (SMTP) / Telegram (Redis Streams → bot) |
| Наблюдаемость | JSON logging, Prometheus, Sentry, Telegram incidents |

```text
                         ┌─────────────┐
   Browser ────────────► │ Caddy/proxy │ ──► Django (web)
                         └─────────────┘         │
                                                 ├──► PostgreSQL
                                                 ├──► Redis (cache, Celery, rate limit, Telegram streams)
                                                 └──► S3 (product files, previews)

   Express Pay webhook ──► Django (web)
   Celery worker/beat ───► PostgreSQL, Redis, S3, SMTP
   telegram-bot ─────────► Redis Streams ──► Telegram Bot API
```

---

## Django-приложения

| Приложение | Ответственность |
|------------|-----------------|
| `apps/products` | Каталог, карточка, отзывы, `ProductFile`, S3 upload/download |
| `apps/cart` | Guest cart (session) и user cart (DB), merge при логине |
| `apps/orders` | Checkout, `Order` / `OrderItem`, промокоды |
| `apps/payments` | `Invoice`, webhook Express Pay, sync pending invoices |
| `apps/access` | `UserProductAccess`, `GuestAccess`, download endpoints |
| `apps/notifications` | `NotificationOutbox`, handlers, Celery delivery |
| `apps/emails` | `EmailLog`, `EmailSuppression`, единая точка SMTP |
| `apps/users` | Кастомный user, allauth headless, OAuth, consent |
| `apps/pages` | Контентные страницы, личный кабинет |
| `apps/custom_games` | Заказ индивидуальной игры (отдельный payable flow) |
| `apps/promocodes` | Промокоды и redemption |
| `apps/favorites` | Избранное |
| `apps/core` | Logging, health, metrics, incidents, SEO, analytics hooks |

Вне `apps/`: `libs/express_pay` — HTTP-клиент и модели Express Pay; `bot/telegram_bot` — отдельный сервис доставки в Telegram.

---

## Основные сущности

```text
Product ──► ProductFile (S3 key, один active archive на продукт)
Order ──► OrderItem(s)
Order ──► Invoice (Express Pay provider_invoice_no, status)
Invoice ──► PaymentEvent (idempotency / audit)

После оплаты:
  Authenticated ──► UserProductAccess (user + product, permanent)
  Guest         ──► GuestAccess (token hash, expiry, download limit)

NotificationOutbox ──► encrypted payload ──► Celery ──► email / Telegram stream
```

---

## Жизненный цикл заказа

```text
CREATED
   │
   ▼
WAITING_FOR_PAYMENT  ◄── create_invoice_task, invoice URL пользователю
   │
   ├──► PAID          ◄── webhook или sync_waiting_invoice_statuses_task
   ├──► CANCELED
   └──► FAILED
```

Статусы определены в `apps/orders/models.py` (`Order.OrderStatus`).

Checkout создаёт заказ и ставит invoice в очередь; fulfillment (`mark_order_paid`) срабатывает только при переходе invoice → `PAID`.

---

## Покупка: от корзины до файла

### 1. Корзина

- **Guest:** product IDs в session (`guest_cart_product_ids`), см. `apps/cart/services.py`.
- **User:** `Cart` / `CartItem` в PostgreSQL.
- **Merge:** signal `user_logged_in` → `merge_guest_cart_to_user()` в `apps/cart/signals.py` (пропускает уже купленные товары).

### 2. Checkout

Entry: `apps/orders/services.py` — создание `Order` из корзины, idempotency key в session, промокод, consent.

После создания заказа → `apps/payments/tasks.py::create_invoice_task` → Express Pay API → `Invoice` в статусе `PENDING`, заказ `WAITING_FOR_PAYMENT`.

### 3. Оплата (два канала)

**A. Webhook (основной путь)**

```text
POST /payments/express-pay/notification/
  → apps/payments/views/express_pay.py
  → verify signature, idempotent PaymentEvent
  → select_for_update на Order
  → mark_order_paid (если status PAID)
```

**B. Polling (fallback)**

```text
Celery Beat: sync_waiting_invoice_statuses_task (каждые ~5 мин)
  → apps/payments/tasks.py
  → Express Pay status API
  → тот же apply_order_status_from_invoice_status / mark_order_paid
```

Оба канала используют общий доменный код в `apps/payments/services.py`.

### 4. Fulfillment после оплаты

`mark_order_paid()` (`apps/payments/services.py`):

| checkout_type | Действие |
|---------------|----------|
| `AUTHENTICATED` | `grant_user_product_accesses(order)` — `UserProductAccess` |
| `GUEST` | `create_guest_access_notification_for_order(order)` — `GuestAccess` + email outbox |

Дополнительно (один раз на первый переход в PAID):

- `issue_order_reward_after_payment` — промокод за заказ
- GA4 / Yandex Metrica purchase events (`transaction.on_commit`)
- Custom game flow — отдельная ветка через `mark_custom_game_request_paid`

**Idempotency:** если `order.status == PAID`, повторный webhook логирует `order.paid.duplicate` и не создаёт повторный access.

### Sequence: guest checkout

```mermaid
sequenceDiagram
    participant U as User
    participant W as Django web
    participant EP as Express Pay
    participant DB as PostgreSQL
    participant C as Celery
    participant S3 as S3 storage
    participant M as SMTP

    U->>W: Checkout (guest email)
    W->>DB: Order WAITING_FOR_PAYMENT
    W->>EP: Create invoice
    U->>EP: Pay
    EP->>W: Webhook PAID
    W->>DB: mark_order_paid
    W->>DB: GuestAccess + NotificationOutbox
    W->>C: enqueue send_notification_outbox_task
    C->>M: Email with /downloads/guest/{token}/
    U->>W: GET guest download link
    W->>DB: validate token, increment downloads_count
    W->>S3: presigned URL
    W->>U: 302 redirect to file
```

### Sequence: authenticated checkout

```mermaid
sequenceDiagram
    participant U as User
    participant W as Django web
    participant EP as Express Pay
    participant DB as PostgreSQL
    participant S3 as S3 storage

    U->>W: Checkout (logged in)
    W->>DB: Order + Invoice
    U->>EP: Pay
    EP->>W: Webhook PAID
    W->>DB: UserProductAccess
    U->>W: GET /downloads/... (login required)
    W->>DB: check UserProductAccess
    W->>S3: presigned URL
    W->>U: 302 redirect
```

Presigned TTL задаётся `S3_PRESIGNED_EXPIRE_SECONDS`. Guest backend links живут дольше (expiry/limit на `GuestAccess`).

---

## NotificationOutbox

Единая очередь исходящих уведомлений (email и Telegram).

```text
Producer (orders, access, payments, auth, …)
  → NotificationOutbox (status=PENDING, encrypted payload)
  → Celery: send_notification_outbox_task(outbox_id)
  → NOTIFICATION_HANDLERS[type]
       ├── email → apps/emails/senders.py (единственный message.send())
       └── telegram → Redis Stream (не Bot API из web/celery)
```

- Payload шифруется `APP_DATA_ENCRYPTION_KEY` (Fernet).
- В Celery broker уходит только `outbox_id`, не guest tokens.
- Cleanup: `cleanup_notification_outbox_task` (Beat, 03:20).

Критичные типы (guest download, invoice, auth) при repeated failures → Telegram **incidents** (`apps/core/alerts.py`).

---

## Telegram: два класса сообщений

| Класс | Канал | Примеры |
|-------|-------|---------|
| Business / admin | `TELEGRAM_NOTIFICATIONS_THREAD_ID` | новый отзыв, custom game request |
| Production incidents | `TELEGRAM_INCIDENTS_THREAD_ID` | webhook failures, outbox failures, S3 down |

Доставка в Telegram:

```text
Django/Celery → Redis Stream (TELEGRAM_OUTBOUND_STREAM)
  → telegram-bot (bot/telegram_bot) — единственный holder TELEGRAM_BOT_TOKEN
  → Telegram Forum API
  → ACK stream / failed stream + feedback consumer (Beat)
```

---

## Хранение файлов

- **Download archives:** private, key `{product_slug}/{uuid}.{ext}`, presigned URL при скачивании.
- **Previews:** public prefix `previews/...` (CDN/object storage URL).
- **Collection covers:** public prefix `collections/...` (same bucket; see [product-collections.md](product-collections.md)).
- Admin upload: напрямую в S3, метаданные в `ProductFile`.

Readiness (`/health/ready/`) проверяет PostgreSQL, Redis и доступность S3.

---

## Failure modes и восстановление

| Симптом | Механизм | Incident key |
|---------|----------|--------------|
| Webhook не дошёл / timeout | `sync_waiting_invoice_statuses_task` | `payments.status_sync.failures` |
| Webhook signature / parse errors | reject + threshold alert | `payments.webhook.failures` |
| Duplicate webhook | `PaymentEvent` key + `already_paid` guard | metric `order_paid_duplicate` |
| Email не ушёл | outbox retry + retention cleanup | `notifications.outbox.failures` |
| S3 недоступен | download 5xx, ready degraded | `storage.s3.unavailable` |
| Guest link expired / limit | 410 на download view | — (expected) |

Resolved alerts поддерживаются для sync, downloads, outbox, storage (см. [runbooks.md](runbooks.md)).

---

## Health и метрики

| Endpoint | Назначение |
|----------|------------|
| `/health/live/` | Процесс жив (Docker healthcheck web) |
| `/health/ready/` | PG + Redis + S3 |
| `/metrics/` | Prometheus (внутренняя сеть / не через публичный Caddy) |

Ключевые метрики: `order_paid`, `payment_webhook_*`, `product_download_*`, `health_readiness_check`, outbox counters — см. [metrics.md](metrics.md).

---

## Entry points (код)

| Задача | Файл |
|--------|------|
| Checkout | `apps/orders/services.py`, `apps/orders/views.py` |
| Create invoice | `apps/payments/tasks.py::create_invoice_task` |
| Webhook | `apps/payments/views/express_pay.py` |
| Mark paid + fulfillment | `apps/payments/services.py::mark_order_paid` |
| Guest access | `apps/access/services.py` |
| Guest download | `apps/access/views.py::GuestProductDownloadView` |
| User download | `apps/products/views.py` (access check + presigned) |
| Outbox send | `apps/notifications/tasks.py`, `apps/notifications/services.py` |
| Cart merge | `apps/cart/signals.py`, `apps/cart/services.py::merge_guest_cart_to_user` |
| Periodic tasks | `apps/core/management/commands/setup_periodic_tasks.py` |
| Incidents | `apps/core/alerts.py` |
| Express Pay client | `libs/express_pay/client.py` |

---

## Deploy (кратко)

```text
GitHub Actions (main): ruff → Django tests → build/push Docker images (web + bot)
VPS staging: scripts/deploy_remote_staging.sh  (web + celery, no bot)
VPS prod:    scripts/deploy_remote.sh          (web + celery + telegram-bot)
       → pull → migrate (run --rm) → up -d → /health/ready/ → setup_periodic_tasks → .deploy-state
```

Prod/staging: `palingames.by` / `dev.palingames.by`, shared reverse proxy, Cloudflare origin firewall.

Подробный runbook: [deploy/README.md](../deploy/README.md#деплой-обновлений-scriptsdeploy_remote-sh).

---

## Связанные документы

- [README](../README.md) — локальный запуск, env, Celery
- [deploy/README.md](../deploy/README.md) — production compose, CI/CD, VPS
- [observability.md](observability.md) — logging, metrics, incidents
- [runbooks.md](runbooks.md) — типовые инциденты
- [metrics.md](metrics.md) — PromQL и dashboards
