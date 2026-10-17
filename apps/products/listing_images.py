from __future__ import annotations

from django.templatetags.static import static

from apps.products.models import ProductImage

PLACEHOLDER = "images/example-product-image-1.png"


def build_listing_image_fields(product_image: ProductImage | None) -> dict:
    fallback = static(PLACEHOLDER)
    if product_image is None or not product_image.image:
        return {
            "image_url": fallback,
            "image_thumb_url": fallback,
            "image_srcset": "",
            "image_width": None,
            "image_height": None,
        }

    original_url = product_image.image.url
    if product_image.has_fresh_thumbs:
        thumb_url = product_image.thumb_url(400) or original_url
        return {
            "image_url": original_url,
            "image_thumb_url": thumb_url,
            "image_srcset": product_image.listing_srcset,
            "image_width": product_image.width,
            "image_height": product_image.height,
        }

    return {
        "image_url": original_url,
        "image_thumb_url": original_url,
        "image_srcset": "",
        "image_width": product_image.width,
        "image_height": product_image.height,
    }
