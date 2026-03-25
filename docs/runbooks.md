# Runbooks

Этот документ нужен для типовых инцидентов, которые вероятнее всего встретятся в проекте.

Идея простая:
- сначала быстро понять масштаб проблемы;
- потом проверить самые вероятные причины;
- затем выполнить минимальные безопасные действия;
- и только после этого уже идти в глубокую диагностику.

## 1. Payment Webhook Not Processed

### Симптомы

- заказ оплачен у провайдера, но в системе остался `WAITING_FOR_PAYMENT`;
- не появился `UserProductAccess` или `GuestAccess`;
- в Sentry или логах растут:
  - `payment.notification.failed`
  - `payment.notification.rejected`
  - `payment.settlement_notification.failed`

### Что это обычно значит

Чаще всего одна из причин:
- webhook не дошёл до приложения;
- подпись webhook не прошла валидацию;
- локальный `Invoice` не найден;
- payload от провайдера не соответствует ожидаемой схеме;
- приложение приняло webhook, но упало внутри обработки.

### Проверить сначала

1. Есть ли входящий лог:
- `payment.notification.received`
- `payment.settlement_notification.received`

2. Есть ли сразу после него:
- `processed`
- `rejected`
- `failed`

3. Совпадают ли:
- `provider_invoice_no`
- `order.payment_account_no`
- `AccountNo` / `InvoiceNo` в payload

4. Не изменились ли:
- `EXPRESS_PAY_WEBHOOK_SECRET_WORD`
- `EXPRESS_PAY_USE_SIGNATURE`

### Быстрые действия

1. Найти заказ и инвойс в админке или БД.
2. Проверить, есть ли `PaymentEvent`.
3. Если webhook не дошёл, дождаться или инициировать fallback через periodic sync.
4. Если webhook rejected по подписи:
- проверить секрет;
- проверить raw payload shape;
- убедиться, что reverse proxy не портит тело запроса.

### Если нужен временный обход

- не менять статус заказа руками без понимания источника истины;
- сначала дождаться/запустить `sync_waiting_invoice_statuses_task`;
- если провайдер уже точно считает инвойс оплаченным, а sync подтверждает это, дальше разбираться, почему не сработал webhook.

### Что смотреть глубже

- Sentry event по webhook endpoint;
- payload webhook;
- `PaymentEvent` idempotency key;
- логи reverse proxy / ingress;
- recent deploy changes в `apps/payments/views/express_pay.py`.

## 2. Invoice Sync Failing Repeatedly

### Симптомы

- растёт `invoice.status_sync.invoice_failed`;
- pending-инвойсы не двигаются по статусам;
- fallback-проверка оплаты фактически не работает.

### Что это обычно значит

Чаще всего:
- Express Pay API недоступен;
- таймауты или сеть;
- блок по rate limit;
- невалидный `provider_invoice_no`;
- ошибка в коде sync-задачи.

### Проверить сначала

1. Есть ли `invoice.status_sync.started` и `invoice.status_sync.completed`.
2. Сколько в summary:
- `selected`
- `processed`
- `failed`
- `unknown`

3. Ошибки однотипные или разные:
- timeout
- auth/signature
- validation
- conversion `provider_invoice_no`

4. Не слишком ли агрессивные настройки:
- `PAYMENTS_STATUS_SYNC_BATCH_SIZE`
- `PAYMENTS_STATUS_SYNC_MIN_INTERVAL_SECONDS`

### Быстрые действия

1. Временно уменьшить нагрузку:
- снизить `PAYMENTS_STATUS_SYNC_BATCH_SIZE`
- увеличить `PAYMENTS_STATUS_SYNC_MIN_INTERVAL_SECONDS`

2. Проверить доступность Express Pay вручную.
3. Проверить несколько конкретных pending-инвойсов:
- есть ли `provider_invoice_no`;
- корректно ли он выглядит;
- не ушёл ли заказ уже в финальный статус локально.

### Если проблема массовая

- считать webhook основным источником обновления;
- sync оставить как degraded fallback;
- не отключать задачу полностью, пока не понятна причина;
- лучше уменьшить частоту и батч, чем просто выключить reconciliation.

### Что смотреть глубже

- Sentry exceptions из `apps.payments.tasks`;
- сетевые ограничения с сервера;
- ротацию токенов/секретов;
- наличие изменений в коде payment provider client.

## 3. Guest Email Outbox Failing

### Симптомы

- гостевой заказ оплачен, но письмо не пришло;
- в логах есть `guest_access.email_outbox.failed`;
- в БД записи outbox зависают в `FAILED`.

### Что это обычно значит

Чаще всего:
- SMTP недоступен;
- сломан шаблон письма;
- проблема в Celery worker;
- ошибка дешифрования payload;
- проблема в формировании ссылок/контекста письма.

### Проверить сначала

1. Есть ли событие `order.paid`.
2. Создался ли `GuestAccessEmailOutbox`.
3. Запустилась ли `send_guest_access_email_outbox_task`.
4. Что в `last_error` у outbox-записи.

### Быстрые действия

1. Проверить SMTP:
- доступность сервера;
- корректность `EMAIL_HOST`, `EMAIL_PORT`, `DEFAULT_FROM_EMAIL`

2. Проверить Celery worker:
- жив ли процесс;
- не застряли ли задачи;
- нет ли массовых task failures.

3. Проверить payload:
- можно ли его расшифровать;
- не сломан ли `APP_DATA_ENCRYPTION_KEY`.

### Безопасный workaround

- не создавать новые guest token вручную без причины;
- сначала убедиться, что `GuestAccess` уже выданы;
- если доступы уже есть, можно переотправить письмо через outbox processing после исправления причины.

### Что смотреть глубже

- Sentry event из email sending path;
- шаблоны `access/email/*`;
- `SITE_BASE_URL`;
- корректность image/download URLs.

## 4. Readiness Endpoint Returns 503

### Симптомы

- `/health/ready/` отвечает `503`;
- балансировщик может начать исключать инстанс из трафика;
- приложение “как будто живое”, но часть функциональности недоступна.

### Что это обычно значит

Одна из обязательных зависимостей недоступна:
- PostgreSQL;
- Redis;
- S3-compatible storage.

### Проверить сначала

1. JSON body `/health/ready/`
2. Какой именно check упал:
- `database`
- `redis`
- `s3`

3. Это один инстанс или все сразу.

### Быстрые действия

#### Если упала database

- проверить доступность PostgreSQL;
- проверить `DATABASE_URL`;
- проверить, не исчерпаны ли соединения.

#### Если упал redis

- проверить Redis process/container;
- проверить `REDIS_URL`;
- проверить сетевую доступность.

#### Если упал s3

- проверить endpoint и bucket;
- проверить ключи доступа;
- проверить, не истёкли ли credentials;
- проверить доступность object storage provider.

### Что не делать

- не переводить readiness в `200` руками;
- не отключать проверку только ради “чтобы зеленело”;
- не перезапускать всё подряд, пока не ясно, какая зависимость реально сломана.

### Что смотреть глубже

- был ли недавний deploy;
- менялись ли env vars;
- есть ли одновременный рост ошибок в `product_file.*`, payment sync или Celery.

## 5. Как пользоваться runbooks

Правильный порядок реакции на инцидент:

1. Определи симптом.
2. Найди подходящий runbook.
3. Пройди разделы:
- симптомы
- быстрые проверки
- быстрые действия

4. Зафиксируй:
- время начала;
- affected scope;
- root cause hypothesis;
- что сделал;
- результат.

5. После инцидента обнови runbook, если он оказался неполным.

## 6. Когда писать новый runbook

Добавляй новый runbook, если одновременно выполняются два условия:
- инцидент уже случался или очень вероятен;
- без готового чек-листа на его разбор уходит слишком много времени.
