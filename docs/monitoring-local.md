# Local Prometheus And Grafana

Этот документ описывает, как использовать локальный monitoring stack в dev.

## 1. Что уже есть в проекте

В `docker-compose.develop.yml` уже добавлены:
- `prometheus`
- `grafana`

Prometheus скрапит метрики Django с:

```text
http://host.docker.internal:8000/metrics/
```

Grafana уже получает datasource на локальный Prometheus через provisioning.

## 2. Что должно быть запущено

### Шаг 1. Поднять dev-инфраструктуру

```bash
make up-develop
```

После этого поднимутся:
- PostgreSQL
- Redis
- smtp4dev
- Prometheus
- Grafana

### Шаг 2. Запустить Django

Важно: Prometheus работает в контейнере и ходит к Django через `host.docker.internal:8000`.
Поэтому в локальной Linux-разработке Django должен быть доступен на `0.0.0.0:8000`, а не только на loopback `127.0.0.1:8000`.

```bash
make runserver-observability
```

### Шаг 3. Проверить endpoint метрик

Открыть:

```text
http://127.0.0.1:8000/metrics/
```

Если все в порядке, ты увидишь текст в формате Prometheus metrics.

## 3. Куда заходить

- Django metrics endpoint: `http://127.0.0.1:8000/metrics/`
- Prometheus UI: `http://127.0.0.1:9090`
- Grafana UI: `http://127.0.0.1:3000`

## 4. Логин и пароль Grafana

Если ты отдельно ничего не настраивал, Grafana использует стандартные дефолтные credentials:

- login: `admin`
- password: `admin`

При первом входе Grafana обычно просит сразу сменить пароль.

Это нормальное поведение.

## 5. Если Grafana не пускает с `admin/admin`

Возможные причины:
- ты уже заходил раньше и менял пароль;
- сохранились старые данные в volume `grafana_data`;
- Grafana стартовала не с нуля, а с уже существующим локальным состоянием.

### Вариант A. Вспомнить или сбросить пароль через UI

Если помнишь старый пароль, просто используй его.

### Вариант B. Сбросить состояние Grafana полностью

Если локальные дашборды не жалко, можно удалить volume Grafana и поднять контейнер заново:

```bash
docker compose -f docker-compose.develop.yml down
docker volume rm palingames_grafana_data
make up-develop
```

После этого Grafana снова должна пустить с:
- `admin`
- `admin`

### Вариант C. Явно задать логин и пароль через env

Если хочешь стабильные credentials в dev, можно позже добавить в `docker-compose.develop.yml`:

```yaml
environment:
  GF_SECURITY_ADMIN_USER: admin
  GF_SECURITY_ADMIN_PASSWORD: admin
```

Сейчас это не добавлено, потому что дефолтного поведения обычно достаточно для локальной разработки.

## 6. Как проверить, что Prometheus реально скрапит метрики

### Через браузер

Открыть:

```text
http://127.0.0.1:9090/targets
```

Ты должен увидеть target:
- `palingames-django`

И его состояние должно быть:
- `UP`

### Если target не `UP`

Проверь по порядку:

1. Django реально запущен на `0.0.0.0:8000`
2. `/metrics/` открывается в браузере
3. Prometheus контейнер поднят
4. Docker поддерживает `host.docker.internal`

## 7. Как проверить datasource в Grafana

1. Открыть Grafana
2. Перейти:

```text
Connections -> Data sources
```

3. Должен быть datasource:
- `Prometheus`

4. Можно нажать:
- `Save & test`

Если все хорошо, Grafana подтвердит, что datasource рабочий.

## 8. Как быстро проверить метрики в Prometheus

Открыть Prometheus UI и попробовать запросы:

```promql
http_requests_total
```

```promql
orders_created_total
```

```promql
orders_paid_total
```

```promql
payment_webhooks_received_total
```

```promql
invoice_status_sync_runs_total
```

```promql
product_download_redirect_total
```

Если ты до этого открыл страницы приложения или запускал фоновые задачи, часть этих метрик уже должна иметь ненулевые значения.

## 9. Какой первый дешборд имеет смысл собрать

Для первого локального дешборда достаточно 6 панелей:

1. `rate(http_requests_total[5m])`
2. `sum by (status_code) (rate(http_requests_total[5m]))`
3. `orders_created_total`
4. `orders_paid_total`
5. `payment_webhooks_failed_total`
6. `invoice_status_sync_failed_total`

Этого уже достаточно, чтобы понять:
- идут ли запросы;
- есть ли ошибки;
- создаются ли заказы;
- проходят ли оплаты;
- не ломается ли payment/reconciliation path.

## 10. Когда это действительно полезно в dev

Prometheus/Grafana на локалке имеет смысл, если ты:
- работаешь с платежами;
- отлаживаешь Celery;
- проверяешь health/readiness;
- внедряешь observability;
- собираешь дашборды или алерты.

Обычно не имеет смысла держать их поднятыми постоянно, если ты:
- меняешь только шаблоны;
- делаешь мелкие UI-правки;
- не трогаешь фоновые процессы и operational flow.

## 11. Минимальный сценарий использования

Если нужен самый короткий сценарий:

1. `make up-develop`
2. `make runserver-observability`
3. открыть `http://127.0.0.1:8000/metrics/`
4. открыть `http://127.0.0.1:9090/targets`
5. открыть `http://127.0.0.1:3000`
6. логиниться `admin/admin`

Если после этого все открывается и target в Prometheus `UP`, значит локальный monitoring stack работает нормально.
