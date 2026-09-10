from django.urls import path

from .admin_upload_views import managed_link_finalize, managed_link_presign

urlpatterns = [
    path("managed-links/presign/", managed_link_presign, name="admin-managed-link-presign"),
    path("managed-links/finalize/", managed_link_finalize, name="admin-managed-link-finalize"),
]
