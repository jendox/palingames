from django.urls import path

from .admin_upload_views import (
    custom_game_file_finalize,
    custom_game_file_presign,
    product_file_finalize,
    product_file_presign,
)

urlpatterns = [
    path("product-files/presign/", product_file_presign, name="admin-product-file-presign"),
    path("product-files/finalize/", product_file_finalize, name="admin-product-file-finalize"),
    path("custom-game-files/presign/", custom_game_file_presign, name="admin-custom-game-file-presign"),
    path("custom-game-files/finalize/", custom_game_file_finalize, name="admin-custom-game-file-finalize"),
]
