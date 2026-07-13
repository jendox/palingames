from django.urls import path

from .admin_upload_views import product_file_finalize, product_file_presign

urlpatterns = [
    path("product-files/presign/", product_file_presign, name="admin-product-file-presign"),
    path("product-files/finalize/", product_file_finalize, name="admin-product-file-finalize"),
]
