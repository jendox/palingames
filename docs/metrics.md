# Metrics And Dashboards

Этот документ описывает минимальный набор метрик и дешбордов для проекта.

Цель:
- видеть здоровье системы без чтения сырых логов;
- быстро замечать деградацию checkout/payment/download flows;
- иметь опорную картину по фоновым задачам.

Этот документ относится к metrics/Prometheus слою.
App-level incident alerts и recovery alerts описаны отдельно:
- [docs/observability.md](/home/jendox/PycharmProjects/palingames/docs/observability.md)
- [docs/runbooks.md](/home/jendox/PycharmProjects/palingames/docs/runbooks.md)

## 1. Принципы

Не стоит собирать “всё подряд”.

Хорошая метрика отвечает хотя бы на один вопрос:
- поток работает или деградирует;
- пользователи доходят до оплаты;
- fallback-механизмы работают;
- фоновые задачи не накапливают сбои;
- скачивание файлов реально доступно.

Метрики в проекте удобно делить на 4 группы:
- traffic and requests;
- business flow;
- background jobs;
- infrastructure dependencies.

## 2. Minimal Metrics Set

### Что уже внедрено в коде

На текущий момент уже экспортируются:
- `http_requests_total`
- `http_request_duration_seconds`
- `orders_created_total`
- `orders_paid_total`
- `invoices_created_total`
- `payment_webhooks_received_total`
- `payment_webhooks_failed_total`
- `payment_webhooks_rejected_total`
- `invoice_status_sync_runs_total`
- `invoice_status_sync_selected_total`
- `invoice_status_sync_processed_total`
- `invoice_status_sync_failed_total`
- `guest_email_outbox_created_total`
- `guest_email_sent_total`
- `guest_email_failed_total`
- `product_download_redirect_total`
- `product_download_failed_total`
- `health_readiness_checks_total`
- `celery_task_started_total`
- `celery_task_finished_total`

Остальные метрики ниже либо уже реализованы, либо являются следующим рекомендуемым расширением.

### A. HTTP / request layer

- `http_requests_total`
  - labels:
    - `path`
    - `method`
    - `status_code`

- `http_request_duration_seconds`
  - labels:
    - `path`
    - `method`

Зачем:
- видеть деградацию endpoint-ов;
- быстро находить рост 4xx/5xx;
- отслеживать медленные страницы и webhook endpoint-ы.

### B. Orders / checkout

- `orders_created_total`
  - labels:
    - `checkout_type`
    - `source`

- `orders_paid_total`
  - labels:
    - `checkout_type`
    - `source`

- `orders_failed_total`
  - labels:
    - `failure_reason`
  - статус:
    - рекомендовано добавить позже

- `checkout_validation_failed_total`
  - статус:
    - рекомендовано добавить позже

Зачем:
- видеть, создаются ли заказы;
- понимать, сколько из них реально оплачиваются;
- замечать поломки funnel.

### C. Invoices / payments

- `invoices_created_total`
  - labels:
    - `provider`

- `invoices_paid_total`
  - labels:
    - `provider`
  - статус:
    - рекомендовано добавить позже

- `payment_webhooks_received_total`
  - labels:
    - `provider`
    - `cmd_type`

- `payment_webhooks_failed_total`
  - labels:
    - `provider`
    - `reason`

- `payment_webhooks_rejected_total`
  - labels:
    - `provider`
    - `reason`

- `invoice_status_sync_runs_total`

- `invoice_status_sync_selected_total`

- `invoice_status_sync_processed_total`
  - labels:
    - `result`
  - where `result` can be:
    - `paid`
    - `expired`
    - `canceled`
    - `pending`
    - `refunded`
    - `unknown`
    - `skipped`

- `invoice_status_sync_failed_total`

Зачем:
- видеть состояние платежного пайплайна;
- отделять webhook path от reconciliation path;
- понимать, не начинает ли система “жить только на sync”.

### D. Access / email / downloads

- `guest_access_issued_total`
  - статус:
    - рекомендовано добавить позже

- `guest_email_outbox_created_total`

- `guest_email_sent_total`

- `guest_email_failed_total`

- `product_download_redirect_total`
  - labels:
    - `access_type`
  - where `access_type`:
    - `user`
    - `guest`

- `product_download_failed_total`
  - labels:
    - `access_type`
    - `reason`

Зачем:
- видеть, что after-payment fulfillment реально работает;
- замечать, что пользователь оплатил, но не может скачать товар;
- отделять проблемы email от проблем storage/download.

Эти метрики сейчас уже дополняются app-level incident families:
- `downloads.delivery.failures`
- `notifications.outbox.failures`
- `storage.s3.unavailable`

### E. Background tasks

- `celery_task_started_total`
  - labels:
    - `task_name`

- `celery_task_finished_total`
  - labels:
    - `task_name`
    - `task_state`

- `celery_task_failed_total`
  - labels:
    - `task_name`
  - статус:
    - рекомендовано добавить позже

Зачем:
- понимать, какие задачи падают;
- видеть нестабильность worker-а;
- находить перегруженные или проблемные task pipelines.

### F. Infrastructure / dependencies

- `health_readiness_checks_total`
  - labels:
    - `component`
    - `status`

- `s3_download_url_generation_failed_total`
  - статус:
    - рекомендовано добавить позже

- `s3_upload_failed_total`
  - статус:
    - рекомендовано добавить позже

- `redis_errors_total`
  - статус:
    - рекомендовано добавить позже

- `database_errors_total`
  - статус:
    - рекомендовано добавить позже

Зачем:
- быстро отделять бизнес-сбой от инфраструктурного;
- видеть деградацию зависимостей еще до массовых пользовательских ошибок.

## 3. Recommended Dashboards

### Dashboard 1: Executive Overview

Для быстрого статуса системы.

Панели:
- requests per minute
- 5xx rate
- readiness status
- orders created vs paid
- webhook failures
- invoice sync failures
- guest email failures
- download failures

Кому полезен:
- тебе;
- дежурному;
- любому, кто хочет понять “все ли вообще живо”.

### Dashboard 2: Payments

Фокус только на money flow.

Панели:
- `orders_created_total`
- `orders_paid_total`
- `invoices_created_total`
- `payment_webhooks_received_total`
- `payment_webhooks_failed_total`
- `payment_webhooks_rejected_total`
- `invoice_status_sync_processed_total`
- `invoice_status_sync_failed_total`

Главный вопрос:
- работает ли цепочка `checkout -> invoice -> paid`.

### Dashboard 3: Fulfillment

Фокус на post-payment delivery.

Панели:
- `guest_access_issued_total`
- `guest_email_outbox_created_total`
- `guest_email_sent_total`
- `guest_email_failed_total`
- `product_download_redirect_total`
- `product_download_failed_total`

Главный вопрос:
- после оплаты пользователь реально получает доступ к товару или нет.

### Dashboard 4: Background Jobs

Фокус на Celery.

Панели:
- tasks started
- tasks finished by state
- tasks failed by name
- invoice sync run outcomes
- guest email outbox task failures

Главный вопрос:
- не ломается ли система в фоне, пока фронтально все выглядит “нормально”.

## 4. First Alert Rules From Metrics

Если позже появится Prometheus/Alertmanager или аналог, начинать стоит с таких правил:

- `payment_webhooks_failed_total` резко растет
- `payment_webhooks_rejected_total` резко растет
- `invoice_status_sync_failed_total` растет подряд несколько интервалов
- `guest_email_failed_total` > 0 стабильно несколько интервалов
- `product_download_failed_total` > 0 стабильно несколько интервалов
- `health_readiness_checks_total{status="failed"}` растет

Важно:
- эти правила не заменяют app-level Telegram incidents;
- они дополняют их, особенно для infra-level symptoms и trends.

Текущий рекомендуемый split:
- payment/download/outbox/storage runtime failures -> app-level incident alerts;
- readiness degradation, broad infra issues, dashboarding -> Prometheus alerts.

## 5. What Not To Measure Yet

На раннем этапе не стоит сразу тащить:
- десятки метрик по каждому template view;
- детальные per-user/per-order labels;
- high-cardinality labels вроде email, full path with ids, order number;
- “бизнес-аналитику” уровня marketing attribution в operational stack.

Почему:
- это шумит;
- усложняет дешборды;
- может сделать metrics backend дорогим и медленным.

## 6. Label Hygiene

Хорошие labels:
- `provider`
- `checkout_type`
- `source`
- `task_name`
- `task_state`
- `reason`
- `status_code`

Плохие labels:
- `email`
- `request_id`
- `order_id`
- `invoice_id`
- `provider_invoice_no`
- `token`

Правило:
- уникальные идентификаторы хороши для логов и Sentry;
- плохи для метрик.

## 7. Rollout Order

Я бы внедрял метрики в таком порядке:

1. HTTP requests and durations
2. Payment/webhook/invoice sync counters
3. Guest email and download counters
4. Celery task state counters
5. Readiness/dependency counters

То есть сначала мерить самые критичные бизнес-цепочки, а не инфраструктуру ради инфраструктуры.

## 8. Success Criteria

Можно считать metrics layer достаточно полезным, если ты можешь без чтения логов ответить на 5 вопросов:

1. Создаются ли сейчас заказы?
2. Оплачиваются ли они?
3. Не ломаются ли webhook-и?
4. Работает ли fallback sync?
5. Доходит ли пользователь до скачивания товара?
