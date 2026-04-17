from django.test import TestCase
from django.urls import reverse

from apps.favorites.models import Favorite
from apps.favorites.services import SESSION_FAVORITES_KEY
from apps.products.models import Category, Product
from apps.users.models import CustomUser


class FavoritesViewsTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(title="Дидактические игры", slug="didakticheskie-igry")
        self.product_1 = Product.objects.create(
            title="Игра 1",
            slug="igra-1",
            price="1.50",
        )
        self.product_2 = Product.objects.create(
            title="Игра 2",
            slug="igra-2",
            price="2.00",
        )
        self.product_1.categories.add(self.category)
        self.product_2.categories.add(self.category)

    def test_guest_favorite_toggle_add_and_remove(self):
        toggle_url = reverse("favorite-toggle")

        response = self.client.post(toggle_url, {"product_id": self.product_1.id})
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "ok": True,
                "is_favorited": True,
                "favorites_count": 1,
            },
        )
        self.assertEqual(self.client.session.get(SESSION_FAVORITES_KEY), [self.product_1.id])

        response = self.client.post(toggle_url, {"product_id": self.product_1.id})
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "ok": True,
                "is_favorited": False,
                "favorites_count": 0,
            },
        )
        self.assertEqual(self.client.session.get(SESSION_FAVORITES_KEY), [])

    def test_guest_favorite_toggle_rejects_unknown_product(self):
        response = self.client.post(reverse("favorite-toggle"), {"product_id": 999_999})
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(self.client.session.get(SESSION_FAVORITES_KEY))

    def test_guest_favorite_toggle_rejects_non_integer_product(self):
        response = self.client.post(reverse("favorite-toggle"), {"product_id": "not-a-number"})
        self.assertEqual(response.status_code, 404)

    def test_authenticated_favorite_toggle_add_and_remove(self):
        user = CustomUser.objects.create_user(email="fav@example.com", password="test-password-123")
        self.client.force_login(user)

        response = self.client.post(reverse("favorite-toggle"), {"product_id": self.product_1.id})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Favorite.objects.filter(user=user, product=self.product_1).exists())
        self.assertJSONEqual(
            response.content,
            {
                "ok": True,
                "is_favorited": True,
                "favorites_count": 1,
            },
        )

        response = self.client.post(reverse("favorite-toggle"), {"product_id": self.product_1.id})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Favorite.objects.filter(user=user, product=self.product_1).exists())
        self.assertJSONEqual(
            response.content,
            {
                "ok": True,
                "is_favorited": False,
                "favorites_count": 0,
            },
        )

    def test_guest_favorites_merge_to_user_after_login(self):
        session = self.client.session
        session[SESSION_FAVORITES_KEY] = [self.product_1.id, self.product_2.id]
        session.save()

        user = CustomUser.objects.create_user(email="merge-fav@example.com", password="test-password-123")
        logged_in = self.client.login(email="merge-fav@example.com", password="test-password-123")
        self.assertTrue(logged_in)

        favorite_product_ids = set(
            Favorite.objects.filter(user=user).values_list("product_id", flat=True),
        )
        self.assertEqual(favorite_product_ids, {self.product_1.id, self.product_2.id})
        self.assertEqual(self.client.session.get(SESSION_FAVORITES_KEY), [])

    def test_guest_favorites_merge_skips_already_favorited(self):
        user = CustomUser.objects.create_user(email="merge-dup@example.com", password="test-password-123")
        Favorite.objects.create(user=user, product=self.product_1)

        session = self.client.session
        session[SESSION_FAVORITES_KEY] = [self.product_1.id, self.product_2.id]
        session.save()

        logged_in = self.client.login(email="merge-dup@example.com", password="test-password-123")
        self.assertTrue(logged_in)

        favorite_product_ids = set(
            Favorite.objects.filter(user=user).values_list("product_id", flat=True),
        )
        self.assertEqual(favorite_product_ids, {self.product_1.id, self.product_2.id})
        self.assertEqual(Favorite.objects.filter(user=user).count(), 2)
        self.assertEqual(self.client.session.get(SESSION_FAVORITES_KEY), [])

    def test_guest_favorites_merge_noop_when_session_empty(self):
        user = CustomUser.objects.create_user(email="empty-fav@example.com", password="test-password-123")
        self.client.login(email="empty-fav@example.com", password="test-password-123")

        self.assertFalse(Favorite.objects.filter(user=user).exists())


class FavoritesPageViewTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(title="Дидактические игры", slug="didakticheskie-igry")
        self.product_1 = Product.objects.create(title="Игра 1", slug="igra-1", price="1.50")
        self.product_2 = Product.objects.create(title="Игра 2", slug="igra-2", price="2.00")
        self.product_1.categories.add(self.category)
        self.product_2.categories.add(self.category)

    def test_guest_empty_favorites_page_renders_placeholder(self):
        response = self.client.get(reverse("favorites"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Избранное")
        self.assertContains(response, "В избранном пока пусто.")

    def test_guest_favorites_page_renders_products_from_session(self):
        session = self.client.session
        session[SESSION_FAVORITES_KEY] = [self.product_1.id, self.product_2.id]
        session.save()

        response = self.client.get(reverse("favorites"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.product_1.title)
        self.assertContains(response, self.product_2.title)
        self.assertEqual(response.context["favorites_count"], 2)

    def test_authenticated_user_is_redirected_to_account_favorites_tab(self):
        user = CustomUser.objects.create_user(email="redirect@example.com", password="test-password-123")
        self.client.force_login(user)

        response = self.client.get(reverse("favorites"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("tab=favorites", response.headers.get("Location", ""))
