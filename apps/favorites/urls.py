from django.urls import path

from .views import FavoritesPageView, favorite_toggle_view

urlpatterns = [
    path("favorites/", FavoritesPageView.as_view(), name="favorites"),
    path("favorites/toggle/", favorite_toggle_view, name="favorite-toggle"),
]
