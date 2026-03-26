# Local Grafana Dashboard Guide

Этот документ содержит стартовый набор панелей и готовые PromQL-запросы для локального Grafana dashboard.

Цель:
- быстро увидеть, что приложение живо;
- понять, работает ли платежный контур;
- видеть деградацию fulfillment flow;
- не тратить время на подбор запросов с нуля.

## 1. Рекомендуемые dashboards

Для начала достаточно 3 dashboards:
- `Executive Overview`
- `Payments`
- `Fulfillment`

Этого хватит, чтобы покрыть основные operational-сценарии проекта.

## 2. Dashboard: Executive Overview

### Panel 1. Requests Per Second

Название:
- `Requests / s`

PromQL:

```promql
sum(rate(http_requests_total[5m]))
```

Тип панели:
- Time series

### Panel 2. HTTP Status Codes

Название:
- `HTTP Status Rate`

PromQL:

```promql
sum by (status_code) (rate(http_requests_total[5m]))
```

Тип панели:
- Time series

### Panel 3. Request Latency P95

Название:
- `HTTP Latency P95`

PromQL:

```promql
histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))
```

Тип панели:
- Time series

### Panel 4. Readiness Failures

Название:
- `Readiness Failures`

PromQL:

```promql
sum by (component) (increase(health_readiness_checks_total{status="failed"}[15m]))
```

Тип панели:
- Bar chart

### Panel 5. Orders Created vs Paid

Название:
- `Orders Created vs Paid`

PromQL:

```promql
sum(increase(orders_created_total[1h]))
```

```promql
sum(increase(orders_paid_total[1h]))
```

Тип панели:
- Time series

### Panel 6. Download Failures

Название:
- `Download Failures`

PromQL:

```promql
sum by (access_type, reason) (increase(product_download_failed_total[1h]))
```

Тип панели:
- Bar chart

## 3. Dashboard: Payments

### Panel 1. Orders Created

Название:
- `Orders Created`

PromQL:

```promql
sum by (checkout_type) (increase(orders_created_total[1h]))
```

### Panel 2. Orders Paid

Название:
- `Orders Paid`

PromQL:

```promql
sum by (checkout_type) (increase(orders_paid_total[1h]))
```

### Panel 3. Invoices Created

Название:
- `Invoices Created`

PromQL:

```promql
sum by (provider) (increase(invoices_created_total[1h]))
```

### Panel 4. Webhooks Received

Название:
- `Payment Webhooks Received`

PromQL:

```promql
sum by (cmd_type) (increase(payment_webhooks_received_total[1h]))
```

### Panel 5. Webhooks Failed

Название:
- `Payment Webhooks Failed`

PromQL:

```promql
sum by (reason) (increase(payment_webhooks_failed_total[1h]))
```

### Panel 6. Webhooks Rejected

Название:
- `Payment Webhooks Rejected`

PromQL:

```promql
sum by (reason) (increase(payment_webhooks_rejected_total[1h]))
```

### Panel 7. Invoice Sync Outcomes

Название:
- `Invoice Sync Outcomes`

PromQL:

```promql
sum by (result) (increase(invoice_status_sync_processed_total[1h]))
```

### Panel 8. Invoice Sync Failures

Название:
- `Invoice Sync Failures`

PromQL:

```promql
increase(invoice_status_sync_failed_total[1h])
```

## 4. Dashboard: Fulfillment

### Panel 1. Guest Email Outbox Created

Название:
- `Guest Email Outbox Created`

PromQL:

```promql
increase(guest_email_outbox_created_total[1h])
```

### Panel 2. Guest Emails Sent

Название:
- `Guest Emails Sent`

PromQL:

```promql
increase(guest_email_sent_total[1h])
```

### Panel 3. Guest Email Failures

Название:
- `Guest Email Failures`

PromQL:

```promql
increase(guest_email_failed_total[1h])
```

### Panel 4. Download Redirects

Название:
- `Download Redirects`

PromQL:

```promql
sum by (access_type) (increase(product_download_redirect_total[1h]))
```

### Panel 5. Download Failures

Название:
- `Download Failures`

PromQL:

```promql
sum by (access_type, reason) (increase(product_download_failed_total[1h]))
```

## 5. Как собирать dashboard в Grafana

### Шаг 1. Создать dashboard

В Grafana:

```text
Dashboards -> New -> New dashboard
```

### Шаг 2. Добавить panel

Для каждой панели:
- `Add visualization`
- выбрать datasource `Prometheus`
- вставить соответствующий PromQL

### Шаг 3. Выбрать тип панели

Обычно:
- `Time series` для rate и трендов;
- `Bar chart` для breakdown по `reason`, `status_code`, `component`;
- `Stat` для одиночных значений.

## 6. Полезные PromQL-подсказки

### Смотреть рост счетчика за интервал

```promql
increase(metric_name[1h])
```

### Смотреть скорость событий

```promql
rate(metric_name[5m])
```

### Суммировать по label

```promql
sum by (label_name) (...)
```

### Исключать нулевой шум

```promql
sum(increase(metric_name[1h])) > 0
```

## 7. Первый practical минимум

Если не хочешь собирать сразу все dashboards, начни с 5 панелей:

1. `sum(rate(http_requests_total[5m]))`
2. `sum by (status_code) (rate(http_requests_total[5m]))`
3. `sum(increase(orders_created_total[1h]))`
4. `sum(increase(orders_paid_total[1h]))`
5. `sum by (reason) (increase(payment_webhooks_failed_total[1h]))`

Этого уже достаточно, чтобы быстро увидеть:
- есть ли трафик;
- есть ли ошибки;
- создаются ли заказы;
- доходят ли до оплаты;
- не ломается ли webhook path.

## 8. Alerts в локальном Prometheus

В локальном Prometheus уже подключён файл:

```text
monitoring/prometheus/alerts.yml
```

Сейчас там есть стартовые правила:
- readiness degraded
- payment webhook failures
- payment webhook rejected
- invoice sync failures
- guest email failures
- download failures

Проверить их можно в Prometheus UI:

```text
http://127.0.0.1:9090/alerts
```
