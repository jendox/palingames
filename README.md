# PalinGames

**Production e-commerce platform for selling and delivering digital educational materials.**

PalinGames is a Django-based online store built for digital educational products. It covers the complete purchase lifecycle: product discovery, cart and checkout, online payment, secure fulfillment, customer access, guest downloads, notifications, and operational monitoring.

**Live:** https://palingames.by

## What the system does

A customer can browse the catalog, purchase one or more digital products, pay through ExpressPay, and receive secure access to the purchased files. The platform supports both authenticated customers and guest checkout.

The backend is responsible not only for commerce, but also for secure file delivery, background notifications, payment processing, incident reporting, and operational health checks.

## Key features

- Product catalog, categories, product pages, reviews, cart, and checkout
- Guest and authenticated-user purchase flows
- Guest cart merge after login
- ExpressPay payment integration and webhook processing
- Persistent access to purchased products for registered users
- Time- and download-limited guest access
- Private S3-compatible storage for digital product files
- Short-lived presigned download URLs
- Direct product-file uploads from Django Admin
- Managed permanent QR links for downloadable materials
- Email and Telegram notifications through a unified outbox
- Background processing with Celery
- Structured JSON logging
- Health/readiness endpoints and Prometheus metrics
- Telegram incident alerts with threshold, deduplication, and recovery semantics
- Optional Sentry integration
- Docker-based development and production deployment

## Purchase and fulfillment flow

```text
Catalog
  ↓
Cart
  ↓
Checkout
  ↓
Order + Invoice
  ↓
ExpressPay
  ↓
Payment webhook / status sync
  ↓
Payment confirmed
  ↓
Access grant created
  ↓
Notification queued
  ↓
Secure download through backend
  ↓
Short-lived presigned S3 URL
```

### Registered users

After successful payment, the system creates a persistent `UserProductAccess` record. Download requests always pass through the backend, which verifies access before generating a short-lived storage URL.

### Guest checkout

For guest purchases, the application creates temporary `GuestAccess` grants with configurable expiration and download limits. Customers receive backend download links by email rather than direct object-storage URLs.

## Architecture

The project is split into focused Django applications:

```text
apps/
├── users/           # custom users and authentication
├── pages/           # content pages and customer account
├── products/        # catalog, images, files, S3 services
├── cart/            # guest/user cart and merge flow
├── orders/          # checkout, orders and order items
├── payments/        # invoices, payment processing, webhooks
├── access/          # paid product access and guest grants
├── managed_links/   # permanent QR-backed links
├── notifications/   # notification outbox and delivery handlers
├── emails/          # SMTP sending, logs and suppression
└── core/            # shared infrastructure and observability
```

A more detailed system map, payment flow, fulfillment flow, and failure modes are documented in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Engineering highlights

### Secure digital delivery

Product archives are stored in private S3-compatible object storage. The application stores metadata and object keys in PostgreSQL, while actual downloads are authorized by the backend and served through short-lived presigned URLs.

### Reliable notifications

Outgoing email and Telegram messages use a database-backed `NotificationOutbox`. Background workers receive only the outbox ID; sensitive payload data remains encrypted in the database.

### Payment resilience

Payment confirmation is handled through webhook processing and background status synchronization. Fulfillment is separated from the payment provider itself, which makes access delivery easier to retry and observe.

### Operational visibility

The application exposes:

```text
/health/live/
/health/ready/
/metrics/
```

Readiness checks cover PostgreSQL, Redis, and object storage. Production incidents can be reported separately from normal business notifications.

## Tech stack

| Area | Technologies |
|---|---|
| Backend | Python 3.13, Django 5.2, Django REST Framework |
| Database | PostgreSQL 16 |
| Background jobs | Celery, django-celery-beat, Redis |
| Authentication | django-allauth |
| Frontend | Django Templates, HTMX, Tailwind CSS 4 |
| Storage | S3-compatible object storage, boto3, MinIO for local development |
| Payments | ExpressPay |
| Validation / tooling | Pydantic 2, Ruff, uv |
| Observability | structured JSON logging, Prometheus, Grafana, optional Sentry |
| Deployment | Docker Compose, Caddy |

## Local development

### Requirements

- Python 3.13
- `uv`
- Docker / Docker Compose

### Setup

```bash
uv sync
cp .env.example .env
make up-develop
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

In separate terminals:

```bash
make tailwind
uv run celery -A config worker -l info
uv run celery -A config beat -l info
```

The development stack provides PostgreSQL, Redis, SMTP testing, object storage, and optional monitoring services.

## Testing

The primary test environment uses PostgreSQL rather than SQLite so that database behavior stays close to production.

```bash
make up-develop
./.venv/bin/python manage.py test
```

Payment-specific tests can be run separately:

```bash
./.venv/bin/python manage.py test apps.payments
```

## Deployment and operations

Production deployment assets and environment guidance are kept in [`deploy/README.md`](deploy/README.md).

Additional documentation:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — architecture and core flows
- [`docs/observability.md`](docs/observability.md) — metrics and incident model
- [`docs/runbooks.md`](docs/runbooks.md) — operational runbooks
- [`docs/metrics.md`](docs/metrics.md) — metrics plan
- [`docs/monitoring-local.md`](docs/monitoring-local.md) — local monitoring setup

## Project focus

PalinGames is an example of a production backend where e-commerce, payments, background processing, secure digital delivery, and operational reliability need to work together as one system.
