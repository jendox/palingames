import importlib
import json
from datetime import timedelta
from decimal import Decimal

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import FloatField, Value
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.favorites.models import Favorite
from apps.products.models import AgeGroup, AgeGroupTag, Category, Product, Review, ReviewStatus, SubType
from apps.products.search import DEFAULT_CATALOG_SORT, order_catalog_queryset
from apps.products.services.reviews import get_product_rating_summaries

User = get_user_model()


def create_published_product(**kwargs):
    kwargs.setdefault("is_published", True)
    return Product.objects.create(**kwargs)


def count_review_sql_queries(queries) -> int:
    return sum(1 for query in queries if "products_review" in query["sql"].lower())


class RatingSummariesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email="reviewer@example.com", password="test-password-123")
        cls.product_with_reviews = create_published_product(title="Rated", slug="rated", price=Decimal("10.00"))
        cls.product_without_reviews = create_published_product(title="Unrated", slug="unrated", price=Decimal("10.00"))
        Review.objects.create(
            product=cls.product_with_reviews,
            user=cls.user,
            rating=4,
            comment="Good",
            status=ReviewStatus.PUBLISHED,
        )
        Review.objects.create(
            product=cls.product_with_reviews,
            user=User.objects.create_user(email="other@example.com", password="test-password-123"),
            rating=5,
            comment="Pending",
            status=ReviewStatus.PENDING,
        )

    def test_summaries_only_published_and_skip_empty_ids(self):
        summaries = get_product_rating_summaries(
            [self.product_with_reviews.id, self.product_without_reviews.id],
        )
        self.assertEqual(set(summaries.keys()), {self.product_with_reviews.id})
        self.assertEqual(summaries[self.product_with_reviews.id].count, 1)
        self.assertEqual(summaries[self.product_with_reviews.id].average, 4.0)

        with self.assertNumQueries(0):
            self.assertEqual(get_product_rating_summaries([]), {})


class CatalogRatingDisplayTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(title="Каталог рейтинг", slug="rating-catalog")
        cls.age = AgeGroupTag.objects.create(value=AgeGroup.AGE_4_5)
        cls.subtype = SubType.objects.create(title="Набор")
        cls.user = User.objects.create_user(email="cat-reviewer@example.com", password="test-password-123")

        cls.unrated = cls._make_product("Без отзыва", "no-reviews-catalog")
        cls.rated = cls._make_product("С отзывом", "with-reviews-catalog")
        Review.objects.create(
            product=cls.rated,
            user=cls.user,
            rating=4,
            comment="Nice",
            status=ReviewStatus.PUBLISHED,
        )

    @classmethod
    def _make_product(cls, title, slug):
        product = create_published_product(title=title, slug=slug, price=Decimal("12.00"))
        product.categories.add(cls.category)
        product.subtypes.add(cls.subtype)
        product.age_groups.add(cls.age)
        return product

    def test_catalog_hides_rating_badge_without_reviews(self):
        response = self.client.get(
            reverse("catalog"),
            {"category": self.category.slug},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="catalog-desktop-results",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode().count("data-product-rating-badge"), 1)
        unrated_card = next(p for p in response.context["catalog_products"] if p["title"] == "Без отзыва")
        self.assertFalse(unrated_card["has_rating"])
        rated_card = next(p for p in response.context["catalog_products"] if p["title"] == "С отзывом")
        self.assertTrue(rated_card["has_rating"])
        self.assertEqual(rated_card["rating"], "4,0")

    def test_catalog_mobile_json_includes_has_rating(self):
        response = self.client.get(reverse("catalog"), {"category": self.category.slug})
        self.assertEqual(response.status_code, 200)
        script = response.content.decode().split('id="catalog-mobile-products-data"')[1]
        payload = json.loads(script.split(">", 1)[1].split("</script>", 1)[0].strip())
        by_title = {item["title"]: item for item in payload}
        self.assertFalse(by_title["Без отзыва"]["has_rating"])
        self.assertTrue(by_title["С отзывом"]["has_rating"])

    def test_catalog_review_query_count_independent_of_product_count(self):
        def review_queries_for_product_count(count: int) -> int:
            Product.objects.filter(categories=self.category).exclude(
                slug__in=["no-reviews-catalog", "with-reviews-catalog"],
            ).delete()
            for index in range(count):
                self._make_product(f"Extra {index}", f"extra-rating-{index}-{count}")
            connection.queries_log.clear()
            with CaptureQueriesContext(connection) as context:
                response = self.client.get(reverse("catalog"), {"category": self.category.slug})
                self.assertEqual(response.status_code, 200)
            return count_review_sql_queries(context.captured_queries)

        self.assertEqual(review_queries_for_product_count(3), review_queries_for_product_count(8))


class FavoritesRatingDisplayTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email="fav-reviewer@example.com", password="test-password-123")
        cls.unrated = create_published_product(title="Fav unrated", slug="fav-unrated", price=Decimal("5.00"))
        cls.rated = create_published_product(title="Fav rated", slug="fav-rated", price=Decimal("6.00"))
        Review.objects.create(
            product=cls.rated,
            user=cls.user,
            rating=5,
            comment="Great",
            status=ReviewStatus.PUBLISHED,
        )

    def test_guest_favorites_hides_rating_badge_without_reviews(self):
        session = self.client.session
        session["guest_favorite_product_ids"] = [self.unrated.id, self.rated.id]
        session.save()
        connection.queries_log.clear()
        with CaptureQueriesContext(connection) as context:
            response = self.client.get(reverse("favorites"))
            self.assertEqual(response.status_code, 200)
        self.assertEqual(count_review_sql_queries(context.captured_queries), 1)
        cards = response.context["favorites_products"]
        self.assertFalse(next(c for c in cards if c["title"] == "Fav unrated")["has_rating"])
        self.assertTrue(next(c for c in cards if c["title"] == "Fav rated")["has_rating"])

    def test_account_favorites_single_review_query(self):
        self.client.force_login(self.user)
        Favorite.objects.create(user=self.user, product=self.unrated)
        Favorite.objects.create(user=self.user, product=self.rated)
        connection.queries_log.clear()
        with CaptureQueriesContext(connection) as context:
            response = self.client.get(reverse("account"), {"tab": "favorites"})
            self.assertEqual(response.status_code, 200)
        self.assertEqual(count_review_sql_queries(context.captured_queries), 1)
        cards = response.context["account_favorites_products"]
        by_title = {card["title"]: card for card in cards}
        self.assertFalse(by_title["Fav unrated"]["has_rating"])
        self.assertTrue(by_title["Fav rated"]["has_rating"])


class ProductDetailRatingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email="detail-reviewer@example.com", password="test-password-123")
        cls.product = create_published_product(title="Detail product", slug="detail-product", price=Decimal("9.00"))

    def test_product_without_reviews_hides_stars_and_aggregate_rating(self):
        response = self.client.get(reverse("product-detail", kwargs={"slug": self.product.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "0.0 (0)")
        self.assertNotContains(response, "aggregateRating")

    def test_product_with_review_shows_rating_and_json_ld(self):
        Review.objects.create(
            product=self.product,
            user=self.user,
            rating=4,
            comment="Solid",
            status=ReviewStatus.PUBLISHED,
        )
        response = self.client.get(reverse("product-detail", kwargs={"slug": self.product.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "4,0 (1)")
        self.assertContains(response, '"aggregateRating"')
        self.assertContains(response, '"ratingValue":"4.0"')
        self.assertContains(response, '"reviewCount":1')


class CatalogSortTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(title="Сортировка", slug="sort-catalog")
        now = timezone.now()
        cls.oldest = create_published_product(title="Oldest", slug="sort-oldest", price=Decimal("1.00"))
        cls.newest = create_published_product(title="Newest", slug="sort-newest", price=Decimal("2.00"))
        cls.tie_old = create_published_product(title="Tie old", slug="sort-tie-old", price=Decimal("3.00"))
        cls.tie_new = create_published_product(title="Tie new", slug="sort-tie-new", price=Decimal("4.00"))
        Product.objects.filter(pk=cls.oldest.pk).update(created_at=now - timedelta(days=10))
        Product.objects.filter(pk=cls.newest.pk).update(created_at=now)
        same_ts = now - timedelta(days=1)
        Product.objects.filter(pk__in=[cls.tie_old.pk, cls.tie_new.pk]).update(created_at=same_ts)
        for product in (cls.oldest, cls.newest, cls.tie_old, cls.tie_new):
            product.categories.add(cls.category)

    def test_order_catalog_queryset_defaults_to_newest(self):
        qs = Product.objects.filter(categories=self.category)
        ordered = list(
            order_catalog_queryset(qs, sort_value="", search_active=False, order_by_similarity=False).values_list(
                "slug",
                flat=True,
            ),
        )
        self.assertEqual(ordered[0], "sort-newest")
        self.assertLess(ordered.index("sort-tie-new"), ordered.index("sort-tie-old"))
        self.assertEqual(ordered[-1], "sort-oldest")

    def test_order_catalog_queryset_unknown_sort_falls_back_to_newest(self):
        qs = Product.objects.filter(categories=self.category)
        ordered = list(
            order_catalog_queryset(
                qs,
                sort_value="unknown",
                search_active=False,
                order_by_similarity=False,
            ).values_list("slug", flat=True),
        )
        self.assertEqual(ordered[0], "sort-newest")

    def test_search_without_sort_keeps_similarity_order(self):
        qs = Product.objects.published().annotate(_search_sim=Value(0.5, output_field=FloatField()))
        ordered_sql = str(
            order_catalog_queryset(
                qs,
                sort_value="",
                search_active=True,
                order_by_similarity=True,
            ).query,
        )
        self.assertIn("_search_sim", ordered_sql)

    def test_catalog_sort_options_select_newest_by_default(self):
        response = self.client.get(reverse("catalog"), {"category": self.category.slug})
        self.assertEqual(response.status_code, 200)
        selected = [option for option in response.context["catalog_sort_options"] if option["selected"]]
        self.assertEqual(selected, [{"value": "newest", "label": "новые игры", "selected": True}])
        self.assertEqual(response.context["catalog_sort_value"], "")

    def test_alphabet_sort_options_select_title_by_default(self):
        Product.objects.filter(slug="sort-newest").update(title="Альфа сорт")
        response = self.client.get(reverse("alphabet-navigator"), {"letter": "А"})
        self.assertEqual(response.status_code, 200)
        selected = [option for option in response.context["alphabet_sort_options"] if option["selected"]]
        self.assertEqual(selected, [{"value": "title", "label": "имя", "selected": True}])


class CopyTextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product = create_published_product(title="Copy text", slug="copy-text", price=Decimal("11.00"))

    def test_product_access_instructions_use_material_wording(self):
        response = self.client.get(reverse("product-detail", kwargs={"slug": self.product.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "материал будет доступен")
        self.assertNotContains(response, "PDF будет доступен в личном кабинете")

    def test_home_hero_uses_obuchennye_igry(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Обучающие игры")
        self.assertNotContains(response, "Обучающие PDF-игры")


class AgeGroupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(title="Возраст каталог", slug="age-catalog")
        cls.other_category = Category.objects.create(title="Другая категория", slug="age-other-category")
        cls.subtype = SubType.objects.create(title="Карточки возраст")

    def test_age_group_enum_and_field_choices(self):
        self.assertEqual(list(AgeGroup.values), ["2-3", "3-4", "4-5", "5-6", "6-7"])
        self.assertEqual(
            list(AgeGroupTag._meta.get_field("value").choices),
            list(AgeGroup.choices),
        )

    def test_seed_migration_is_idempotent(self):
        migration = importlib.import_module("apps.products.migrations.0011_seed_age_groups_3_4_5_6")
        migration.seed_age_groups(django_apps, None)
        migration.seed_age_groups(django_apps, None)
        self.assertEqual(AgeGroupTag.objects.filter(value="3-4").count(), 1)
        self.assertEqual(AgeGroupTag.objects.filter(value="5-6").count(), 1)

    def _age_tag(self, value):
        tag, _ = AgeGroupTag.objects.get_or_create(value=value)
        return tag

    def _make_product(self, title, slug, category, age_tag, *, published=True):
        product = create_published_product(title=title, slug=slug, price=Decimal("10.00"), is_published=published)
        product.categories.add(category)
        product.subtypes.add(self.subtype)
        product.age_groups.add(age_tag)
        return product

    def test_catalog_age_filter_hides_empty_groups_and_orders_logically(self):
        age_3_4 = self._age_tag(AgeGroup.AGE_3_4)
        age_5_6 = self._age_tag(AgeGroup.AGE_5_6)
        age_4_5 = self._age_tag(AgeGroup.AGE_4_5)
        empty_age = self._age_tag(AgeGroup.AGE_2_3)
        self._make_product("In cat 3-4", "in-cat-3-4", self.category, age_3_4)
        self._make_product("In cat 5-6", "in-cat-5-6", self.category, age_5_6)
        self._make_product("Other cat", "other-cat-age", self.other_category, age_4_5)
        self._make_product("Unpublished", "unpublished-age", self.category, age_4_5, published=False)

        response = self.client.get(reverse("catalog"), {"category": self.category.slug})
        self.assertEqual(response.status_code, 200)
        age_labels = [option["label"] for option in response.context["catalog_filters"]["ages"]]
        self.assertEqual(age_labels, ["3-4", "5-6"])
        self.assertNotIn(empty_age.value, age_labels)

    def test_catalog_keeps_selected_empty_age_group_in_options(self):
        empty_age = self._age_tag(AgeGroup.AGE_6_7)
        visible_age = self._age_tag(AgeGroup.AGE_4_5)
        self._make_product("Visible age", "visible-age", self.category, visible_age)

        response = self.client.get(
            reverse("catalog"),
            {"category": self.category.slug, "age": str(empty_age.pk)},
        )
        self.assertEqual(response.status_code, 200)
        ages = {option["id"]: option for option in response.context["catalog_filters"]["ages"]}
        self.assertIn(empty_age.pk, ages)
        self.assertTrue(ages[empty_age.pk]["selected"])
        self.assertEqual(ages[empty_age.pk]["count"], 0)

    def test_catalog_filters_by_new_age_group(self):
        age_3_4 = self._age_tag(AgeGroup.AGE_3_4)
        matched = self._make_product("Match 3-4", "match-3-4", self.category, age_3_4)
        self._make_product(
            "No match",
            "no-match-age",
            self.category,
            self._age_tag(AgeGroup.AGE_5_6),
        )

        response = self.client.get(reverse("catalog"), {"category": self.category.slug, "age": str(age_3_4.pk)})
        self.assertEqual(response.status_code, 200)
        titles = [product["title"] for product in response.context["catalog_products"]]
        self.assertEqual(titles, [matched.title])

    def test_alphabet_age_filter_respects_letter_scope(self):
        age_3_4 = self._age_tag(AgeGroup.AGE_3_4)
        self._make_product("Астра 3-4", "astra-3-4", self.category, age_3_4)
        self._make_product(
            "Бeta 3-4",
            "beta-3-4",
            self.category,
            age_3_4,
        )

        response = self.client.get(reverse("alphabet-navigator"), {"letter": "А"})
        self.assertEqual(response.status_code, 200)
        age_labels = [option["label"] for option in response.context["alphabet_filters"]["ages"]]
        self.assertEqual(age_labels, ["3-4"])

    def test_default_catalog_sort_constant(self):
        self.assertEqual(DEFAULT_CATALOG_SORT, "newest")
