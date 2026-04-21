from django.urls import path

from .views import (
    AlphabetNavigatorView,
    CatalogSearchSuggestView,
    CatalogView,
    ProductDetailView,
    ProductDownloadView,
    ProductReviewSubmitView,
)

urlpatterns = [
    path("catalog/suggest/", CatalogSearchSuggestView.as_view(), name="catalog-search-suggest"),
    path("catalog/", CatalogView.as_view(), name="catalog"),
    path("alphabet/", AlphabetNavigatorView.as_view(), name="alphabet-navigator"),
    path("products/<int:product_id>/download/", ProductDownloadView.as_view(), name="product-download"),
    path("products/<slug:slug>/reviews/submit/", ProductReviewSubmitView.as_view(), name="product-review-submit"),
    path("products/<slug:slug>/", ProductDetailView.as_view(), name="product-detail"),
]
