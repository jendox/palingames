from django.urls import path

from .collection_views import CollectionDetailView, CollectionIndexView
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
    path("collections/", CollectionIndexView.as_view(), name="collections"),
    path("collections/<slug:slug>/", CollectionDetailView.as_view(), name="collection-detail"),
    path("alphabet/", AlphabetNavigatorView.as_view(), name="alphabet-navigator"),
    path("products/<int:product_id>/download/", ProductDownloadView.as_view(), name="product-download"),
    path("products/<slug:slug>/reviews/submit/", ProductReviewSubmitView.as_view(), name="product-review-submit"),
    path("products/<slug:slug>/", ProductDetailView.as_view(), name="product-detail"),
]
