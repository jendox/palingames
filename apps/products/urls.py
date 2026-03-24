from django.urls import path

from .views import AlphabetNavigatorView, CatalogView, ProductDetailView, ProductDownloadView

urlpatterns = [
    path("catalog/", CatalogView.as_view(), name="catalog"),
    path("alphabet/", AlphabetNavigatorView.as_view(), name="alphabet-navigator"),
    path("products/<int:product_id>/download/", ProductDownloadView.as_view(), name="product-download"),
    path("products/<slug:slug>/", ProductDetailView.as_view(), name="product-detail"),
]
