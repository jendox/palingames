import re

from django.test import TestCase
from django.urls import reverse

_XL_WIDTH_300 = re.compile(r"(?<!\d)xl:w-\[300px\]")
_XL_HEIGHT_424 = re.compile(r"(?<!\d)xl:h-\[424px\]")


class LayoutOverflowRegressionTests(TestCase):
    def test_home_footer_desktop_grid_uses_responsive_horizontal_gap(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "gap-x-[48px]")
        self.assertContains(response, "min-[1280px]:gap-x-[120px]")
        self.assertNotContains(
            response,
            'grid grid-cols-[1fr_auto_1fr] items-start gap-x-[120px] gap-y-20',
        )

    def test_home_catalog_categories_desktop_avoids_xl_overflow_classes(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIsNone(_XL_WIDTH_300.search(content))
        self.assertIsNone(_XL_HEIGHT_424.search(content))
        self.assertContains(response, "2xl:w-[300px]")
        self.assertContains(response, "xl:gap-x-6")
