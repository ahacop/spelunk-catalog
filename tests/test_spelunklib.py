import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from spelunklib import NotAStory, check_bytes, identify_story, validate_manifest  # noqa: E402


def fake_story(version=3, release=88, serial=b"840726", checksum=0xA129, size=4096):
    b = bytearray(size)
    b[0] = version
    b[2:4] = release.to_bytes(2, "big")
    for off in (0x04, 0x08, 0x0A, 0x0C, 0x0E):
        b[off:off + 2] = (0x100).to_bytes(2, "big")
    b[0x12:0x18] = serial
    b[0x1A:0x1C] = min(size // 2, 0xFFFF).to_bytes(2, "big")
    b[0x1C:0x1E] = checksum.to_bytes(2, "big")
    return bytes(b)


def manifest(data):
    i = identify_story(data)
    return {
        "spelunk": 1, "title": "Zork I", "game": "0dbnusxunq7fw5ro",
        "story": {"ifid": i.ifid, "release": i.release, "serial": i.serial,
                  "checksum": i.checksum, "type": i.type, "sha256": i.sha256,
                  "sources": ["https://eblong.com/infocom/gamefiles/zork1-r88-s840726.z3"]},
    }


class Identity(unittest.TestCase):
    def test_reads_header(self):
        i = identify_story(fake_story())
        self.assertEqual((i.release, i.serial, i.checksum, i.type), (88, "840726", "a129", "z3"))

    def test_ifid_drops_checksum_for_date_serials(self):
        self.assertEqual(identify_story(fake_story()).ifid, "ZCODE-88-840726")
        self.assertEqual(identify_story(fake_story(serial=b"000000")).ifid, "ZCODE-88-000000-A129")

    def test_rejects_non_story(self):
        with self.assertRaises(NotAStory):
            identify_story(b"<html>" + bytes(100))
        with self.assertRaises(NotAStory):
            identify_story(fake_story(version=3, size=130 * 1024))


class Manifest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_manifest(manifest(fake_story())), [])

    def test_rejects_http_svg_and_local_hosts(self):
        m = manifest(fake_story())
        m["story"]["sources"] = ["http://eblong.com/x.z3", "https://127.0.0.1/x.z3", "https://nas.local/x"]
        m["cover"] = {"type": "svg", "sha256": "0" * 64, "sources": ["https://example.com/c.svg"]}
        errs = validate_manifest(m)
        self.assertEqual(len(errs), 4, errs)

    def test_rejects_dirty_title(self):
        m = manifest(fake_story())
        m["title"] = "Zork‮ I"
        self.assertTrue(validate_manifest(m))

    def test_bytes_checks(self):
        data = fake_story()
        part = manifest(data)["story"]
        self.assertEqual(check_bytes(part, data, "story"), [])
        other = fake_story(release=119)
        errs = check_bytes(part, other, "story")
        self.assertTrue(any("sha256" in e for e in errs))
        self.assertTrue(any("release" in e for e in errs))
        cover = {"type": "png", "sha256": hashlib.sha256(b"<svg/>").hexdigest()}
        self.assertTrue(check_bytes(cover, b"<svg/>", "cover"))


class HintMarkup(unittest.TestCase):
    def test_allowlist(self):
        from spelunklib import hint_markup_problems
        ok = b'{"content": "<p>Try <b>north</b> &gt; then <em>up</em>.</p><ul><li>a</li></ul><pre>x</pre>"}'
        self.assertEqual(hint_markup_problems(ok), [])
        bad = b'{"a": "<a href=\\"https://x\\">x</a><img src=x><p style=\\"x\\">y</p><script>1</script>"}'
        probs = hint_markup_problems(bad)
        for want in ("<a>", "<img>", "attributes on <p>", "<script>"):
            self.assertTrue(any(want in p for p in probs), (want, probs))


if __name__ == "__main__":
    unittest.main()
