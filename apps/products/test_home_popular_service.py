from __future__ import annotations

import itertools
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.orders.models import Order, OrderItem
from apps.products.models import Product
from apps.products.services import home_popular
from apps.products.services.home_popular import (
    POPULAR_ALGORITHM_VERSION,
    PopularSettings,
    _reset_warnings,
    compute_popular_product_ids,
    get_popular_cache_key,
    get_popular_product_ids,
    get_popular_products,
    get_popular_settings,
)

LOCMEM_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "home-popular-service-tests",
    },
    "rate_limit": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "home-popular-service-tests-rate",
    },
}

FIXED_NOW = timezone.now().replace(microsecond=0)
DEFAULTS = PopularSettings(limit=6, window_days=30, min_orders=2, cache_ttl=3600)


class PopularTestMixin:
    _counter = itertools.count(1)

    def setUp(self):
        super().setUp()
        cache.clear()
        _reset_warnings()
        now_patcher = patch.object(home_popular.timezone, "now", return_value=FIXED_NOW)
        now_patcher.start()
        self.addCleanup(now_patcher.stop)

    def make_product(self, title: str, *, published: bool = True, created_days_ago: float = 100) -> Product:
        index = next(self._counter)
        product = Product.objects.create(
            title=f"{title} {index}",
            slug=f"popular-{index}",
            price=Decimal("5.00"),
            is_published=published,
        )
        Product.objects.filter(pk=product.pk).update(created_at=FIXED_NOW - timedelta(days=created_days_ago))
        return product

    def make_order(
        self,
        *products: Product,
        days_ago: float = 1,
        status: str = Order.OrderStatus.PAID,
        source: str = Order.Source.PALINGAMES,
        quantity: int = 1,
    ) -> Order:
        index = next(self._counter)
        order = Order.objects.create(
            email=f"buyer-{index}@example.com",
            checkout_type=Order.CheckoutType.GUEST,
            status=status,
            source=source,
            paid_at=FIXED_NOW - timedelta(days=days_ago),
            items_count=len(products),
        )
        for product in products:
            OrderItem.objects.create(
                order=order,
                product=product,
                title_snapshot=product.title,
                category_snapshot="",
                unit_price_amount=product.price,
                quantity=quantity,
                line_total_amount=product.price * quantity,
            )
        return order


@override_settings(CACHES=LOCMEM_CACHES)
class PopularRankingTests(PopularTestMixin, TestCase):
    def test_only_paid_orders_are_counted(self):
        paid = self.make_product("Оплаченный")
        self.make_order(paid)
        self.make_order(paid)
        not_paid = self.make_product("Неоплаченный", created_days_ago=200)
        for status in (
            Order.OrderStatus.CREATED,
            Order.OrderStatus.WAITING_FOR_PAYMENT,
            Order.OrderStatus.CANCELED,
            Order.OrderStatus.REFUNDED,
            Order.OrderStatus.FAILED,
        ):
            self.make_order(not_paid, status=status)
            self.make_order(not_paid, status=status)
        cfg = PopularSettings(limit=1, window_days=30, min_orders=2, cache_ttl=3600)

        self.assertEqual(compute_popular_product_ids(cfg), [paid.pk])

    def test_paid_order_sources_are_counted_equally(self):
        mixed = self.make_product("PG+IG")
        self.make_order(mixed, source=Order.Source.PALINGAMES, days_ago=5)
        self.make_order(mixed, source=Order.Source.INSTAGRAM, days_ago=4)
        telegram = self.make_product("TG")
        self.make_order(telegram, source=Order.Source.TELEGRAM, days_ago=1)
        self.make_order(telegram, source=Order.Source.TELEGRAM, days_ago=2)
        single = self.make_product("Один заказ")
        self.make_order(single, days_ago=0.5)
        cfg = PopularSettings(limit=3, window_days=30, min_orders=2, cache_ttl=3600)

        # Both multi-order products are tier 1 (2 orders each); fresher last_paid_at first.
        self.assertEqual(compute_popular_product_ids(cfg), [telegram.pk, mixed.pk, single.pk])

    def test_window_excludes_orders_older_than_window(self):
        recent = self.make_product("29 дней")
        self.make_order(recent, days_ago=29)
        stale = self.make_product("31 день", created_days_ago=300)
        self.make_order(stale, days_ago=31)
        self.make_order(stale, days_ago=31)
        cfg = PopularSettings(limit=1, window_days=30, min_orders=2, cache_ttl=3600)

        self.assertEqual(compute_popular_product_ids(cfg), [recent.pk])

    def test_rank_counts_distinct_orders_not_quantity(self):
        bulk = self.make_product("Количество 5")
        self.make_order(bulk, quantity=5, days_ago=1)
        two_orders = self.make_product("Два заказа")
        self.make_order(two_orders, days_ago=10)
        self.make_order(two_orders, days_ago=11)
        cfg = PopularSettings(limit=2, window_days=30, min_orders=2, cache_ttl=3600)

        self.assertEqual(compute_popular_product_ids(cfg), [two_orders.pk, bulk.pk])

    def test_tier_1_sorted_by_orders_then_last_paid_then_id(self):
        three = self.make_product("Три")
        for days in (20, 21, 22):
            self.make_order(three, days_ago=days)
        two_old = self.make_product("Два старых")
        self.make_order(two_old, days_ago=9)
        self.make_order(two_old, days_ago=10)
        two_fresh = self.make_product("Два свежих")
        self.make_order(two_fresh, days_ago=2)
        self.make_order(two_fresh, days_ago=10)
        tie_low = self.make_product("Ничья меньший id")
        tie_high = self.make_product("Ничья больший id")
        shared_1 = self.make_order(tie_low, tie_high, days_ago=15)
        shared_2 = self.make_order(tie_low, tie_high, days_ago=16)
        self.assertLess(tie_low.pk, tie_high.pk)
        self.assertTrue(shared_1.pk and shared_2.pk)
        cfg = PopularSettings(limit=5, window_days=30, min_orders=2, cache_ttl=3600)

        self.assertEqual(
            compute_popular_product_ids(cfg),
            [three.pk, two_fresh.pk, two_old.pk, tie_high.pk, tie_low.pk],
        )

    def test_tier_2_single_order_products_follow_tier_1_by_freshness_then_id(self):
        top = self.make_product("Топ")
        self.make_order(top, days_ago=25)
        self.make_order(top, days_ago=26)
        single_old = self.make_product("Один старый")
        self.make_order(single_old, days_ago=10)
        single_fresh = self.make_product("Один свежий")
        self.make_order(single_fresh, days_ago=1)
        tie_low = self.make_product("Один ничья A")
        tie_high = self.make_product("Один ничья B")
        self.make_order(tie_low, tie_high, days_ago=5)
        cfg = PopularSettings(limit=5, window_days=30, min_orders=2, cache_ttl=3600)

        self.assertEqual(
            compute_popular_product_ids(cfg),
            [top.pk, single_fresh.pk, tie_high.pk, tie_low.pk, single_old.pk],
        )

    @override_settings(HOME_POPULAR_PRODUCTS_MIN_ORDERS="3")
    def test_min_orders_3_moves_two_order_products_to_tier_2(self):
        three = self.make_product("Три заказа")
        for days in (20, 21, 22):
            self.make_order(three, days_ago=days)
        two = self.make_product("Два заказа")
        self.make_order(two, days_ago=15)
        self.make_order(two, days_ago=16)
        one_fresh = self.make_product("Один свежий")
        self.make_order(one_fresh, days_ago=1)
        one_old = self.make_product("Один старый")
        self.make_order(one_old, days_ago=8)
        one_tie_low = self.make_product("Один ничья A")
        one_tie_high = self.make_product("Один ничья B")
        self.make_order(one_tie_low, one_tie_high, days_ago=3)
        newest = self.make_product("Новинка", created_days_ago=0.1)

        cfg = get_popular_settings()
        self.assertEqual(cfg.min_orders, 3)
        cfg = PopularSettings(limit=7, window_days=cfg.window_days, min_orders=cfg.min_orders, cache_ttl=cfg.cache_ttl)

        # Two-order product beats one-order products even though their paid_at is fresher;
        # equal order counts fall back to the freshest paid_at, then the larger id.
        self.assertEqual(
            compute_popular_product_ids(cfg),
            [three.pk, two.pk, one_fresh.pk, one_tie_high.pk, one_tie_low.pk, one_old.pk, newest.pk],
        )

    def test_tier_3_fills_with_newest_without_duplicates(self):
        sold_twice = self.make_product("Продан дважды", created_days_ago=1)
        self.make_order(sold_twice, days_ago=3)
        self.make_order(sold_twice, days_ago=4)
        sold_once = self.make_product("Продан раз", created_days_ago=2)
        self.make_order(sold_once, days_ago=3)
        newest = [self.make_product(f"Новинка {n}", created_days_ago=10 + n) for n in range(6)]

        result = compute_popular_product_ids(DEFAULTS)

        self.assertEqual(result, [sold_twice.pk, sold_once.pk, *[p.pk for p in newest[:4]]])
        self.assertEqual(len(result), len(set(result)))
        self.assertLessEqual(len(result), DEFAULTS.limit)

    def test_unpublished_products_are_excluded_in_every_tier(self):
        hidden_tier_1 = self.make_product("Скрытый топ", published=False)
        self.make_order(hidden_tier_1)
        self.make_order(hidden_tier_1)
        hidden_tier_2 = self.make_product("Скрытый один", published=False)
        self.make_order(hidden_tier_2)
        self.make_product("Скрытая новинка", published=False, created_days_ago=0)
        visible = self.make_product("Видимый", created_days_ago=50)

        self.assertEqual(compute_popular_product_ids(DEFAULTS), [visible.pk])

    def test_product_unpublished_after_caching_is_dropped(self):
        first = self.make_product("Первый")
        self.make_order(first)
        self.make_order(first)
        second = self.make_product("Второй")
        self.make_order(second)

        self.assertEqual([p.pk for p in get_popular_products()], [first.pk, second.pk])
        Product.objects.filter(pk=first.pk).update(is_published=False)

        self.assertEqual(get_popular_product_ids(), [first.pk, second.pk])
        self.assertEqual([p.pk for p in get_popular_products()], [second.pk])

    def test_no_published_products_returns_empty_list(self):
        self.make_product("Черновик", published=False)

        self.assertEqual(compute_popular_product_ids(DEFAULTS), [])
        self.assertEqual(get_popular_products(), [])

    def test_result_never_exceeds_limit(self):
        for index in range(8):
            product = self.make_product(f"Товар {index}")
            self.make_order(product)
            self.make_order(product)
        cfg = PopularSettings(limit=3, window_days=30, min_orders=2, cache_ttl=3600)

        self.assertEqual(len(compute_popular_product_ids(cfg)), 3)

    def test_get_popular_products_keeps_ranking_order(self):
        low = self.make_product("Низ")
        self.make_order(low, days_ago=9)
        self.make_order(low, days_ago=9)
        high = self.make_product("Верх")
        for _ in range(3):
            self.make_order(high, days_ago=2)

        products = get_popular_products()

        self.assertEqual([p.pk for p in products][:2], [high.pk, low.pk])


@override_settings(CACHES=LOCMEM_CACHES)
class PopularCacheTests(PopularTestMixin, TestCase):
    def test_default_cache_key_includes_version_and_params(self):
        self.assertEqual(POPULAR_ALGORITHM_VERSION, 1)
        self.assertEqual(get_popular_cache_key(get_popular_settings()), "home_popular:v1:30:6:2")

    @override_settings(
        HOME_POPULAR_PRODUCTS_LIMIT="abc",
        HOME_POPULAR_PRODUCTS_WINDOW_DAYS="0",
        HOME_POPULAR_PRODUCTS_MIN_ORDERS="1",
    )
    def test_invalid_settings_fall_back_to_default_key(self):
        with self.assertLogs("apps.products", "WARNING"):
            cfg = get_popular_settings()
        self.assertEqual(get_popular_cache_key(cfg), "home_popular:v1:30:6:2")

    def test_second_call_hits_cache_without_aggregate_query(self):
        product = self.make_product("Кэш")
        self.make_order(product)
        self.make_order(product)

        first = get_popular_product_ids()
        with self.assertNumQueries(0):
            second = get_popular_product_ids()

        self.assertEqual(first, second)
        self.assertEqual(cache.get("home_popular:v1:30:6:2"), first)

    def test_empty_list_is_cached(self):
        self.assertEqual(get_popular_product_ids(), [])
        self.assertEqual(cache.get("home_popular:v1:30:6:2"), [])

        self.make_product("Появился позже")
        with self.assertNumQueries(0):
            self.assertEqual(get_popular_product_ids(), [])

    def test_changed_settings_use_new_key_and_recompute(self):
        old = self.make_product("Старый топ")
        self.make_order(old, days_ago=20)
        self.make_order(old, days_ago=21)
        recent = self.make_product("Свежий", created_days_ago=200)
        self.make_order(recent, days_ago=1)

        default_ids = get_popular_product_ids()
        self.assertEqual(default_ids[:2], [old.pk, recent.pk])

        with override_settings(HOME_POPULAR_PRODUCTS_WINDOW_DAYS="7"):
            with CaptureQueriesContext(connection) as queries:
                week_ids = get_popular_product_ids()
            self.assertGreater(len(queries), 0)
            self.assertEqual(week_ids[0], recent.pk)
            self.assertEqual(cache.get("home_popular:v1:7:6:2"), week_ids)

        with override_settings(HOME_POPULAR_PRODUCTS_LIMIT="2"):
            self.assertEqual(get_popular_product_ids(), [old.pk, recent.pk])
            self.assertIsNotNone(cache.get("home_popular:v1:30:2:2"))

        with override_settings(HOME_POPULAR_PRODUCTS_MIN_ORDERS="3"):
            get_popular_product_ids()
            self.assertIsNotNone(cache.get("home_popular:v1:30:6:3"))

        self.assertEqual(cache.get("home_popular:v1:30:6:2"), default_ids)

    def test_cache_value_is_list_of_ints(self):
        for index in range(3):
            product = self.make_product(f"Тип {index}")
            self.make_order(product)

        get_popular_product_ids()
        value = cache.get("home_popular:v1:30:6:2")

        self.assertIsInstance(value, list)
        self.assertTrue(value)
        self.assertTrue(all(type(item) is int for item in value))

    def test_cache_outage_falls_back_to_computing(self):
        product = self.make_product("Без кэша")
        self.make_order(product)

        with (
            patch.object(home_popular.cache, "get", side_effect=ConnectionError("redis down")),
            patch.object(home_popular.cache, "set", side_effect=ConnectionError("redis down")),
            self.assertLogs("apps.products", "WARNING"),
        ):
            self.assertEqual(get_popular_product_ids(), [product.pk])


SETTING_NAMES = {
    "limit": "HOME_POPULAR_PRODUCTS_LIMIT",
    "window_days": "HOME_POPULAR_PRODUCTS_WINDOW_DAYS",
    "min_orders": "HOME_POPULAR_PRODUCTS_MIN_ORDERS",
    "cache_ttl": "HOME_POPULAR_PRODUCTS_CACHE_TTL",
}
SETTING_DEFAULTS = {"limit": 6, "window_days": 30, "min_orders": 2, "cache_ttl": 3600}


class PopularSettingsParsingTests(TestCase):
    def setUp(self):
        _reset_warnings()

    def _assert_parsed(self, attr: str, raw, expected: int, *, warns: bool):
        name = SETTING_NAMES[attr]
        with self.subTest(setting=name, raw=raw), override_settings(**{name: raw}):
            _reset_warnings()
            if warns:
                with self.assertLogs("apps.products", "WARNING") as logs:
                    value = getattr(get_popular_settings(), attr)
                self.assertIn(name, "\n".join(logs.output))
            else:
                with self.assertNoLogs("apps.products", "WARNING"):
                    value = getattr(get_popular_settings(), attr)
            self.assertEqual(value, expected)

    def test_valid_values_are_parsed_without_warning(self):
        for attr in SETTING_NAMES:
            valid = {"limit": 12, "window_days": 12, "min_orders": 12, "cache_ttl": 120}[attr]
            self._assert_parsed(attr, str(valid), valid, warns=False)
            self._assert_parsed(attr, f" {valid} ", valid, warns=False)
            self._assert_parsed(attr, f"+{valid}", valid, warns=False)
            self._assert_parsed(attr, valid, valid, warns=False)

    def test_spec_examples_are_parsed_without_warning(self):
        self._assert_parsed("limit", "6", 6, warns=False)
        self._assert_parsed("limit", " 12 ", 12, warns=False)
        self._assert_parsed("limit", "+7", 7, warns=False)
        self._assert_parsed("limit", 7, 7, warns=False)

    def test_unset_values_use_default_without_warning(self):
        for attr, default in SETTING_DEFAULTS.items():
            for raw in (None, "", "   "):
                self._assert_parsed(attr, raw, default, warns=False)

    def test_missing_setting_uses_default_without_warning(self):
        name = SETTING_NAMES["limit"]
        with self.settings():
            delattr(settings, name)
            with self.assertNoLogs("apps.products", "WARNING"):
                self.assertEqual(get_popular_settings().limit, 6)

    def test_invalid_values_use_default_with_warning(self):
        for attr, default in SETTING_DEFAULTS.items():
            for raw in ("abc", "6.5", "1e3", True, 6.0, "-1", "0"):
                self._assert_parsed(attr, raw, default, warns=True)

    def test_out_of_range_values_use_default_with_warning(self):
        cases = (
            ("limit", "25"),
            ("window_days", "366"),
            ("min_orders", "1"),
            ("min_orders", "101"),
            ("cache_ttl", "59"),
            ("cache_ttl", "86401"),
        )
        for attr, raw in cases:
            self._assert_parsed(attr, raw, SETTING_DEFAULTS[attr], warns=True)

    def test_boundary_values_are_accepted(self):
        cases = (
            ("limit", "1", 1),
            ("limit", "24", 24),
            ("window_days", "1", 1),
            ("window_days", "365", 365),
            ("min_orders", "2", 2),
            ("min_orders", "100", 100),
            ("cache_ttl", "60", 60),
            ("cache_ttl", "86400", 86400),
        )
        for attr, raw, expected in cases:
            self._assert_parsed(attr, raw, expected, warns=False)

    @override_settings(HOME_POPULAR_PRODUCTS_LIMIT="abc")
    def test_warning_is_deduplicated_until_reset(self):
        with self.assertLogs("apps.products", "WARNING") as logs:
            get_popular_settings()
        self.assertEqual(len(logs.output), 1)
        self.assertIn("HOME_POPULAR_PRODUCTS_LIMIT", logs.output[0])

        with self.assertNoLogs("apps.products", "WARNING"):
            get_popular_settings()

        _reset_warnings()
        with self.assertLogs("apps.products", "WARNING") as logs:
            get_popular_settings()
        self.assertEqual(len(logs.output), 1)

    def test_settings_module_keeps_raw_strings(self):
        for name in SETTING_NAMES.values():
            with self.subTest(setting=name):
                self.assertIsInstance(getattr(settings, name), str)

    def test_settings_module_uses_env_str_for_popular_settings(self):
        from pathlib import Path

        source = (Path(settings.BASE_DIR) / "config" / "settings.py").read_text(encoding="utf-8")
        for name in SETTING_NAMES.values():
            with self.subTest(setting=name):
                self.assertIn(f'{name} = env.str("{name}"', source)

    def test_settings_import_does_not_fail_on_invalid_env(self):
        import os
        import subprocess
        import sys

        env = {**os.environ, "HOME_POPULAR_PRODUCTS_LIMIT": "abc", "DJANGO_SETTINGS_MODULE": "config.settings"}
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from django.conf import settings; print(repr(settings.HOME_POPULAR_PRODUCTS_LIMIT))",
            ],
            cwd=settings.BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("'abc'", result.stdout)
