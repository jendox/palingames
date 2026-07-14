# Analytics setup GA4 — staging runbook

**Последнее обновление:** 14 июля 2026  
**Среда:** prod `https://palingames.by`  
**GTM:** `GTM-5XBD94WD`  
**GA4:** `G-4EGSM0Z2SN`

---

## 1. Цель runbook

Проверка клиентской аналитики (GTM → GA4), подтверждение Live Version контейнера, затем отдельная проверка server-side событий через Measurement Protocol. Перед запуском Яндекс Директ — отдельная задача по Яндекс Метрике (см. §10).

**Не в scope:** изменение consent-архитектуры, inline-скрипты, прямой `gtag.js`, клиентские GTM-теги для `purchase` / `file_download_guest` / `file_download_custom_game`.

---

## 2. Архитектура (текущее состояние)

### 2.1. Consent

| Компонент | Статус |
|-----------|--------|
| Basic Consent Mode, default `denied` в `<head>` | ✅ |
| GTM + `analytics.js` только после Accept analytics cookies | ✅ |
| `cookie-consent.js` управляет consent и загрузкой GTM | ✅ |
| Inline `<script>` для событий в шаблонах | ❌ не используется |
| Прямой `gtag.js` с GA4 ID | ❌ не используется |

После Accept: `analytics_storage = granted` → GTM → `analytics.js` → `dataLayer.push(...)`.

### 2.2. Каналы событий

| Событие | Канал | GTM-тег |
|---------|-------|---------|
| `page_view` | Client dataLayer | ✅ Live |
| `view_item_list` | Client dataLayer | ✅ Live |
| `view_item` | Client dataLayer | ✅ Live |
| `add_to_cart` | Client dataLayer | ✅ Live |
| `begin_checkout` | Client dataLayer | ✅ Live |
| `sign_up` | Client dataLayer | ✅ Live |
| `login` | Client dataLayer | ✅ Live |
| `file_download_account` | Client dataLayer | ✅ Live |
| `purchase` | Server MP only | ❌ клиентский тег не нужен |
| `file_download_guest` | Server MP only | ❌ клиентский тег не нужен |
| `file_download_custom_game` | Server MP only | ❌ клиентский тег не нужен |

### 2.3. PII

В аналитику не передаётся: email, имя, телефон, `user_id`, signed URL, токены, персональные идентификаторы.

### 2.4. Ключевые файлы

- `static/js/analytics.js` — клиентские dataLayer-события
- `static/js/cookie-consent.js` — consent + загрузка GTM
- `apps/core/analytics.py` — GA4 Measurement Protocol
- `apps/core/analytics_events.py` — session queue для one-time auth/download
- `templates/base/_document.html` — consent default + `pending-analytics-events`

---

## 3. GTM — Live Version

### 3.1. Базовый тег

| Параметр | Значение |
|----------|----------|
| Tag | Google tag — GA4 — PalinGames |
| Tag ID | `G-4EGSM0Z2SN` |
| Trigger | Initialization — All Pages |

### 3.2. GA4 Event tags (все в Live Version)

- GA4 Event — `page_view`
- GA4 Event — `view_item_list`
- GA4 Event — `view_item`
- GA4 Event — `add_to_cart`
- GA4 Event — `begin_checkout`
- GA4 Event — `sign_up`
- GA4 Event — `login`
- GA4 Event — `file_download_account`

Ecommerce: Custom Event triggers, данные из dataLayer.  
Auth/download: параметры через DLV.

### 3.3. Data Layer Variables

- `DLV - method`
- `DLV - file_name`
- `DLV - file_extension`
- `DLV - item_id`
- `DLV - item_name`
- `DLV - item_category`
- `DLV - item_variant`
- `DLV - download_type`
- `DLV - ecommerce`

### 3.4. Статус публикации

| Шаг | Статус |
|-----|--------|
| Теги + триггеры + DLV созданы | ✅ |
| Submit → Live Version | ✅ |
| События в GA4 Realtime без Preview | ✅ |

> **Примечание:** «Publish GTM» = Submit в интерфейсе tagmanager.google.com. Это отдельно от деплоя сайта: сайт шлёт `dataLayer`, GTM решает, что отправить в GA4. Оба слоя на prod работают.

### 3.5. Что не добавлять в GTM

- Клиентский `purchase`
- Клиентский `file_download_guest`
- Клиентский `file_download_custom_game`
- Дублирующий Google tag / второй GTM snippet
- GA4 Enhanced Measurement `file_download` как основной механизм

---

## 4. Payload reference

### Ecommerce

```json
// view_item
{
  "event": "view_item",
  "ecommerce": {
    "currency": "BYN",
    "value": 5.0,
    "items": [{
      "item_id": "123",
      "item_name": "Название",
      "item_category": "Категория",
      "item_variant": "Тип",
      "price": 5.0,
      "quantity": 1
    }]
  }
}
```

```json
// view_item_list
{
  "event": "view_item_list",
  "ecommerce": {
    "item_list_name": "Название списка",
    "items": [...]
  }
}
```

### Auth

```json
// sign_up — method: email | google | yandex
{ "event": "sign_up", "method": "email" }

// login — method: email | google | yandex
{ "event": "login", "method": "google" }
```

**Бизнес-логика:**
- `sign_up` email — после подтверждения email, не при submit формы
- `sign_up` OAuth — при первом входе; `login` в той же цепочке подавляется
- `login` — успешный email/password или повторный OAuth
- Reload не должен повторять one-time события

### Download

```json
// file_download_account — только скачивание из аккаунта
{
  "event": "file_download_account",
  "file_name": "Транспорт",
  "file_extension": "zip",
  "item_id": "42",
  "item_name": "Транспорт",
  "item_category": "Игры",
  "item_variant": "Набор",
  "download_type": "account"
}
```

Guest и custom game — только server MP.

---

## 5. Preconditions для smoke-test

| Проверка | Статус |
|----------|--------|
| `ANALYTICS_ENABLED=True`, `GTM_ID=GTM-5XBD94WD` на prod | ✅ |
| Инкогнито / чистые cookies | ✅ |
| GTM Preview / Tag Assistant **без VPN** | ✅ |
| Analytics cookies приняты | ✅ |
| GA4 Realtime открыт параллельно | ✅ |

---

## 6. Smoke-test checklist (клиентские события)

Критерий: событие в dataLayer → GTM-тег 1 раз → нет дублей → GA4 Realtime (без Preview).

| # | Событие | Сценарий | Статус |
|---|---------|----------|--------|
| 1 | `page_view` | Любая страница после consent | ✅ prod |
| 2 | `view_item_list` | Каталог / избранное / поиск | ✅ prod |
| 3 | `view_item` | `/products/<slug>/` | ✅ prod |
| 4 | `add_to_cart` | Кнопка «В корзину» | ✅ prod |
| 5 | `begin_checkout` | Страница checkout | ✅ prod |
| 6 | `sign_up` email | Регистрация → письмо → confirm → redirect | ⬜ осталось |
| 7 | `login` email | Logout → login email/password | ✅ prod |
| 8 | `sign_up` Google | Первый OAuth Google | ⬜ осталось |
| 9 | `login` Google | Повторный OAuth Google | ⬜ осталось |
| 10 | `sign_up` Yandex | Первый OAuth Yandex | ✅ prod |
| 11 | `login` Yandex | Повторный OAuth Yandex | ✅ prod |
| 12 | `file_download_account` | Аккаунт → купленный материал → Скачать | ✅ prod |

**Прогресс:** 9 / 12 клиентских сценариев проверены на prod.

### Negative checks

| # | Проверка | Статус |
|---|----------|--------|
| 1 | Неверный пароль → `login` не отправляется | ✅ prod |
| 2 | Reload после `sign_up` → повтора нет | ⬜ (для email; для Yandex — проверить при желании) |
| 3 | `sign_up` + `login` в одной регистрации → только `sign_up` | ⬜ для email/Google; для Yandex — ✅ при первом OAuth |
| 4 | Скачивание без consent → файл качается, события нет | ✅ prod |
| 5 | Один клик «Скачать» → одно событие | ✅ prod |

---

## 7. Server-side verification (Measurement Protocol)

Не проверяется через GTM Preview. Требует `GA4_MEASUREMENT_ID` + `GA4_API_SECRET`, `ANALYTICS_ENABLED=True`.

| Событие | Как проверить | Статус |
|---------|---------------|--------|
| `purchase` | Тестовая покупка → GA4 Realtime / Ecommerce reports; logs `analytics.mp.sent` | ⬜ |
| `file_download_guest` | Guest download link → GA4 Realtime; logs; consent на заказе | ⬜ |
| `file_download_custom_game` | Custom game token download → GA4 Realtime; logs | ⬜ |

Опционально: GA4 DebugView при `debug_mode` в MP payload.

---

## 8. Общий прогресс

```
GTM Live Version:        ████████████████  100%
Клиентские smoke-tests:  ████████████░░░░   75%  (9/12)
Negative checks:         ██████████░░░░░░   60%  (3/5)
Server-side MP:          ░░░░░░░░░░░░░░░░    0%  (0/3)
Яндекс Метрика:          ░░░░░░░░░░░░░░░░    0%  (отложено)
```

### Что осталось по GA4

1. **Клиентские auth-сценарии (3):** `sign_up` email, `sign_up` Google, `login` Google.
2. **Negative (опционально):** reload после `sign_up` email; цепочка sign_up без login для email/Google.
3. **Server-side MP (3):** `purchase` (реальная покупка), `file_download_guest`, `file_download_custom_game`.

Ecommerce-воронка и account download на prod работают. Клиентская GA4-аналитика **живая**.

---

## 9. Definition of Done — GA4

- [x] GTM Live Version с полным набором клиентских тегов
- [x] Ecommerce smoke-test на prod (1–5)
- [x] Yandex OAuth auth + account download на prod
- [x] Submit GTM, события в Realtime без Preview
- [ ] `sign_up` email, `sign_up` / `login` Google
- [ ] Server-side `purchase` (реальная покупка)
- [ ] Server-side `file_download_guest`
- [ ] Server-side `file_download_custom_game`

---

## 10. Яндекс Метрика (отдельная фаза, до Яндекс Директ)

### Контекст

- Купон Яндекс Директ: потратить 300 BYN → получить ещё 300 BYN бонусом.
- Купон действителен **до сентября 2026** (примерно).
- **Метрика не обязательна для активации купона**, но **настоятельно рекомендуется до запуска кампании** — иначе расход без отслеживания конверсий.

### Текущее состояние в проекте

| Компонент | Статус |
|-----------|--------|
| `YANDEX_METRIKA_ID` в settings / `.env` | ✅ заготовка |
| `yandex_metrika_id` в context processor | ✅ |
| Скрипт Метрики в шаблонах | ❌ |
| Загрузка после consent | ❌ |
| Цели / ecommerce в Метрике | ❌ |
| Привязка к Яндекс Директ | ❌ |

OAuth Yandex (`method=yandex` в GA4) — это авторизация, не аналитика.

### Рекомендуемый порядок работ

| # | Задача | Когда |
|---|--------|-------|
| 1 | Добить GA4: auth email/Google + server-side MP | Сейчас |
| 2 | Создать счётчик в Метрике, привязать к `palingames.by` | До запуска Директ |
| 3 | Подключить счётчик после analytics consent (как GTM) | До запуска Директ |
| 4 | Минимальные цели: `purchase`, `sign_up`, опционально `add_to_cart` | До запуска Директ |
| 5 | Привязать счётчик к аккаунту Яндекс Директ | Перед кампанией |
| 6 | Запустить кампанию, потратить 300 BYN по купону | После п.1–5 |

### MVP scope Метрики (не дублировать всю GA4-воронку)

- Счётчик + consent-gated загрузка
- 2–3 цели на ключевые конверсии
- Привязка к Директу для оптимизации и ремаркетинга
- Webvisor / полный ecommerce — позже, по необходимости

### Что не делать

- Не дублировать все 8 GA4-событий один в один
- Не грузить Метрику до consent
- Не менять consent-архитектуру без отдельной задачи
- Не запускать Директ «вслепую» без хотя бы базовых целей

### Решение

**Отложить** до завершения GA4 server-side проверок. Реализовать **в спокойном режиме до сентября**, до расхода купона.

---

## 11. Troubleshooting

| Симптом | Что проверить |
|---------|---------------|
| GTM Preview не видит контейнер | Отключить VPN |
| События не в dataLayer | Consent принят? `analytics.js` загружен? |
| `view_item` не срабатывал (исправлено) | `product-detail` → `product` mapping |
| Валюта `933` вместо `BYN` (исправлено) | `get_currency_code()` |
| Дубли `sign_up`/`login` | Session queue + suppress flag |
| Дубли download | `data-analytics-file-download-tracked` |
| MP-события не в Realtime | `GA4_API_SECRET`, `analytics.mp.sent`, consent на заказе |
| «Publish GTM» vs prod-сайт | Submit в GTM UI ≠ деплой Django; оба должны быть готовы |

---

## 12. Roadmap (сводка)

| Фаза | Содержание | Статус |
|------|------------|--------|
| **A. GA4 client** | GTM tags, smoke-test, Live Version | ✅ ~75%, 3 auth-сценария остались |
| **B. GA4 server** | MP: purchase, guest/custom download | ⬜ |
| **C. Яндекс Метрика** | Счётчик + цели + consent, до Директ | ⬜ до сентября |
| **D. Яндекс Директ** | Кампания, расход купона 300+300 BYN | ⬜ после фазы C |
