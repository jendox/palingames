from __future__ import annotations

import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.access.models import UserProductAccess
from apps.cart.models import Cart, CartItem
from apps.cart.services import SESSION_CART_KEY
from apps.favorites.models import Favorite
from apps.favorites.services import SESSION_FAVORITES_KEY
from apps.products.models import Category, Product, ProductImage, Review, ReviewStatus, SubType
from apps.products.services.home_popular import _reset_warnings
from apps.products.test_product_cards import parse_product_cards

LOCMEM_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "home-popular-page-tests",
    },
    "rate_limit": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "home-popular-page-tests-rate",
    },
}

DESKTOP_TEMPLATE = "pages/home/sections/01a_popular_products_desktop.html"
MOBILE_TEMPLATE = "pages/home/sections/01a_popular_products_mobile.html"
MOBILE_GRID_CLASSES = (
    "grid-cols-1 justify-items-center justify-center gap-x-[16px] gap-y-[16px] "
    "min-[388px]:grid-cols-[repeat(2,170px)]"
)
CLOUDS_DIVIDER_MARKER = "images/decor/clouds/clouds-group.svg"
SECTION_RE = re.compile(r"<section\b[^>]*>(?:(?!</section>).)*?Популярные материалы.*?</section>", re.S)


def popular_sections(html: str) -> list[str]:
    return SECTION_RE.findall(html)


@override_settings(CACHES=LOCMEM_CACHES)
class HomePopularPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.reviewer = user_model.objects.create_user(email="home-reviewer@example.com", password="test-pass-123")
        cls.category = Category.objects.create(title="Развивающие игры", slug="home-popular-category")
        cls.subtype = SubType.objects.create(title="Лото домашнее")
        cls.products = []
        for index in range(6):
            product = Product.objects.create(
                title=f"Популярный материал {index}",
                slug=f"home-popular-{index}",
                price=Decimal("9.00"),
                is_published=True,
            )
            product.categories.add(cls.category)
            product.subtypes.add(cls.subtype)
            ProductImage.objects.create(
                product=product,
                image=f"previews/home-popular-{index}/source.png",
                order=0,
                width=800,
                height=936,
                thumb_400_key=f"previews/home-popular-{index}/source_w400.webp",
                thumb_800_key=f"previews/home-popular-{index}/source_w800.webp",
                thumb_400_display_width=400,
                thumb_800_display_width=800,
                thumbs_source_key=f"previews/home-popular-{index}/source.png",
            )
            Review.objects.create(
                product=product,
                user=cls.reviewer,
                rating=5,
                comment="Отлично",
                status=ReviewStatus.PUBLISHED,
            )
            cls.products.append(product)

    def setUp(self):
        cache.clear()
        _reset_warnings()

    def test_block_renders_desktop_and_mobile_markup(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, DESKTOP_TEMPLATE)
        self.assertTemplateUsed(response, MOBILE_TEMPLATE)
        self.assertEqual(len(response.context["home_popular_products"]), 6)

        cards = parse_product_cards(response.content.decode())
        self.assertEqual(len([card for card in cards if card["variant"] == "desktop"]), 6)
        self.assertEqual(len([card for card in cards if card["variant"] == "mobile"]), 6)

    def test_block_is_placed_between_hero_and_why_choose(self):
        response = self.client.get(reverse("home"))
        html = response.content.decode()

        popular_index = html.index("Популярные материалы")
        self.assertLess(html.index("Обучающие игры"), popular_index)
        self.assertLess(popular_index, html.index("Почему выбирают нас"))

    def test_mobile_grid_and_catalog_link(self):
        response = self.client.get(reverse("home"))
        sections = popular_sections(response.content.decode())

        self.assertEqual(len(sections), 2)
        mobile_section = next(section for section in sections if "max-w-[420px]" in section)
        self.assertIn(MOBILE_GRID_CLASSES, mobile_section)
        for section in sections:
            with self.subTest(section=section[:60]):
                self.assertIn(f'href="{reverse("catalog")}"', section)
                self.assertIn("Весь каталог", section)
                self.assertNotIn("→", section)

    def test_block_images_are_lazy_without_fetchpriority(self):
        response = self.client.get(reverse("home"))
        cards = parse_product_cards(response.content.decode())

        self.assertEqual(len(cards), 12)
        for card in cards:
            with self.subTest(variant=card["variant"], item_id=card["item_id"]):
                self.assertEqual(card["img"]["loading"], "lazy")
                self.assertIsNone(card["img"]["fetchpriority"])
                self.assertTrue(card["img"]["srcset"])
        for section in popular_sections(response.content.decode()):
            self.assertNotIn("fetchpriority", section)

    def test_catalog_scripts_are_included_with_block(self):
        response = self.client.get(reverse("home"))

        self.assertContains(response, "js/catalog-filters.js")
        self.assertContains(response, "js/vendor/htmx.min.js")

    def test_empty_storefront_hides_block_and_scripts(self):
        Product.objects.update(is_published=False)

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["home_popular_products"], [])
        self.assertNotContains(response, "Популярные материалы")
        self.assertNotContains(response, "js/catalog-filters.js")
        self.assertTemplateNotUsed(response, DESKTOP_TEMPLATE)
        self.assertTemplateNotUsed(response, MOBILE_TEMPLATE)

    def test_empty_storefront_adds_no_extra_cloud_divider(self):
        with_block = self.client.get(reverse("home")).content.decode()
        Product.objects.update(is_published=False)
        cache.clear()
        without_block = self.client.get(reverse("home")).content.decode()

        self.assertEqual(without_block.count(CLOUDS_DIVIDER_MARKER), with_block.count(CLOUDS_DIVIDER_MARKER) - 1)

    def _home_queries(self, *, limit: str, user=None) -> int:
        if user is not None:
            self.client.force_login(user)
        with override_settings(HOME_POPULAR_PRODUCTS_LIMIT=limit):
            cache.clear()
            warm = self.client.get(reverse("home"))
            self.assertEqual(len(warm.context["home_popular_products"]), int(limit))
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(reverse("home"))
            self.assertEqual(len(response.context["home_popular_products"]), int(limit))
        return len(queries)

    def test_query_count_does_not_depend_on_card_count(self):
        user = get_user_model().objects.create_user(email="home-queries@example.com", password="test-pass-123")
        cart = Cart.objects.create(user=user)
        CartItem.objects.create(cart=cart, product=self.products[0])
        Favorite.objects.create(user=user, product=self.products[1])
        UserProductAccess.objects.create(user=user, product=self.products[2], order=None)

        guest_two = self._home_queries(limit="2")
        guest_six = self._home_queries(limit="6")
        user_two = self._home_queries(limit="2", user=user)
        user_six = self._home_queries(limit="6", user=user)

        self.assertEqual(guest_two, guest_six)
        self.assertEqual(user_two, user_six)
        self.assertLessEqual(user_six, 12)


@override_settings(CACHES=LOCMEM_CACHES)
class HomePopularUserStateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user_a = user_model.objects.create_user(email="home-a@example.com", password="test-pass-123")
        cls.user_b = user_model.objects.create_user(email="home-b@example.com", password="test-pass-123")
        cls.purchased = Product.objects.create(
            title="Купленный A", slug="home-state-purchased", price=Decimal("5.00"), is_published=True,
        )
        cls.in_cart = Product.objects.create(
            title="В корзине A", slug="home-state-cart", price=Decimal("6.00"), is_published=True,
        )
        cls.favorite = Product.objects.create(
            title="Избранное A", slug="home-state-favorite", price=Decimal("7.00"), is_published=True,
        )
        cart = Cart.objects.create(user=cls.user_a)
        CartItem.objects.create(cart=cart, product=cls.purchased)
        CartItem.objects.create(cart=cart, product=cls.in_cart)
        Favorite.objects.create(user=cls.user_a, product=cls.favorite)
        UserProductAccess.objects.create(user=cls.user_a, product=cls.purchased, order=None)

    def setUp(self):
        cache.clear()

    def _cards(self, response) -> dict[int, dict]:
        return {card["id"]: card for card in response.context["home_popular_products"]}

    def test_user_states_do_not_leak_through_warm_cache(self):
        self.client.force_login(self.user_a)
        cards_a = self._cards(self.client.get(reverse("home")))

        self.assertTrue(cards_a[self.purchased.pk]["is_purchased"])
        self.assertEqual(
            cards_a[self.purchased.pk]["download_url"],
            reverse("product-download", kwargs={"product_id": self.purchased.pk}),
        )
        self.assertFalse(cards_a[self.purchased.pk]["is_in_cart"])
        self.assertTrue(cards_a[self.in_cart.pk]["is_in_cart"])
        self.assertTrue(cards_a[self.favorite.pk]["is_favorited"])

        self.client.logout()
        self.client.force_login(self.user_b)
        cards_b = self._cards(self.client.get(reverse("home")))

        self.client.logout()
        cards_guest = self._cards(self.client.get(reverse("home")))

        for label, cards in (("user_b", cards_b), ("guest", cards_guest)):
            with self.subTest(viewer=label):
                self.assertEqual(set(cards), set(cards_a))
                for card in cards.values():
                    self.assertFalse(card["is_purchased"])
                    self.assertEqual(card["download_url"], "")
                    self.assertFalse(card["is_in_cart"])
                    self.assertFalse(card["is_favorited"])

    def test_guest_session_state_is_shown(self):
        session = self.client.session
        session[SESSION_CART_KEY] = [self.in_cart.pk]
        session[SESSION_FAVORITES_KEY] = [self.favorite.pk]
        session.save()

        cards = self._cards(self.client.get(reverse("home")))

        self.assertTrue(cards[self.in_cart.pk]["is_in_cart"])
        self.assertTrue(cards[self.favorite.pk]["is_favorited"])
        self.assertFalse(cards[self.purchased.pk]["is_in_cart"])

    def test_purchased_card_renders_download_link_instead_of_cart_button(self):
        self.client.force_login(self.user_a)
        response = self.client.get(reverse("home"))
        cards = parse_product_cards(response.content.decode())

        purchased_cards = [card for card in cards if card["item_id"] == str(self.purchased.pk)]
        self.assertEqual(len(purchased_cards), 2)
        for card in purchased_cards:
            self.assertTrue(card["download_link"])
            self.assertIsNone(card["in_cart"])
