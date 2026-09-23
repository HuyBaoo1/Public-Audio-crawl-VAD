from __future__ import annotations

import unittest

from news_vad_pipeline.utils import keyword_match, normalize_text


class DiscoveryFilterTest(unittest.TestCase):
    def test_vietnamese_diacritics_are_normalized(self) -> None:
        self.assertEqual(normalize_text("BẢN TIN THỜI SỰ"), "ban tin thoi su")
        self.assertTrue(keyword_match("Bản tin Thời sự Hà Nội", ("thoi su",)))

    def test_unrelated_title_does_not_match(self) -> None:
        self.assertFalse(keyword_match("Chương trình ca nhạc cuối tuần", ("ban tin", "thoi su")))


if __name__ == "__main__":
    unittest.main()
