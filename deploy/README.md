# Production deploy (Docker + VPS)

Содержимое каталога: **образ приложения**, **prod docker-compose**, **Caddy**, **Prometheus/Grafana для сервера**, скрипты и пример переменных окружения.

Локальный dev-стек по-прежнему в корне: `docker-compose.develop.yml` и `monitoring/`.

## Быстрый старт на VPS

1. Установите Docker и Docker Compose plugin.
2. Склонируйте репозиторий (или скопируйте только каталог `deploy/` и при необходимости `docker-compose.prod.yml` + конфиги).
3. `cd deploy`
4. `cp env.example .env` и заполните секреты (в т.ч. `DJANGO_SECRET_KEY`, `APP_DATA_ENCRYPTION_KEY`, OAuth, `DATABASE_URL` с паролем).
5. Выставьте боевой домен: `CADDY_DOMAIN=shop.example.com` (для Let’s Encrypt не указывайте схему `https://`).
6. Поднимите стек:
   - **Сборка на сервере:**  
     `docker compose -f docker-compose.prod.yml up -d --build`
   - **Только образ с Docker Hub:** задайте `PALINGAMES_WEB_REF=youruser/palingames:tag` в `.env` или в окружении, затем  
     `docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d`
7. Миграции:  
   `docker compose -f docker-compose.prod.yml exec web python manage.py migrate --noinput`
8. Суперпользователь (один раз):  
   `docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser`

## Метрики и доступ

- Приложение **не публикует порт 8000** наружу: с интернета идёт только трафик через **Caddy** (80/443).
- **Prometheus** скрапит `http://web:8000/metrics/` во внутренней сети compose.
- В **Caddyfile** путь `/metrics` отвечает **404** (дополнительная защита).
- UI **Prometheus** проброшен только на localhost VPS: `127.0.0.1:9090`; **Grafana** — `127.0.0.1:3000` (доступ по SSH-туннелю или с хоста).

## CI/CD (Docker Hub)

1. Создайте репозиторий образа на Docker Hub и access token.
2. В GitHub: секреты `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`.
3. Workflow [.github/workflows/ci-cd.yml](../.github/workflows/ci-cd.yml) при push в `main` выполняет `ruff` и собирает/push образ `PALINGAMES_WEB_REF` (см. переменные в workflow).
4. На сервере задайте тот же тег, выполните `scripts/deploy_remote.sh` или команды из него вручную.

## Образ приложения

Файл [Dockerfile](Dockerfile) рассчитан на контекст **корня репозитория**:

```bash
docker build -f deploy/Dockerfile -t youruser/palingames:mytag .
```

Сборка выполняет `tailwind build`, `collectstatic` (Whitenoise manifest), CMD — **gunicorn**.

## Alertmanager (опционально)

После настройки получателей в [alertmanager/alertmanager.yml](alertmanager/alertmanager.yml) поднимите профиль:

```bash
docker compose -f docker-compose.prod.yml --profile alerting up -d
```

Чтобы Prometheus отправлял алерты в Alertmanager, добавьте в `prometheus/prometheus.yml` секцию `alerting` (см. [документацию Prometheus](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#alertmanager_config)).

## Медиа-файлы

В продакшене при `DEBUG=False` Django не раздаёт `MEDIA_URL` через `urls.py`. Файлы из админки должны храниться в **S3-совместимом хранилище** (как в типичной конфигурации проекта), а не на локальном volume контейнера `web`.
