import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from extras import classify, https, merge, sniff  # noqa: E402


class Classify(unittest.TestCase):
    def test_slots(self):
        self.assertEqual(classify({"title": "Manual"}), "manual")
        self.assertEqual(classify({"title": "InvisiClues hint booklet"}), "hints")
        self.assertEqual(classify({"title": "Map"}), "map")
        self.assertEqual(classify({"title": "Feelies"}), "feelies")
        self.assertIsNone(classify({"title": "Walkthrough"}))
        self.assertIsNone(classify({"title": "Story file", "isGame": True}))

    def test_sniff_refuses_unsafe_types(self):
        self.assertEqual(sniff(b"GIF89a..."), "gif")
        self.assertEqual(sniff(b'<?xml version="1.0"?><svg xmlns="x"/>'), "svg")
        self.assertEqual(sniff(b"PK\x03\x04..."), "zip")
        self.assertEqual(sniff(b"%PDF-1.7"), "pdf")

    def test_https_upgrade(self):
        self.assertEqual(https("http://example.org/a.pdf"), "https://example.org/a.pdf")


class Merge(unittest.TestCase):
    def test_keeps_hand_added_and_dedupes(self):
        m = {"manual": {"sha256": "a"}, "hints": [{"sha256": "h"}]}
        merge(m, {"manual": {"sha256": "b"}, "hints": [{"sha256": "h"}, {"sha256": "i"}]})
        self.assertEqual(m["manual"]["sha256"], "a")
        self.assertEqual([p["sha256"] for p in m["hints"]], ["h", "i"])


if __name__ == "__main__":
    unittest.main()
