# Observability

Этот документ задаёт минимальный operational-контракт проекта:
- какие события считаются ключевыми;
- какие поля в них должны быть;
- какие сигналы стоит алертить в первую очередь.

## 1. Базовые принципы

Observability в этом проекте строится на трёх слоях:
- structured JSON logs;
- Sentry для ошибок и traceback;
- health endpoints для liveness/readiness.

Цель не в том, чтобы логировать всё подряд, а в том, чтобы по инциденту быстро ответить на вопросы:
- что сломалось;
- на каком шаге пайплайна;
- с каким заказом, инвойсом, задачей или запросом это связано;
- требуется ли ручное вмешательство.

## 2. Обязательные поля контекста

Ниже поля, которые считаются опорными для диагностики.

### Глобальные

- `request_id`
- `task_id`
- `task_name`
- `task_state`
- `http_method`
- `path`
- `status_code`
- `source`

### Платежи и заказы

- `order_id`
- `order_public_id`
- `invoice_id`
- `provider_invoice_no`
- `provider_status`
- `checkout_type`
- `payment_provider`

### Доступы и письма

- `outbox_id`
- `guest_access_id`
- `product_id`
- `email`

Чувствительные поля должны оставаться редактированными через текущий logging layer.

## 3. Event Taxonomy

Имена событий должны быть:
- короткими;
- стабильными;
- в формате `domain.action.result`;
- без смешения разных смыслов в одном событии.

### Request layer

- `request.started`
- `request.finished`
- `request.failed`

### Task layer

- `task.started`
- `task.finished`

### Orders

- `order.creation.started`
- `order.creation.success`
- `order.creation.failed`
- `order.paid`

### Invoices

- `invoice.creation.enqueued`
- `invoice.creation.started`
- `invoice.creation.success`
- `invoice.creation.failed`
- `invoice.creation.skipped`
- `invoice.status_sync.started`
- `invoice.status_sync.invoice_processed`
- `invoice.status_sync.invoice_failed`
- `invoice.status_sync.unknown_status`
- `invoice.status_sync.completed`

### Payments

- `payment.notification.received`
- `payment.notification.processed`
- `payment.notification.rejected`
- `payment.notification.failed`
- `payment.notification.ignored`
- `payment.settlement_notification.received`
- `payment.settlement_notification.processed`
- `payment.settlement_notification.rejected`
- `payment.settlement_notification.failed`
- `payment.settlement_notification.ignored`

### Guest access / email

- `guest_access.granted`
- `guest_access.download.redirected`
- `guest_access.download.rejected`
- `guest_access.download.failed`
- `guest_access.download.unavailable`
- `guest_access.email.sent`
- `guest_access.email_outbox.created`
- `guest_access.email_outbox.sent`
- `guest_access.email_outbox.failed`
- `guest_access.email_outbox.skipped`
- `guest_access.email_outbox.cleanup.started`
- `guest_access.email_outbox.cleanup.completed`

### Product storage

- `product_storage.client.created`
- `product_storage.bucket.validated`
- `product_storage.bucket.validation_failed`
- `product_file.upload.started`
- `product_file.upload.success`
- `product_file.upload.failed`
- `product_file.delete.success`
- `product_file.delete.failed`
- `product_file.metadata.success`
- `product_file.metadata.failed`
- `product_file.download_url.generated`
- `product_file.download_url.failed`

### Health / lifecycle

- `app.started`
- `health.readiness.checked`

## 4. Severity Guide

### `INFO`

Использовать для нормальных переходов состояния:
- старт/финиш запроса;
- создание заказа;
- создание инвойса;
- успешная оплата;
- успешная фонова задача;
- успешная выдача доступа.

### `WARNING`

Использовать, когда бизнес-поток не обязательно упал, но есть аномалия:
- некорректный webhook payload;
- неверная подпись;
- пропущенный неизвестный статус;
- попытка использовать истёкший guest token;
- readiness в degraded-состоянии.

### `ERROR`

Использовать, когда требуется диагностика и возможно ручное вмешательство:
- провал invoice sync на конкретном инвойсе;
- исключение при webhook processing;
- провал отправки письма;
- провал генерации presigned URL;
- недоступность S3/Redis/DB для readiness.

## 5. Mandatory Alerts

Ниже минимальный набор алертов, который стоит настроить первым.

### A. Payment webhook failures

Триггер:
- repeated `payment.notification.failed`
- repeated `payment.notification.rejected`

Почему важно:
- можно перестать получать подтверждения оплаты;
- пользователь оплатит заказ, а система не выдаст доступ автоматически.

Что смотреть:
- подпись;
- входной payload;
- существует ли локальный `Invoice`;
- не сломался ли routing или CSRF bypass для webhook endpoint.

### B. Invoice sync failures

Триггер:
- рост `invoice.status_sync.invoice_failed` выше порога за 10-15 минут

Почему важно:
- fallback-механизм reconciliation перестаёт работать;
- система становится зависимой только от webhook.

Что смотреть:
- доступность Express Pay API;
- лимиты запросов;
- корректность `provider_invoice_no`;
- таймауты и сетевые ошибки.

### C. Guest email outbox failures

Триггер:
- repeated `guest_access.email_outbox.failed`

Почему важно:
- гость оплатил заказ, но не получил ссылки на скачивание.

Что смотреть:
- SMTP;
- шифрование payload;
- ошибки шаблона письма;
- Celery worker.

### D. Product download failures

Триггер:
- `product_file.download_url.failed`
- repeated `guest_access.download.failed`

Почему важно:
- оплата прошла, но файл фактически нельзя скачать.

Что смотреть:
- S3 credentials;
- bucket availability;
- presigned URL generation;
- наличие активного `ProductFile`.

### E. Readiness degraded

Триггер:
- `/health/ready/` не `200` дольше 2-5 минут

Почему важно:
- приложение живо, но непригодно для реального трафика.

Что смотреть:
- DB connection;
- Redis availability;
- S3 availability;
- недавние deploy changes.

## 6. Recommended Thresholds

Стартовые пороги без тонкой настройки:

- `payment.notification.failed OR rejected` >= 5 за 10 минут
- `invoice.status_sync.invoice_failed` >= 5 за 15 минут
- `guest_access.email_outbox.failed` >= 3 за 15 минут
- `product_file.download_url.failed` >= 3 за 15 минут
- `/health/ready/ != 200` дольше 2 минут

Это не идеальные значения, а безопасная стартовая точка. После пары недель реального трафика их лучше откалибровать по фактическому noise level.

## 7. Как использовать Sentry

Sentry лучше использовать для:
- необработанных исключений;
- группировки traceback;
- быстрого поиска по `request_id`, `task_id`, `path`.

Sentry хуже подходит для:
- подсчёта бизнес-метрик;
- SLA/SLO дешбордов;
- readiness polling.

То есть:
- exception -> Sentry;
- бизнес-сигнал и состояние системы -> logs/monitoring/health checks.

## 8. Следующий слой после MVP

После текущего этапа логичный следующий шаг:
- добавить metrics backend (`Prometheus` или аналог);
- построить dashboard:
  - orders created / paid;
  - pending invoices;
  - invoice sync failures;
  - guest email failures;
  - download failures;
- завести runbooks на 3-5 типовых инцидентов.
