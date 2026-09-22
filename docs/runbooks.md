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
- `payments.unmapped_provider_status`
- `payments.order_refunded`
- `downloads.delivery.failures`
- `notifications.outbox.failures`
- `storage.s3.unavailable`
- `orders.delivery.invariant`
- `payments.invoice_creation.missing`

Recovery/resolved alerts сейчас реализованы для:
- `payments.status_sync.failures`
- `downloads.delivery.failures`
- `notifications.outbox.failures`
- `storage.s3.unavailable`

Без recovery-сигнала: `payments.webhook.failures`, `orders.delivery.invariant`,
`payments.invoice_creation.missing`, `payments.unmapped_provider_status`, `payments.order_refunded`.
Последние три — событийные (разбираются вручную), поэтому «восстановление» для них не определено.

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
- `invoice_created_user`
- `auth_account_email`

Симптомы:
- пользователь не получил критичное письмо со ссылкой на скачивание (`guest_order_download`, `custom_game_download`);
- пользователь не получил письмо со ссылкой на оплату после checkout или создания инвойса игры на заказ (`invoice_created_user`);
- пользователь не получил auth-письмо с confirm/reset/login link (`auth_account_email`);
- в Telegram пришёл alert `Repeated critical notification outbox failures`;
- в БД outbox-записи остаются в `FAILED`;
- outbox «завис» в `PENDING` или `PROCESSING` (письмо не ушло, incident ещё не сработал).

Автовосстановление (Celery Beat, каждые 10 мин):
- task `reap_stuck_notification_outbox_processing_task`;
- grace `NOTIFICATION_OUTBOX_PROCESSING_TIMEOUT_MINUTES` (по умолчанию 15 мин);
- stale `PROCESSING`: reconcile по `EmailLog.SENT` или повтор `send_notification_outbox_task`;
- stale `PENDING` (старше grace): повторная постановка `send_notification_outbox_task`;
- логи `notification.outbox.reaper.completed`, `notification.outbox.reconciled`, `notification.outbox.processing.recovered`.

Повторная обработка в `process_notification_outbox`:
- свежий `PROCESSING` → skip (`processing_in_progress`), без второго SMTP;
- stale `PROCESSING` без `EmailLog` → recovery и retry send;
- после `NOTIFICATION_OUTBOX_MAX_PROCESSING_ATTEMPTS` reaper помечает outbox `FAILED` и шлёт critical outbox incident.
- Celery: `CELERY_TASK_ACKS_LATE` / `CELERY_TASK_REJECT_ON_WORKER_LOST` — задача переотправляется при падении worker до ack.

Что это обычно значит:
- SMTP/transport недоступен;
- ошибка в payload/template;
- проблема в Celery worker;
- ошибка в send path конкретного notification type.

Проверить сначала:
1. Какой `notification_type` и `channel` в alert details.
2. Статус outbox: `PENDING` / `PROCESSING` / `FAILED` / `SENT`, `attempts`, `last_attempt_at`, `created_at`.
3. Что в `NotificationOutbox.last_error`.
4. Жив ли Celery worker и beat (`Reap stuck notification outbox processing` в django-celery-beat).
5. Есть ли недавние изменения в email/telegram formatter.
6. Для `invoice_created_user`: есть ли у связанного `Invoice` поле `invoice_url` и совпадает ли `payment_email_sent_for_provider_invoice_no` с `provider_invoice_no`.
7. Для `auth_account_email`: не срабатывает ли allauth rate limit `confirm_email` (1/10s/key) — повторный resend в течение 10 секунд не создаёт outbox.
8. В admin **Emails → Email logs**: статус (`SENT` / `FAILED` / `SUPPRESSED`), `error`, связь с outbox. Для `SUPPRESSED` проверить **Emails → Email suppressions** (manual unsuppress через `active=False`). Если outbox `PROCESSING`, а EmailLog уже `SENT` — дождаться reaper/reconcile или вручную «Отправить» из admin outbox (идемпотентно после reconcile).

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
1. Есть ли `product_file.download_url.failed` или `managed_link.redirect.s3_failed`.
2. Какой `operation` указан в alert details (`generate_presigned_download_url`, `managed_link_generate_presigned_download_url`, `managed_link_generate_presigned_upload_url`).
3. Доступен ли bucket и endpoint из окружения приложения.
4. Не менялись ли `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME`, `S3_MANAGED_LINKS_BUCKET_NAME`.

Быстрые действия:
1. Проверить storage endpoint и credentials.
2. Проверить bucket existence и network path.
3. Убедиться, что проблема не только в одном приложении/инстансе.

Когда считать проблему закрытой:
- `generate_presigned_download_url` снова отрабатывает успешно;
- пришёл `resolved` alert `Storage recovered`.

### Managed links: orphan objects в `qr-assets`

Симптомы:
- после неудачного finalize в admin остался объект в S3 без строки `ManagedLink.s3_file_key`;
- `/go/<token>/` работает по `external_url`, но в bucket есть лишние ключи `{token}/{uuid}.ext`.

Действия:
1. Найти ключ в bucket (prefix `{token}/`).
2. Сверить с `ManagedLink.s3_file_key` в PostgreSQL.
3. Удалить orphan вручную через S3 CLI/console, если ключ не используется ни одной записью.

Автоматический orphan cleanup **не** включён в MVP.

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
  - `managed_links_s3` (если настроен `S3_MANAGED_LINKS_BUCKET_NAME`)
3. Это один инстанс или все.

Быстрые действия:
1. Для database: проверить доступность PostgreSQL и лимиты соединений.
2. Для redis: проверить Redis process/container и `REDIS_URL`.
3. Для s3: проверить endpoint, bucket и credentials.

Что не делать:
- не форсить readiness в `200`;
- не отключать check ради “зелёного” статуса.

## 8. Order Stuck Without Payment Invoice

Incident key:
- `payments.invoice_creation.missing`

Симптомы:
- в Telegram topic `Incidents` пришёл alert `Order stuck without payment invoice`;
- в логах `order_delivery_watchdog.completed` с `checked_unpaid_orders > 0` и `problems > 0`;
- заказ в админке `CREATED` или `WAITING_FOR_PAYMENT`, нет `Invoice` или у инвойса пустые
  `provider_invoice_no` / `invoice_url`;
- клиент оформил checkout, но письмо со ссылкой на оплату не пришло.

Что это обычно значит:
- `create_invoice_task` не завершилась после исчерпания retry (сеть, 5xx Express Pay);
- задача не была обработана Celery;
- реже — сбой между успешным ответом API и записью в БД (см. ниже про дубликат у провайдера).

Проверить сначала:
1. Логи `invoice.creation.failed`, `invoice.creation.retry_scheduled`, `invoice.creation.success` по `order_id` / `target_id`.
2. Заказ: `status`, `payment_account_no`, `created_at`, связанный `Invoice`.
3. Очередь Celery / состояние worker.

Быстрые действия:
1. В админке заказа — **«Создать инвойс»** (offsite) или повторно поставить
   `create_invoice_task` для site-checkout заказа.
2. Убедиться, что появились `invoice_url` и outbox `invoice_created_user` (письмо со ссылкой).
3. Если клиент уже жаловался — проверить spam и email на заказе.

Дубликат инвойса у Express Pay:
- при редком retry после «API успешен, БД не записана» у провайдера может появиться лишний
  неоплаченный счёт; в PostgreSQL остаётся **один** инвойс на заказ, покупателю уходит актуальная
  ссылка. Это **допустимо**; сверка — в кабинете Express Pay по `payment_account_no`.

Dedupe:
- повторный alert по тому же заказу — не чаще `INCIDENT_ALERT_DEDUPE_TTL_SECONDS` (по умолчанию 15 мин).

## 9. Paid Order Delivery Invariant

Incident key:
- `orders.delivery.invariant`

Симптомы:
- в Telegram topic `Incidents` пришёл alert `Paid order delivery problem`;
- в логах есть `order_delivery_watchdog.completed` с `problems > 0`;
- заказ в админке `PAID`, но клиент не получил доступ или guest email.

Типовые `problem` codes:
- `order_paid_at_missing`
- `invoice_missing`, `invoice_status_mismatch`, `invoice_paid_at_missing`
- `missing_user_product_access`, `missing_guest_access`
- `guest_download_notification_missing`, `guest_download_notification_failed`, `guest_download_notification_stuck`
- `authenticated_order_without_user`, `unknown_checkout_type`

Что это обычно значит:
- fulfillment после оплаты не завершился полностью;
- рассинхрон invoice/order;
- access не выдан или guest email outbox не создан/упал.

Что проверить:
1. `Order`, `Invoice`, `UserProductAccess` / `GuestAccess` для `order_id` из alert.
2. Для guest — `NotificationOutbox` с `notification_type=guest_order_download`.
3. Логи оплаты: webhook / `mark_order_paid` / grant access tasks.

Что не делать автоматически через watchdog:
- watchdog только обнаруживает и алертит; self-healing в первой итерации не реализован.

Dedupe:
- повтор того же нарушения не спамит чат 7 дней (настраивается `ORDER_DELIVERY_ALERT_DEDUPE_TTL_SECONDS`).

## 10. Unmapped Payment Provider Status

### Политика DEFERRED-1 (оплата картой не в продукте)

PalinGames **намеренно не маппит** коды Express Pay `4` (`PARTIALLY_PAID`) и `6` (`PAID_BY_CARD`), пока оплата картой **не включена** как продуктовая возможность. Это **не** «висящий баг», а отложенная capability с контролями:

- заказ и инвойс **не** переводятся в `PAID` по этим кодам автоматически;
- каждый такой webhook → **`payments.unmapped_provider_status`** (Telegram Incidents), лог `payment.status.unmapped`, метрика `payment_unmapped_provider_status_total`.

**Ожидаемо в норме:** алертов по `6` **нет**, если в кабинете Express Pay не включали оплату картой.

**Если пришёл алерт с `provider_status=6`:**

1. Проверить, не включили ли карты у провайдера без деплоя маппинга.
2. Проверить в EP, была ли реальная оплата; при подтверждённой полной оплате — выдать доступ вручную (ниже).
3. Завести работу **`card-payment-readiness`**: `PAID_BY_CARD → PAID`, отдельная политика для `4` (частичная оплата **не** выдаёт товар).

**Перед включением карт на сайте и в EP:** сначала merge/deploy маппинга и smoke на staging. Не добавлять «заготовку» `6→PAID` в prod, пока карты выключены — иначе ошибочный статус 6 от провайдера выдаст товар без намерения.

Подробнее: `.cursor/plans/Reliability audit bugfixes-22092026.plan.md` §0.1 (локально, не в git).

---

Incident key:
- `payments.unmapped_provider_status`

Симптомы:
- в Telegram topic `Incidents` пришёл alert `Unmapped payment provider status`;
- в логах есть `payment.status.unmapped` с полем `provider_status`;
- растёт `payment_unmapped_provider_status_total`;
- заказ остался в `WAITING_FOR_PAYMENT`, хотя клиент утверждает, что оплатил.

Что это значит:
- Express Pay прислал статус, которого нет в `map_invoice_status`
  ([`apps/payments/services.py`](../../apps/payments/services.py));
- приложение намеренно **не** меняет состояние заказа, чтобы не выдать товар по непонятному сигналу.

Самые вероятные коды:
- `6` (`PAID_BY_CARD`) — DEFERRED-1: карты включили у провайдера или пришёл реальный card-payment без маппинга в коде;
- `4` (`PARTIALLY_PAID`) — частичная оплата; товар выдавать нельзя (отдельная политика при включении карт).

Что проверить:
1. `provider_status` из лога и сверить с `libs/express_pay/models.py::InvoiceStatus`.
2. Реальный статус инвойса в личном кабинете Express Pay.
3. Не включили ли недавно новый способ оплаты.

Быстрые действия:
1. Если это подтверждённая полная оплата — выдать доступ вручную через админку и зафиксировать заказ.
2. Не «чинить» это выставлением `PAID` в БД до подтверждения провайдера.
3. Завести задачу на добавление кода в `map_invoice_status`; для частичной оплаты нужен отдельный
   статус и ручной разбор, а не маппинг в `PAID`.

## 11. Order Refunded

Incident key:
- `payments.order_refunded`

Симптомы:
- в Telegram topic `Incidents` пришёл alert `Order refunded by payment provider`;
- заказ в админке перешёл в статус `REFUNDED`, заполнен `refunded_at`.

Что это значит:
- провайдер сообщил о возврате средств по инвойсу;
- приложение зафиксировало факт, но **ничего не откатывает автоматически**.

Что происходит и чего не происходит:
- `order.paid_at` сохраняется намеренно — нужен для сверки и отчётности;
- выданный `UserProductAccess` / `GuestAccess` **не отзывается**: файлы, скорее всего, уже скачаны;
- возврат терминальный: последующий `PAID` от провайдера игнорируется.

Что проверить:
1. Инициатор возврата: клиент, поддержка или провайдер.
2. Скачивались ли файлы (`downloads_count` у `GuestAccess`, логи download view).
3. Попал ли заказ в уже отправленный месячный NPD-отчёт.

Быстрые действия:
1. Решить по политике возвратов, нужно ли отзывать доступ; при необходимости деактивировать
   access вручную в админке.
2. Если отчёт за месяц уже сформирован, скорректировать его вручную.
3. При росте числа возвратов — разбираться с причиной на стороне продукта, а не кода.

## 11. Как пользоваться runbooks

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
