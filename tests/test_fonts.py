from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class AirfoolBelarusianFontTests(unittest.TestCase):
    def test_airfool_be_woff2_exists_and_has_woff2_signature(self) -> None:
        woff2_path = REPO_ROOT / "static/fonts/airfool-be.woff2"
        self.assertTrue(woff2_path.is_file())
        self.assertEqual(woff2_path.read_bytes()[:4], b"wOF2")

    def test_airfool_be_ttf_exists(self) -> None:
        ttf_path = REPO_ROOT / "static/fonts/airfool-be.ttf"
        self.assertTrue(ttf_path.is_file())

    def test_fonts_css_airfool_block_points_to_belarusian_font_files(self) -> None:
        fonts_css = (REPO_ROOT / "tailwind/fonts.css").read_text(encoding="utf-8")
        airfool_blocks = re.findall(
            r'@font-face\s*\{[^}]*font-family:\s*"Airfool"[^}]*\}',
            fonts_css,
            flags=re.DOTALL,
        )
        self.assertEqual(len(airfool_blocks), 1, "expected a single Airfool @font-face block")
        block = airfool_blocks[0]
        self.assertIn('url("/static/fonts/airfool-be.woff2") format("woff2")', block)
        self.assertNotIn("/static/fonts/airfool.ttf", block)
