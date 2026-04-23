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

Invoices:
- `invoice.creation.started`
- `invoice.creation.success`
- `invoice.creation.failed`
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

Notifications:
- `notification.outbox.created`
- `notification.outbox.enqueued`
- `notification.outbox.processing.started`
- `notification.outbox.failed`
- `notification.outbox.sent`

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

Route:
- [`apps/notifications/alerts.py`](/home/jendox/PycharmProjects/palingames/apps/notifications/alerts.py)
- [`apps/notifications/services.py`](/home/jendox/PycharmProjects/palingames/apps/notifications/services.py)

Recovery:
- `Critical notification outbox recovered`

5. `storage.s3.unavailable`
Что считается incident:
- repeated runtime failures в download URL generation path.

Route:
- [`apps/products/alerts.py`](/home/jendox/PycharmProjects/palingames/apps/products/alerts.py)
- [`apps/products/services/s3.py`](/home/jendox/PycharmProjects/palingames/apps/products/services/s3.py)

Recovery:
- `Storage recovered`

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
- dedupe TTL через `INCIDENT_ALERT_DEDUPE_TTL_SECONDS`.

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

Актуальные alert rules лежат в [`monitoring/prometheus/alerts.yml`](/home/jendox/PycharmProjects/palingames/monitoring/prometheus/alerts.yml).

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

Логичные следующие шаги:
- добавить docs/runbooks под каждый новый incident key;
- решить отдельно recovery model для `payments.webhook.failures`;
- при необходимости добавить Alertmanager -> Telegram только для infra-level alerts;
- после накопления реального traffic откалибровать thresholds.
