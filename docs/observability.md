# Observability

Этот документ задаёт operational contract проекта:
- какие события и сигналы считаются ключевыми;
- как разделены business notifications и incidents;
- какие alert families уже реализованы;
- как связаны logs, metrics, health checks, Telegram incidents и recovery alerts.

## 1. Observability Layers

В проекте используются четыре слоя:
- structured JSON logs;
- Sentry для traceback и exception grouping;
- health endpoints и Prometheus metrics;
- Telegram incident alerts для production issues.

Важно:
- не тащить все исключения из Sentry в Telegram;
- не смешивать business/admin notifications с incidents;
- алертить только повторяющиеся или реально impacting problems.

## 2. Global Principles

Нормальный operational flow должен позволять быстро ответить:
- что сломалось;
- где именно в пайплайне;
- влияет ли это на деньги или выдачу продукта;
- нужно ли ручное вмешательство;
- восстановилось ли уже поведение.

Отсюда правила:
- logs отвечают за подробный контекст;
- Sentry отвечает за traceback и grouping exceptions;
- metrics отвечают за тренды и массовость;
- Telegram incidents отвечают за внимание дежурного;
- recovery/resolved alerts отвечают за явное завершение инцидента.

## 3. Required Context Fields

Базовые поля контекста:
- `request_id`
- `task_id`
- `task_name`
- `task_state`
- `http_method`
- `path`
- `status_code`

Платежи и заказы:
- `order_id`
- `invoice_id`
- `provider_invoice_no`
- `provider_status`
- `payment_provider`

Fulfillment и notifications:
- `outbox_id`
- `product_id`
- `guest_access_id`
- `notification_type`
- `channel`

Managed links:
- `managed_link_id`
- `token_prefix` (не full token; path `/go/...` redacted в middleware)

Чувствительные поля должны оставаться redacted в logging layer.

## 4. Event Taxonomy

Имена событий должны быть:
- короткими;
- стабильными;
- в формате `domain.action.result`;
- без смешения нескольких смыслов в одном event name.

Request layer:
- `request.started`
- `request.finished`
- `request.failed`

Task layer:
- `task.started`
- `task.finished`

Orders:
- `order.creation.started`
- `order.creation.success`
- `order.creation.failed`
- `order.paid`
- `order_delivery_watchdog.completed` (поля: `checked_orders`, `checked_unpaid_orders`, `problems`, `alerts_sent`)

Invoices:
- `invoice.creation.started`
- `invoice.creation.success`
- `invoice.creation.retry_scheduled`
- `invoice.creation.failed`
- `invoice.creation.skipped`
- `invoice.status_sync.started`
- `invoice.status_sync.invoice_processed`
- `invoice.status_sync.invoice_failed`
- `invoice.status_sync.unknown_status`
- `invoice.status_sync.completed`

Payments:
- `payment.notification.received`
- `payment.notification.processed`
- `payment.notification.rejected`
- `payment.notification.failed`
- `payment.settlement_notification.received`
- `payment.settlement_notification.processed`
- `payment.settlement_notification.rejected`
- `payment.settlement_notification.failed`

Downloads and storage:
- `guest_access.download.redirected`
- `guest_access.download.rejected`
- `guest_access.download.failed`
- `product.download.redirected`
- `product.download.failed`
- `custom_game_request.download.redirected`
- `custom_game_request.download.failed`
- `product_file.download_url.generated`
- `product_file.download_url.failed`
- `managed_link.redirect.success`
- `managed_link.redirect.not_found`
- `managed_link.redirect.s3_failed`
- `managed_link.redirect.no_destination`
- `managed_link_storage.metadata.success`
- `managed_link_storage.metadata.failed`
- `managed_link.row_delete.deleted`
- `managed_link.row_delete.delete_failed`
- `managed_link.qr.failed`

Notifications:
- `notification.outbox.created`
- `notification.outbox.enqueued`
- `notification.outbox.task.started`
- `notification.outbox.skipped` (reason: `already_sent`, `awaiting_telegram_delivery`, `processing_in_progress`)
- `notification.outbox.processing.started`
- `notification.outbox.processing.recovered` (stale `PROCESSING` → retry)
- `notification.outbox.reconciled` (outbox → `SENT` по `EmailLog.SENT`)
- `notification.outbox.failed`
- `notification.outbox.sent`
- `notification.outbox.delivering` (Telegram)
- `notification.outbox.reaper.started` / `notification.outbox.reaper.completed` (поля: `reaped`, `processing_reaped`, `pending_reaped`, `timeout_minutes`)

Email delivery (`apps/emails/senders.py`):
- `email.send.sent`
- `email.send.failed`
- `email.send.suppressed`

Health and lifecycle:
- `app.started`
- `health.readiness.checked`

## 5. Severity Guide

`INFO`:
- нормальные state transitions;
- успешный request/task/payment/download/outbox send.

`WARNING`:
- аномалия без обязательного hard failure;
- rejected webhook;
- expired token;
- rate limit;
- degraded readiness.

`ERROR`:
- repeated failures;
- payment processing exceptions;
- invoice sync invoice failure;
- outbox send failure;
- presigned URL generation failure.

## 6. Incident Alerts Model

В проекте Telegram alerts разделены так:
- `notifications` topic: business/admin events;
- `incidents` topic: production issues.

Incident alerts реализуются отдельным transport layer через [`apps/core/alerts.py`](/home/jendox/PycharmProjects/palingames/apps/core/alerts.py), а не через business `NotificationType`.

Базовый API:
- `send_incident_alert(...)`
- `record_threshold_incident(...)`
- `resolve_threshold_incident(...)`
- `send_incident_recovery(...)`

Ключевые свойства:
- explicit `key`
- optional `fingerprint` для dedupe scope
- threshold-based alerting
- dedupe window
- active incident state
- explicit resolved alerts

## 7. Implemented Incident Families

Текущие incident keys:

1. `payments.webhook.failures`
Что считается incident:
- repeated webhook failures для critical reasons.

Route:
- [`apps/payments/alerts.py`](/home/jendox/PycharmProjects/palingames/apps/payments/alerts.py)
- [`apps/payments/views/express_pay.py`](/home/jendox/PycharmProjects/palingames/apps/payments/views/express_pay.py)

Recovery:
- пока не реализован.

2. `payments.status_sync.failures`
Что считается incident:
- repeated exceptions в fallback invoice status sync.

Route:
- [`apps/payments/alerts.py`](/home/jendox/PycharmProjects/palingames/apps/payments/alerts.py)
- [`apps/payments/tasks.py`](/home/jendox/PycharmProjects/palingames/apps/payments/tasks.py)

Recovery:
- `Invoice status sync recovered`

3. `downloads.delivery.failures`
Что считается incident:
- repeated failures выдачи user-facing download links.

Route:
- [`apps/products/alerts.py`](/home/jendox/PycharmProjects/palingames/apps/products/alerts.py)
- [`apps/access/views.py`](/home/jendox/PycharmProjects/palingames/apps/access/views.py)
- [`apps/products/views.py`](/home/jendox/PycharmProjects/palingames/apps/products/views.py)
- [`apps/custom_games/views.py`](/home/jendox/PycharmProjects/palingames/apps/custom_games/views.py)

Recovery:
- `Download delivery recovered`

4. `notifications.outbox.failures`
Что считается incident:
- repeated failures только для critical notification flows.

Critical flows:
- `guest_order_download`
- `custom_game_download`
- `invoice_created_user`
- `auth_account_email`

Route:
- [`apps/notifications/alerts.py`](/home/jendox/PycharmProjects/palingames/apps/notifications/alerts.py)
- [`apps/notifications/services.py`](/home/jendox/PycharmProjects/palingames/apps/notifications/services.py)

Recovery:
- `Critical notification outbox recovered`

5. `storage.s3.unavailable`
Что считается incident:
- repeated runtime failures в download URL generation path (product downloads, managed link redirects, admin presign).

Route:
- [`apps/products/alerts.py`](apps/products/alerts.py)
- [`apps/products/services/s3.py`](apps/products/services/s3.py)
- [`apps/managed_links/services/storage.py`](apps/managed_links/services/storage.py) — operations `managed_link_generate_presigned_download_url`, `managed_link_generate_presigned_upload_url`
- [`apps/managed_links/views.py`](apps/managed_links/views.py)

Recovery:
- `Storage recovered`

6. `payments.invoice_creation.missing`
Что считается incident:
- заказ до оплаты (`CREATED` / `WAITING_FOR_PAYMENT`) без полного инвойса (`provider_invoice_no`, `invoice_url`) дольше grace period.

Route:
- [`apps/payments/alerts.py`](/home/jendox/PycharmProjects/palingames/apps/payments/alerts.py)
- [`apps/orders/watchdog.py`](/home/jendox/PycharmProjects/palingames/apps/orders/watchdog.py)
- [`apps/orders/tasks.py`](/home/jendox/PycharmProjects/palingames/apps/orders/tasks.py)

Recovery:
- не применим (событие); восстановление вручную — пересоздание инвойса, см. runbooks §8.

Watchdog:
- тот же periodic task `check_paid_order_delivery_watchdog_task`, interval 5 минут;
- для pre-payment: grace 10 минут после `order.created_at`, lookback 48 часов;
- problem code `payment_invoice_missing`;
- immediate alert, dedupe `INCIDENT_ALERT_DEDUPE_TTL_SECONDS` (по умолчанию 15 мин).

7. `orders.delivery.invariant`
Что считается incident:
- нарушение инвариантов доставки **оплаченного** цифрового заказа: missing/mismatched invoice, missing access, missing/failed guest download email.

Route:
- [`apps/orders/alerts.py`](/home/jendox/PycharmProjects/palingames/apps/orders/alerts.py)
- [`apps/orders/watchdog.py`](/home/jendox/PycharmProjects/palingames/apps/orders/watchdog.py)
- [`apps/orders/tasks.py`](/home/jendox/PycharmProjects/palingames/apps/orders/tasks.py)

Recovery:
- пока не реализован.

Watchdog:
- periodic task `apps.orders.tasks.check_paid_order_delivery_watchdog_task`, interval 5 минут;
- для paid: grace 10 минут после `paid_at`, lookback 48 часов;
- immediate alert (без threshold), dedupe через fingerprint + `ORDER_DELIVERY_ALERT_DEDUPE_TTL_SECONDS`.

8. `payments.unmapped_provider_status`
Что считается incident:
- провайдер прислал статус инвойса, которого нет в `map_invoice_status`, поэтому состояние заказа
  намеренно не изменено (например `PAID_BY_CARD` при неподключённой оплате картой).

Route:
- [`apps/payments/alerts.py`](/home/jendox/PycharmProjects/palingames/apps/payments/alerts.py)
- [`apps/payments/services.py`](/home/jendox/PycharmProjects/palingames/apps/payments/services.py)

Тип:
- immediate alert (без threshold), dedupe по `provider` + `provider_status`.

Recovery:
- не применим: это событие, а не деградация. Разбирается вручную, см. runbooks.

9. `payments.order_refunded`
Что считается incident:
- по инвойсу оплаченного заказа пришёл возврат; заказ переведён в `REFUNDED`, доступ к файлам
  не отзывается автоматически.

Route:
- [`apps/payments/alerts.py`](/home/jendox/PycharmProjects/palingames/apps/payments/alerts.py)
- [`apps/payments/services.py`](/home/jendox/PycharmProjects/palingames/apps/payments/services.py)

Тип:
- immediate alert (без threshold), dedupe по `order_id`.

Recovery:
- не применим: возврат терминален и разбирается вручную.

## 8. What Must Not Go To Telegram Incidents

Не должны попадать в `incidents` topic:
- новые отзывы;
- новые заявки;
- admin/business notifications;
- единичный failed retry;
- validation errors;
- transient user-facing 4xx;
- все Sentry exceptions без фильтрации;
- все 500 подряд без threshold и taxonomy.

## 9. Thresholds And Dedupe

Текущая модель:
- threshold alerting через cache counters;
- incident dedupe через `key` или explicit `fingerprint`;
- active incident state для resolved alerts;
- dedupe TTL через `INCIDENT_ALERT_DEDUPE_TTL_SECONDS`;
- для `orders.delivery.invariant` — immediate alert с отдельным dedupe TTL `ORDER_DELIVERY_ALERT_DEDUPE_TTL_SECONDS` (по умолчанию 7 дней).

Настройки по incident families:
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
- `ORDER_DELIVERY_ALERT_DEDUPE_TTL_SECONDS`

## 10. Recovery/Resolved Semantics

Resolved alert должен отправляться только если:
- до этого уже был активный incident state;
- success path действительно подтверждает восстановление.

Это важно, чтобы не слать ложные `resolved` на обычный успешный запрос.

Текущие success signals:
- clean invoice status sync run без `failed`;
- успешная выдача download URL после предыдущих delivery failures;
- успешная отправка critical outbox notification;
- успешная генерация presigned URL после storage incident.

## 11. Relationship With Metrics And Prometheus

App-level incident alerts и Prometheus alerts дополняют друг друга.

App-level incidents лучше подходят для:
- payment/domain failures;
- download delivery issues;
- critical outbox problems;
- runtime storage errors.

Prometheus alerts лучше подходят для:
- readiness degradation;
- широкие инфраструктурные проблемы;
- массовые request/worker symptoms;
- dashboards и trends.

Managed links metrics (см. [metrics.md](metrics.md#d2-managed-links-qr-redirects)):
- `managed_link_redirect_total{source}`
- `managed_link_redirect_failed_total{reason}`
- `managed_link_qr_generated_total{qr_format,with_logo}`

Dedicated alert rule для managed links в `deploy/prometheus/alerts.yml` пока не добавлен — рекомендуется post-launch.

Актуальные alert rules лежат в [`monitoring/prometheus/alerts.yml`](../monitoring/prometheus/alerts.yml).

## 12. Sentry Usage

Sentry использовать для:
- traceback;
- exception grouping;
- быстрого поиска по `request_id`, `task_id`, `path`.

Sentry не использовать как прямой Telegram incident transport на все exceptions.

Правило:
- exception -> Sentry;
- trend/mass signal -> metrics/Prometheus;
- operator attention -> Telegram incident alert.

## 13. Next Useful Improvements

**MVP decision (2026-07-04):** paging через **Telegram app incidents**; Prometheus rules — для Grafana/trends; Alertmanager не подключаем до post-MVP (см. [deploy/README.md](../deploy/README.md)).

Логичные следующие шаги:
- добавить docs/runbooks под каждый новый incident key;
- решить отдельно recovery model для `payments.webhook.failures`;
- при необходимости добавить Alertmanager -> Telegram только для infra-level alerts;
- после накопления реального traffic откалибровать thresholds.
