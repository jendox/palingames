
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.products.models import AgeGroup, AgeGroupTag, Category, Product, SubType


class CatalogViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(title="Дидактические игры", slug="didactic-games")
        cls.other_category = Category.objects.create(title="Настольные игры", slug="board-games")

        cls.age_2_3 = AgeGroupTag.objects.create(value=AgeGroup.AGE_2_3)
        cls.age_4_5 = AgeGroupTag.objects.create(value=AgeGroup.AGE_4_5)

        cls.subtype_cards = SubType.objects.create(title="Карточки", category=cls.category)
        cls.subtype_sets = SubType.objects.create(title="Наборы", category=cls.category)

        cls.alpha = cls._make_product("Альфа", "alpha", Decimal("30.00"), cls.category, cls.subtype_cards, cls.age_2_3)
        cls.beta = cls._make_product("Бета", "beta", Decimal("10.00"), cls.category, cls.subtype_sets, cls.age_4_5)
        cls.gamma = cls._make_product("Гамма", "gamma", Decimal("20.00"), cls.category, cls.subtype_cards, cls.age_4_5)

        cls.foreign_product = Product.objects.create(
            title="Чужой товар",
            slug="foreign-product",
            price=Decimal("99.00"),
        )
        cls.foreign_product.categories.add(cls.other_category)

    @classmethod
    def _make_product(cls, title, slug, price, category, subtype, age_group):
        product = Product.objects.create(
            title=title,
            slug=slug,
            price=price,
        )
        product.categories.add(category)
        product.subtypes.add(subtype)
        product.age_groups.add(age_group)
        return product

    def test_catalog_filters_products_by_selected_category(self):
        response = self.client.get(reverse("catalog"), {"category": self.category.slug})

        self.assertEqual(response.status_code, 200)
        products = response.context["catalog_products"]
        titles = [product["title"] for product in products]

        self.assertCountEqual(titles, ["Альфа", "Бета", "Гамма"])
        self.assertNotIn("Чужой товар", titles)

    def test_catalog_sorts_products_by_price_ascending(self):
        response = self.client.get(
            reverse("catalog"),
            {"category": self.category.slug, "sort": "price_asc"},
        )

        self.assertEqual(response.status_code, 200)
        titles = [product["title"] for product in response.context["catalog_products"]]

        self.assertEqual(titles, ["Бета", "Гамма", "Альфа"])

    def test_catalog_filters_products_by_subtype_and_age(self):
        response = self.client.get(
            reverse("catalog"),
            {
                "category": self.category.slug,
                "subtype": str(self.subtype_cards.pk),
                "age": str(self.age_4_5.pk),
            },
        )

        self.assertEqual(response.status_code, 200)
        titles = [product["title"] for product in response.context["catalog_products"]]

        self.assertEqual(titles, ["Гамма"])

    def test_mobile_htmx_request_returns_mobile_listing_partial(self):
        response = self.client.get(
            reverse("catalog"),
            {"category": self.category.slug, "sort": "price_desc"},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="catalog-mobile-listing-root",
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn('id="catalog-mobile-listing-root"', content)
        self.assertIn('data-catalog-mobile-filter-form', content)
        self.assertIn('name="sort" value="price_desc"', content)
        self.assertIn("Сортировка", content)

    def test_catalog_uses_different_page_sizes_for_desktop_and_mobile(self):
        for index in range(6):
            self._make_product(
                f"Дополнительный {index}",
                f"extra-{index}",
                Decimal(f"{40 + index}.00"),
                self.category,
                self.subtype_cards,
                self.age_2_3,
            )

        response = self.client.get(reverse("catalog"), {"category": self.category.slug})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["catalog_products"]), 9)
        self.assertEqual(len(response.context["catalog_mobile_products"]), 8)

    def test_desktop_results_panel_shows_current_page_range(self):
        for index in range(15):
            self._make_product(
                f"Диапазон {index}",
                f"range-{index}",
                Decimal(f"{40 + index}.00"),
                self.category,
                self.subtype_cards,
                self.age_2_3,
            )

        response = self.client.get(reverse("catalog"), {"category": self.category.slug})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1-9 из 18")

    def test_mobile_pagination_uses_compact_page_items_with_ellipsis(self):
        for index in range(22):
            self._make_product(
                f"Пагинация {index}",
                f"pagination-{index}",
                Decimal(f"{40 + index}.00"),
                self.category,
                self.subtype_cards,
                self.age_2_3,
            )

        response = self.client.get(reverse("catalog"), {"category": self.category.slug, "page": 2})

        self.assertEqual(response.status_code, 200)
        pagination_items = response.context["catalog_mobile_pagination_items"]

        self.assertEqual(
            pagination_items,
            [
                {"type": "page", "number": 1, "current": False},
                {"type": "page", "number": 2, "current": True},
                {"type": "page", "number": 3, "current": False},
                {"type": "ellipsis"},
                {"type": "page", "number": 4, "current": False},
            ],
        )


class AlphabetNavigatorViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(title="Дидактические игры", slug="didactic-games")
        cls.subtype = SubType.objects.create(title="Карточки", category=cls.category)
        cls.alt_subtype = SubType.objects.create(title="Наборы", category=cls.category)
        cls.age = AgeGroupTag.objects.create(value=AgeGroup.AGE_2_3)
        cls.alt_age = AgeGroupTag.objects.create(value=AgeGroup.AGE_4_5)

        cls._make_product("Альфа", "alpha-nav", Decimal("30.00"))
        cls._make_product("Арфа", "harp-nav", Decimal("20.00"))
        cls._make_product("Бета", "beta-nav", Decimal("10.00"))
        cls._make_product("Астра", "astra-nav", Decimal("15.00"), subtype=cls.alt_subtype, age=cls.alt_age)

    @classmethod
    def _make_product(cls, title, slug, price, subtype=None, age=None):
        product = Product.objects.create(title=title, slug=slug, price=price)
        product.categories.add(cls.category)
        product.subtypes.add(subtype or cls.subtype)
        product.age_groups.add(age or cls.age)
        return product

    def test_alphabet_navigator_defaults_to_letter_a(self):
        response = self.client.get(reverse("alphabet-navigator"))

        self.assertEqual(response.status_code, 200)
        titles = [product["title"] for product in response.context["alphabet_products"]]

        self.assertCountEqual(titles, ["Альфа", "Арфа", "Астра"])
        self.assertNotIn("Бета", titles)
        self.assertEqual(response.context["alphabet_selected_letter"], "А")

    def test_alphabet_navigator_filters_by_requested_letter(self):
        response = self.client.get(reverse("alphabet-navigator"), {"letter": "Б"})

        self.assertEqual(response.status_code, 200)
        titles = [product["title"] for product in response.context["alphabet_products"]]

        self.assertEqual(titles, ["Бета"])

    def test_alphabet_navigator_mobile_htmx_returns_mobile_partial(self):
        response = self.client.get(
            reverse("alphabet-navigator"),
            {"letter": "А", "sort": "price_desc"},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="alphabet-mobile-listing-root",
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn('id="alphabet-mobile-listing-root"', content)
        self.assertIn("Алфавитный навигатор", content)
        self.assertIn("сортировка", content.lower())

    def test_alphabet_navigator_filters_inside_selected_letter(self):
        response = self.client.get(
            reverse("alphabet-navigator"),
            {"letter": "А", "subtype": str(self.alt_subtype.pk), "age": str(self.alt_age.pk)},
        )

        self.assertEqual(response.status_code, 200)
        titles = [product["title"] for product in response.context["alphabet_products"]]

        self.assertEqual(titles, ["Астра"])

    def test_alphabet_navigator_uses_catalog_like_htmx_targets(self):
        response = self.client.get(reverse("alphabet-navigator"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn('hx-target="#alphabet-desktop-results"', content)
        self.assertIn('hx-target="#alphabet-mobile-listing-root"', content)

    def test_alphabet_navigator_desktop_htmx_returns_results_panel_partial(self):
        response = self.client.get(
            reverse("alphabet-navigator"),
            {"letter": "А", "subtype": str(self.subtype.pk)},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="alphabet-desktop-results",
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn('id="alphabet-desktop-results"', content)
        self.assertNotIn('id="alphabet-desktop-listing-root"', content)
