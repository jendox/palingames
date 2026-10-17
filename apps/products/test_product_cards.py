from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.access.models import UserProductAccess
from apps.cart.models import Cart, CartItem
from apps.favorites.models import Favorite
from apps.products.models import Category, Product, ProductFile, ProductImage, Review, ReviewStatus, SubType
from apps.products.services.reviews import get_product_rating_summaries
from apps.products.views import AlphabetNavigatorView, CatalogView

PLACEHOLDER_URL = "/static/images/example-product-image-1.png"


class _CardArticleParser(HTMLParser):
    """Collects key attributes of every product card <article> in a page."""

    def __init__(self):
        super().__init__()
        self.cards: list[dict] = []
        self._current: dict | None = None
        self._template_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "template":
            self._template_depth += 1
            return
        if self._template_depth:
            return
        if tag == "article" and "data-analytics-item" in attrs:
            classes = attrs.get("class", "")
            self._current = {
                "variant": "mobile" if "w-[170px]" in classes else "desktop",
                "item_id": attrs.get("data-analytics-item-id"),
                "item_name": attrs.get("data-analytics-item-name"),
                "item_category": attrs.get("data-analytics-item-category"),
                "item_variant": attrs.get("data-analytics-item-variant"),
                "price": attrs.get("data-analytics-price"),
                "currency": attrs.get("data-analytics-currency"),
                "href": None,
                "img": None,
                "favorited": None,
                "in_cart": None,
                "download_link": False,
            }
            return
        card = self._current
        if card is None:
            return
        if tag == "a" and card["href"] is None:
            card["href"] = attrs.get("href")
        if tag == "img" and attrs.get("decoding") == "async" and card["img"] is None:
            card["img"] = {
                "src": attrs.get("src"),
                "srcset": attrs.get("srcset"),
                "sizes": attrs.get("sizes"),
                "loading": attrs.get("loading"),
                "fetchpriority": attrs.get("fetchpriority"),
            }
        if "data-favorited" in attrs:
            card["favorited"] = attrs["data-favorited"]
        if "data-in-cart" in attrs:
            card["in_cart"] = attrs["data-in-cart"]
        if "data-product-download-link" in attrs:
            card["download_link"] = True

    def handle_endtag(self, tag):
        if tag == "template" and self._template_depth:
            self._template_depth -= 1
            return
        if tag == "article" and self._current is not None:
            self.cards.append(self._current)
            self._current = None


def parse_product_cards(html: str) -> list[dict]:
    parser = _CardArticleParser()
    parser.feed(html)
    return parser.cards


class ProductCardFixtureMixin:
    """Fixed catalog dataset: rated/unrated, with/without image, purchased/in cart/favorited."""

    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(email="cards-a@example.com", password="test-pass-123")
        reviewer_1 = user_model.objects.create_user(email="cards-r1@example.com", password="test-pass-123")
        reviewer_2 = user_model.objects.create_user(email="cards-r2@example.com", password="test-pass-123")

        cls.category = Category.objects.create(title="Дидактические игры", slug="cards-didactic")
        cls.subtype = SubType.objects.create(title="Карточки снимок")

        base_time = timezone.now() - timedelta(days=10)

        # Purchased + in cart + favorited, rated, image with fresh WebP thumbs and an active file.
        cls.azbuka = Product.objects.create(
            title="Азбука", slug="cards-azbuka", price=Decimal("12.50"), is_published=True,
        )
        cls.azbuka.categories.add(cls.category)
        cls.azbuka.subtypes.add(cls.subtype)
        cls.azbuka_image = ProductImage.objects.create(
            product=cls.azbuka,
            image="previews/cards-azbuka/source.png",
            order=0,
            width=800,
            height=936,
            thumb_400_key="previews/cards-azbuka/source_w400.webp",
            thumb_800_key="previews/cards-azbuka/source_w800.webp",
            thumb_400_display_width=400,
            thumb_800_display_width=800,
            thumbs_source_key="previews/cards-azbuka/source.png",
        )
        ProductFile.objects.create(
            product=cls.azbuka,
            file_key="products/cards-azbuka/azbuka.pdf",
            original_filename="azbuka.pdf",
            is_active=True,
        )
        Review.objects.create(
            product=cls.azbuka, user=reviewer_1, rating=5, comment="Отлично", status=ReviewStatus.PUBLISHED,
        )
        Review.objects.create(
            product=cls.azbuka, user=reviewer_2, rating=4, comment="Хорошо", status=ReviewStatus.PUBLISHED,
        )

        # In cart + favorited, no image, no rating, no subtype.
        cls.arbuz = Product.objects.create(title="Арбуз", slug="cards-arbuz", price=Decimal("10.00"), is_published=True)
        cls.arbuz.categories.add(cls.category)

        # No user state, rated, image without thumbs.
        cls.aist = Product.objects.create(title="Аист", slug="cards-aist", price=Decimal("7.00"), is_published=True)
        cls.aist.categories.add(cls.category)
        cls.aist.subtypes.add(cls.subtype)
        cls.aist_image = ProductImage.objects.create(product=cls.aist, image="previews/cards-aist/aist.png", order=0)
        Review.objects.create(
            product=cls.aist, user=reviewer_1, rating=3, comment="Нормально", status=ReviewStatus.PUBLISHED,
        )
        Review.objects.create(
            product=cls.aist, user=reviewer_2, rating=1, comment="На модерации", status=ReviewStatus.PENDING,
        )

        for offset, product in enumerate((cls.azbuka, cls.arbuz, cls.aist)):
            Product.objects.filter(pk=product.pk).update(created_at=base_time + timedelta(hours=offset))

        cart = Cart.objects.create(user=cls.user)
        CartItem.objects.create(cart=cart, product=cls.azbuka)
        CartItem.objects.create(cart=cart, product=cls.arbuz)
        Favorite.objects.create(user=cls.user, product=cls.azbuka)
        Favorite.objects.create(user=cls.user, product=cls.arbuz)
        UserProductAccess.objects.create(user=cls.user, product=cls.azbuka, order=None)

    def expected_card(self, product, *, category_label: str) -> dict:
        """Saved snapshot of the card dict (storage-derived image URLs come from the image row)."""
        image_fields = {
            "image_url": PLACEHOLDER_URL,
            "image_thumb_url": PLACEHOLDER_URL,
            "image_srcset": "",
            "image_width": None,
            "image_height": None,
        }
        if product.pk == self.azbuka.pk:
            image = self.azbuka_image
            thumb_400 = image.thumb_url(400)
            thumb_800 = image.thumb_url(800)
            image_fields = {
                "image_url": image.image.url,
                "image_thumb_url": thumb_400,
                "image_srcset": f"{thumb_400} 400w, {thumb_800} 800w",
                "image_width": 800,
                "image_height": 936,
            }
            return {
                "id": product.pk,
                "title": "Азбука",
                "url": "/products/cards-azbuka/",
                "price": "12,50 BYN",
                "price_value": 12.5,
                "currency": "BYN",
                "kind": "Карточки снимок",
                "category": category_label,
                "content": "",
                "rating": "4,5",
                "reviews_count": 2,
                "has_rating": True,
                "is_favorited": True,
                "is_in_cart": False,
                "is_purchased": True,
                "download_url": f"/products/{product.pk}/download/",
                "download_analytics": {
                    "event": "file_download_account",
                    "file_name": "Азбука",
                    "file_extension": "pdf",
                    "item_id": str(product.pk),
                    "item_name": "Азбука",
                    "item_category": "Дидактические игры",
                    "item_variant": "Карточки снимок",
                    "download_type": "account",
                },
                **image_fields,
            }
        if product.pk == self.arbuz.pk:
            return {
                "id": product.pk,
                "title": "Арбуз",
                "url": "/products/cards-arbuz/",
                "price": "10,00 BYN",
                "price_value": 10.0,
                "currency": "BYN",
                "kind": "Дидактические игры",
                "category": category_label,
                "content": "",
                "rating": "",
                "reviews_count": 0,
                "has_rating": False,
                "is_favorited": True,
                "is_in_cart": True,
                "is_purchased": False,
                "download_url": "",
                "download_analytics": None,
                **image_fields,
            }
        image_fields = {
            "image_url": self.aist_image.image.url,
            "image_thumb_url": self.aist_image.image.url,
            "image_srcset": "",
            "image_width": None,
            "image_height": None,
        }
        return {
            "id": product.pk,
            "title": "Аист",
            "url": "/products/cards-aist/",
            "price": "7,00 BYN",
            "price_value": 7.0,
            "currency": "BYN",
            "kind": "Карточки снимок",
            "category": category_label,
            "content": "",
            "rating": "3,0",
            "reviews_count": 1,
            "has_rating": True,
            "is_favorited": False,
            "is_in_cart": False,
            "is_purchased": False,
            "download_url": "",
            "download_analytics": None,
            **image_fields,
        }

    def expected_html_cards(self, *, variant: str) -> list[dict]:
        """Saved snapshot of rendered card attributes, newest first (default catalog sort)."""
        azbuka_image = self.azbuka_image
        thumb_400 = azbuka_image.thumb_url(400)
        thumb_800 = azbuka_image.thumb_url(800)
        is_mobile = variant == "mobile"
        sizes = "170px" if is_mobile else "436px"
        return [
            {
                "variant": variant,
                "item_id": str(self.aist.pk),
                "item_name": "Аист",
                "item_category": "Дидактическая игра",
                "item_variant": "Карточки снимок",
                "price": "7,0",
                "currency": "BYN",
                "href": "/products/cards-aist/",
                "img": {
                    "src": self.aist_image.image.url,
                    "srcset": None,
                    "sizes": None,
                    "loading": "eager" if is_mobile else "lazy",
                    "fetchpriority": "high" if is_mobile else None,
                },
                "favorited": "false",
                "in_cart": "false",
                "download_link": False,
            },
            {
                "variant": variant,
                "item_id": str(self.arbuz.pk),
                "item_name": "Арбуз",
                "item_category": "Дидактическая игра",
                "item_variant": "Дидактические игры",
                "price": "10,0",
                "currency": "BYN",
                "href": "/products/cards-arbuz/",
                "img": {
                    "src": PLACEHOLDER_URL,
                    "srcset": None,
                    "sizes": None,
                    "loading": "eager" if is_mobile else "lazy",
                    "fetchpriority": None,
                },
                "favorited": "true",
                "in_cart": "true",
                "download_link": False,
            },
            {
                "variant": variant,
                "item_id": str(self.azbuka.pk),
                "item_name": "Азбука",
                "item_category": "Дидактическая игра",
                "item_variant": "Карточки снимок",
                "price": "12,5",
                "currency": "BYN",
                "href": "/products/cards-azbuka/",
                "img": {
                    "src": thumb_400,
                    "srcset": f"{thumb_400} 400w, {thumb_800} 800w",
                    "sizes": sizes,
                    "loading": "lazy",
                    "fetchpriority": None,
                },
                "favorited": "true",
                "in_cart": None,
                "download_link": True,
            },
        ]


class CatalogCardRegressionTests(ProductCardFixtureMixin, TestCase):
    maxDiff = None

    def _view_for_user(self):
        request = RequestFactory().get(reverse("catalog"))
        request.user = self.user
        request.session = {}
        view = CatalogView()
        view.setup(request)
        return view

    def _card_kwargs(self):
        ids = [self.azbuka.pk, self.arbuz.pk, self.aist.pk]
        return {
            "cart_product_ids": {self.azbuka.pk, self.arbuz.pk},
            "purchased_product_ids": {self.azbuka.pk},
            "favorite_product_ids": {self.azbuka.pk, self.arbuz.pk},
            "rating_summaries": get_product_rating_summaries(ids),
        }

    def _loaded_products(self):
        products = CatalogView()._base_products_queryset().filter(pk__in=[self.azbuka.pk, self.arbuz.pk, self.aist.pk])
        return {product.pk: product for product in products}

    def test_catalog_view_build_product_card_matches_snapshot(self):
        view = self._view_for_user()
        products = self._loaded_products()
        kwargs = self._card_kwargs()
        for product in products.values():
            with self.subTest(product=product.title, selected_category=False):
                self.assertEqual(
                    view._build_product_card(product, **kwargs),
                    self.expected_card(product, category_label="Дидактическая игра"),
                )
            with self.subTest(product=product.title, selected_category=True):
                self.assertEqual(
                    view._build_product_card(product, selected_category=self.category, **kwargs),
                    self.expected_card(product, category_label="Дидактическая игра"),
                )

    def test_card_dict_key_order_is_stable(self):
        view = self._view_for_user()
        product = self._loaded_products()[self.azbuka.pk]
        card = view._build_product_card(product, **self._card_kwargs())
        self.assertEqual(list(card), list(self.expected_card(product, category_label="Дидактическая игра")))

    def test_format_category_label_snapshot(self):
        view = CatalogView()
        cases = {
            None: "",
            "Дидактические игры": "Дидактическая игра",
            "Развивающие игры": "Развивающая игра",
            "Настольные игры": "Настольная игра",
            "Книги": "Книга",
            "Раскраски": "Раскраски",
        }
        for title, expected in cases.items():
            category = None if title is None else Category(title=title)
            with self.subTest(title=title):
                self.assertEqual(view._format_category_label(category), expected)

    def test_catalog_response_cards_match_snapshot(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("catalog"), {"category": self.category.slug})
        self.assertEqual(response.status_code, 200)

        cards_by_id = {card["id"]: card for card in response.context["catalog_products"]}
        mobile_by_id = {card["id"]: card for card in response.context["catalog_mobile_products"]}
        for product in (self.azbuka, self.arbuz, self.aist):
            with self.subTest(product=product.title):
                expected = self.expected_card(product, category_label="Дидактическая игра")
                self.assertEqual(cards_by_id[product.pk], expected)
                self.assertEqual(mobile_by_id[product.pk], expected)

        cards = parse_product_cards(response.content.decode())
        desktop_cards = [card for card in cards if card["variant"] == "desktop"]
        mobile_cards = [card for card in cards if card["variant"] == "mobile"]
        self.assertEqual(desktop_cards, self.expected_html_cards(variant="desktop"))
        self.assertEqual(mobile_cards, self.expected_html_cards(variant="mobile"))

    def test_alphabet_navigator_cards_match_snapshot(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("alphabet-navigator"), {"letter": "А"})
        self.assertEqual(response.status_code, 200)

        desktop_by_id = {card["id"]: card for card in response.context["alphabet_products"]}
        mobile_by_id = {card["id"]: card for card in response.context["alphabet_mobile_products"]}
        for product in (self.azbuka, self.arbuz, self.aist):
            with self.subTest(product=product.title):
                expected = self.expected_card(product, category_label="Дидактическая игра")
                self.assertEqual(desktop_by_id[product.pk], expected)
                self.assertEqual(mobile_by_id[product.pk], expected)

        cards = parse_product_cards(response.content.decode())
        title_order = [str(self.azbuka.pk), str(self.aist.pk), str(self.arbuz.pk)]

        def by_title(expected_cards):
            by_id = {card["item_id"]: card for card in expected_cards}
            return [by_id[item_id] for item_id in title_order]

        desktop_expected = by_title(self.expected_html_cards(variant="desktop"))
        mobile_expected = by_title(self.expected_html_cards(variant="mobile"))
        for card in mobile_expected:
            card["img"]["loading"] = "lazy"
            card["img"]["fetchpriority"] = None
        self.assertEqual([card for card in cards if card["variant"] == "desktop"], desktop_expected)
        self.assertEqual([card for card in cards if card["variant"] == "mobile"], mobile_expected)

    def test_alphabet_navigator_view_uses_catalog_card_builder(self):
        self.assertIs(AlphabetNavigatorView._build_product_card, CatalogView._build_product_card)
