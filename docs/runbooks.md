# Runbooks

Этот документ описывает типовые production-инциденты и порядок реакции на них.

Принцип:
- сначала быстро подтвердить масштаб;
- потом проверить наиболее вероятную причину;
- затем выполнить минимальные безопасные действия;
- и только после этого идти в глубокую диагностику.

## 1. Incident Alerts Contract

В проекте уже разделены два класса Telegram-сигналов:
- `notifications` — business/admin events;
- `incidents` — только production issues, требующие внимания.

Incident alerts отправляются через отдельный operational layer, а не через business `NotificationType`.

Текущие incident keys:
- `payments.webhook.failures`
- `payments.status_sync.failures`
- `downloads.delivery.failures`
- `notifications.outbox.failures`
- `storage.s3.unavailable`

Recovery/resolved alerts сейчас реализованы для:
- `payments.status_sync.failures`
- `downloads.delivery.failures`
- `notifications.outbox.failures`
- `storage.s3.unavailable`

`payments.webhook.failures` пока без recovery-сигнала, потому что в success path нет надёжного reason-scoped подтверждения восстановления.

## 2. Payment Webhook Failures

Incident key:
- `payments.webhook.failures`

Симптомы:
- provider считает заказ оплаченным, но локальный `Order` или `Invoice` остался в `WAITING_FOR_PAYMENT` / `PENDING`;
- в Telegram topic `Incidents` пришёл alert `Repeated payment webhook failures`;
- в логах растут:
  - `payment.notification.failed`
  - `payment.settlement_notification.failed`

Что это обычно значит:
- webhook не дошёл до приложения;
- локальный `Invoice` не найден;
- payload обработан, но код упал внутри;
- provider присылает unexpected payload shape.

Проверить сначала:
1. Есть ли входящий лог `payment.notification.received` или `payment.settlement_notification.received`.
2. Есть ли после него `processed`, `rejected` или `failed`.
3. Совпадают ли `provider_invoice_no`, `InvoiceNo`, `AccountNo`, локальный `order.payment_account_no`.
4. Не менялись ли `EXPRESS_PAY_WEBHOOK_SECRET_WORD` и routing до webhook endpoint.

Быстрые действия:
1. Найти заказ и инвойс в админке или БД.
2. Проверить, создался ли `PaymentEvent`.
3. Если webhook не дошёл, дождаться или вручную инициировать fallback через `sync_waiting_invoice_statuses_task`.
4. Если проблема массовая, не менять статусы заказов руками до подтверждения провайдера.

Что смотреть глубже:
- Sentry events по webhook endpoint;
- payload webhook;
- последние изменения в [`apps/payments/views/express_pay.py`](/home/jendox/PycharmProjects/palingames/apps/payments/views/express_pay.py);
- network/reverse proxy logs.

## 3. Invoice Status Sync Failures

Incident key:
- `payments.status_sync.failures`

Recovery title:
- `Invoice status sync recovered`

Симптомы:
- repeated alert `Repeated invoice status sync failures`;
- pending-инвойсы не двигаются по статусам;
- fallback reconciliation перестаёт работать.

Что это обычно значит:
- Express Pay API недоступен;
- сеть или timeout;
- невалидный `provider_invoice_no`;
- ошибка в коде sync task.

Проверить сначала:
1. Есть ли `invoice.status_sync.started` и `invoice.status_sync.completed`.
2. Что в summary:
  - `selected`
  - `processed`
  - `failed`
  - `unknown`
3. Ошибка однотипная или плавающая.
4. Не слишком ли агрессивны `PAYMENTS_STATUS_SYNC_BATCH_SIZE` и `PAYMENTS_STATUS_SYNC_MIN_INTERVAL_SECONDS`.

Быстрые действия:
1. Проверить доступность Express Pay вручную.
2. Проверить несколько конкретных `PENDING` инвойсов.
3. Если проблема массовая, уменьшить batch или частоту sync, а не отключать задачу полностью.

Когда считать проблему закрытой:
- есть `resolved` alert `Invoice status sync recovered`;
- новые прогоны sync завершаются без `failed`;
- pending-инвойсы снова переходят в финальные статусы.

Что смотреть глубже:
- исключения из [`apps/payments/tasks.py`](/home/jendox/PycharmProjects/palingames/apps/payments/tasks.py);
- provider client;
- сетевые ограничения хоста.

## 4. Download Delivery Failures

Incident key:
- `downloads.delivery.failures`

Recovery title:
- `Download delivery recovered`

Симптомы:
- пользователь оплатил товар, но не может скачать файл;
- alert `Repeated download delivery failures`;
- в логах repeated:
  - `guest_access.download.failed`
  - `product.download.failed`
  - `custom_game_request.download.failed`

Что это обычно значит:
- storage временно недоступен;
- presigned URL generation падает;
- runtime path выдачи файла сломан;
- активный файл существует не для всех товаров.

Проверить сначала:
1. Какой `delivery_type` в alert details:
  - `guest_product`
  - `product`
  - `custom_game`
2. Какой `reason` указан.
3. Есть ли одновременно ошибки `product_file.download_url.failed`.
4. Есть ли активный файл у проблемного продукта/request.

Быстрые действия:
1. Проверить storage/presigned URL path.
2. Проверить последние ошибки в [`apps/products/services/s3.py`](/home/jendox/PycharmProjects/palingames/apps/products/services/s3.py).
3. Проверить, что у товара есть активный `ProductFile`, а у custom game есть `CustomGameFile`.

Когда считать проблему закрытой:
- пользователи снова получают redirect/download URL без 503;
- пришёл `resolved` alert `Download delivery recovered`.

Что смотреть глубже:
- [`apps/access/views.py`](/home/jendox/PycharmProjects/palingames/apps/access/views.py)
- [`apps/products/views.py`](/home/jendox/PycharmProjects/palingames/apps/products/views.py)
- [`apps/custom_games/views.py`](/home/jendox/PycharmProjects/palingames/apps/custom_games/views.py)

## 5. Critical Notification Outbox Failures

Incident key:
- `notifications.outbox.failures`

Recovery title:
- `Critical notification outbox recovered`

Сейчас alerting включён только для critical notification flows:
- `guest_order_download`
- `custom_game_download`

Симптомы:
- пользователь не получил критичное письмо со ссылкой на скачивание;
- в Telegram пришёл alert `Repeated critical notification outbox failures`;
- в БД outbox-записи остаются в `FAILED`.

Что это обычно значит:
- SMTP/transport недоступен;
- ошибка в payload/template;
- проблема в Celery worker;
- ошибка в send path конкретного notification type.

Проверить сначала:
1. Какой `notification_type` и `channel` в alert details.
2. Что в `NotificationOutbox.last_error`.
3. Жив ли Celery worker.
4. Есть ли недавние изменения в email/telegram formatter.

Быстрые действия:
1. Проверить транспорт:
  - SMTP для email
  - Telegram route для telegram
2. Проверить обработку outbox через [`apps/notifications/services.py`](/home/jendox/PycharmProjects/palingames/apps/notifications/services.py).
3. После исправления причины повторно обработать failed outbox записи.

Когда считать проблему закрытой:
- новые outbox entries переходят в `SENT`;
- пришёл `resolved` alert `Critical notification outbox recovered`.

## 6. Storage Unavailable

Incident key:
- `storage.s3.unavailable`

Recovery title:
- `Storage recovered`

Симптомы:
- repeated alert `Storage is unavailable`;
- presigned download URL generation падает;
- user-facing download flows получают 503.

Что это обычно значит:
- S3-compatible storage недоступен;
- invalid credentials;
- bucket/network issue;
- boto client/runtime error.

Проверить сначала:
1. Есть ли `product_file.download_url.failed`.
2. Какой `operation` указан в alert details.
3. Доступен ли bucket и endpoint из окружения приложения.
4. Не менялись ли `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME`.

Быстрые действия:
1. Проверить storage endpoint и credentials.
2. Проверить bucket existence и network path.
3. Убедиться, что проблема не только в одном приложении/инстансе.

Когда считать проблему закрытой:
- `generate_presigned_download_url` снова отрабатывает успешно;
- пришёл `resolved` alert `Storage recovered`.

## 7. Readiness Returns 503

Это не отдельный app-level incident alert, а infrastructure signal через health/Prometheus.

Симптомы:
- `/health/ready/` отвечает `503`;
- load balancer может исключить инстанс из трафика;
- Prometheus alert `PalingamesReadinessDegraded`.

Проверить сначала:
1. JSON body `/health/ready/`.
2. Какой check упал:
  - `database`
  - `redis`
  - `s3`
3. Это один инстанс или все.

Быстрые действия:
1. Для database: проверить доступность PostgreSQL и лимиты соединений.
2. Для redis: проверить Redis process/container и `REDIS_URL`.
3. Для s3: проверить endpoint, bucket и credentials.

Что не делать:
- не форсить readiness в `200`;
- не отключать check ради “зелёного” статуса.

## 8. Как пользоваться runbooks

Правильный порядок реакции:
1. Определи symptom или incident key.
2. Найди соответствующий runbook.
3. Пройди quick checks.
4. Выполни минимальные безопасные действия.
5. Зафиксируй:
  - время начала;
  - affected scope;
  - первичную причину;
  - временный workaround;
  - время восстановления.
6. Если инцидент не укладывается в существующие сценарии, дополни этот документ после разбора.
