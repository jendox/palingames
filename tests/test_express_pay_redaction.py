from __future__ import annotations

import unittest

from libs.express_pay.redaction import mask_secret_query


class MaskSecretQueryTests(unittest.TestCase):
    SECRET = "abc123secret"

    def test_masks_token_in_middle_of_url(self):
        url = f"https://api.express-pay.by/v1/invoices?foo=1&token={self.SECRET}&bar=2"
        masked = mask_secret_query(url)
        self.assertNotIn(self.SECRET, masked)
        self.assertIn("token=***", masked)
        self.assertIn("foo=1", masked)
        self.assertIn("bar=2", masked)

    def test_masks_token_at_end_of_url(self):
        url = f"https://api.express-pay.by/v1/invoices?token={self.SECRET}"
        self.assertEqual(
            mask_secret_query(url),
            "https://api.express-pay.by/v1/invoices?token=***",
        )

    def test_masks_token_case_insensitive(self):
        url = f"https://api.express-pay.by/v1/invoices?TOKEN={self.SECRET}"
        self.assertIn("TOKEN=***", mask_secret_query(url))
        self.assertNotIn(self.SECRET, mask_secret_query(url))

    def test_masks_token_inside_quotes(self):
        text = f"401 Unauthorized for url 'https://api.express-pay.by/v1/invoices?token={self.SECRET}'"
        masked = mask_secret_query(text)
        self.assertIn("token=***", masked)
        self.assertNotIn(self.SECRET, masked)

    def test_leaves_text_without_token_unchanged(self):
        text = "connection failed for host api.express-pay.by"
        self.assertEqual(mask_secret_query(text), text)
