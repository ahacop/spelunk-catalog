"""Shared helpers for the Spelunk catalog: Z-machine header identity and
.spelunk manifest validation (format version 1, ahacop/spelunk#31).

Standard library only, so CI needs no dependencies.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit

FORMAT_VERSION = 1

# Per-part size caps from #31's security model, in bytes.
CAPS = {
    "manifest": 256 * 1024,
    "story": 512 * 1024,
    "hints": 2 * 1024 * 1024,
    "image": 20 * 1024 * 1024,
    "pdf": 50 * 1024 * 1024,
}

STORY_TYPES = {f"z{v}" for v in range(1, 9)}
IMAGE_TYPES = {"png", "jpeg", "heic"}
HINT_TYPES = {"hints-json", "z5", "z8"}

# Maximum story file length per Z-machine version (Standard 1.1, section 1.1.4).
MAX_STORY_LEN = {1: 128, 2: 128, 3: 128, 4: 256, 5: 256, 6: 512, 7: 512, 8: 512}
LENGTH_SCALE = {1: 2, 2: 2, 3: 2, 4: 4, 5: 4, 6: 8, 7: 8, 8: 8}

STRING_CAP = 200


@dataclass(frozen=True)
class StoryIdentity:
    version: int
    release: int
    serial: str
    checksum: str  # four lowercase hex digits
    sha256: str

    @property
    def type(self) -> str:
        return f"z{self.version}"

    @property
    def ifid(self) -> str:
        """Treaty of Babel IFID for a story with no embedded UUID.

        The checksum is left off when the serial looks like a 1980s/90s date
        (starts with 8 or 9), and kept otherwise (Treaty of Babel, 2.2.2.1).
        """
        base = f"ZCODE-{self.release}-{self.serial}"
        if re.fullmatch(r"[89]\d{5}", self.serial):
            return base
        return f"{base}-{self.checksum.upper()}"

    @property
    def ifid_variants(self) -> list[str]:
        """Both spellings, for looking a story up on IFDB."""
        base = f"ZCODE-{self.release}-{self.serial}"
        with_sum = f"{base}-{self.checksum.upper()}"
        return [self.ifid] + [v for v in (base, with_sum) if v != self.ifid]


class NotAStory(ValueError):
    pass


def identify_story(data: bytes) -> StoryIdentity:
    """Read release, serial and checksum from a Z-machine header, after
    checking it looks like a real story (as #31's sniffer does)."""
    if len(data) < 64:
        raise NotAStory("shorter than a Z-machine header")
    version = data[0]
    if version not in MAX_STORY_LEN:
        raise NotAStory(f"version byte {version} is not 1-8")
    if len(data) > MAX_STORY_LEN[version] * 1024:
        raise NotAStory(f"{len(data)} bytes is too long for a v{version} story")
    if len(data) > CAPS["story"]:
        raise NotAStory("over the 512 KB story cap")
    high_mem = int.from_bytes(data[0x04:0x06], "big")
    dictionary = int.from_bytes(data[0x08:0x0A], "big")
    objects = int.from_bytes(data[0x0A:0x0C], "big")
    globals_ = int.from_bytes(data[0x0C:0x0E], "big")
    static_mem = int.from_bytes(data[0x0E:0x10], "big")
    for name, addr in (("high memory", high_mem), ("dictionary", dictionary),
                       ("object table", objects), ("globals", globals_),
                       ("static memory", static_mem)):
        if addr < 64 or addr >= len(data):
            raise NotAStory(f"{name} address {addr:#x} is outside the file")
    length_field = int.from_bytes(data[0x1A:0x1C], "big")
    if length_field and version >= 3:
        declared = length_field * LENGTH_SCALE[version]
        if declared > len(data):
            raise NotAStory(f"header length {declared} exceeds file size {len(data)}")
    serial_bytes = data[0x12:0x18]
    try:
        serial = serial_bytes.decode("ascii")
    except UnicodeDecodeError:
        serial = serial_bytes.hex()
    if not serial.isprintable():
        serial = serial_bytes.hex()
    return StoryIdentity(
        version=version,
        release=int.from_bytes(data[0x02:0x04], "big"),
        serial=serial,
        checksum=f"{int.from_bytes(data[0x1C:0x1E], 'big'):04x}",
        sha256=hashlib.sha256(data).hexdigest(),
    )


# --- sanitising -----------------------------------------------------------

_BIDI = set("‎‏‪‫‬‭‮⁦⁧⁨⁩")


def clean_text(s: str, cap: int = STRING_CAP) -> str:
    """Plain text: no control or bidi characters, whitespace collapsed, capped."""
    out = []
    for ch in s:
        if ch in _BIDI or unicodedata.category(ch) in ("Cc", "Cf"):
            out.append(" ")
        else:
            out.append(ch)
    text = re.sub(r"\s+", " ", "".join(out)).strip()
    return text[:cap]


# --- hint markup ----------------------------------------------------------

# Hint text may carry these tags and nothing else: no attributes, links,
# images, styles or scripts. The app renders them natively (never WebKit).
HINT_TAGS = {"p", "div", "br", "b", "strong", "i", "em", "ul", "ol", "li", "pre"}


def hint_markup_problems(data: bytes) -> list[str]:
    """Problems with the HTML fragments inside a hint JSON file."""
    from html.parser import HTMLParser

    problems: set[str] = set()

    class Check(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag not in HINT_TAGS:
                problems.add(f"tag <{tag}> not allowed")
            if attrs:
                problems.add(f"attributes on <{tag}> not allowed")

        handle_startendtag = handle_starttag

        def handle_endtag(self, tag):
            if tag not in HINT_TAGS:
                problems.add(f"tag </{tag}> not allowed")

        def handle_comment(self, data):
            problems.add("comments not allowed")

        def handle_decl(self, decl):
            problems.add("declarations not allowed")

        def handle_pi(self, data):
            problems.add("processing instructions not allowed")

    def strings(node):
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for v in node.values():
                yield from strings(v)
        elif isinstance(node, list):
            for v in node:
                yield from strings(v)

    try:
        doc = json.loads(data)
    except ValueError:
        return ["not JSON"]
    for text in strings(doc):
        if "<" in text or "&" in text:
            parser = Check(convert_charrefs=True)
            parser.feed(text)
            parser.close()
    return sorted(problems)


# --- validation -----------------------------------------------------------

_SHA = re.compile(r"[0-9a-f]{64}")
_IFDB_ID = re.compile(r"[0-9a-z]{16}")


def check_url(url: object) -> list[str]:
    if not isinstance(url, str):
        return ["source is not a string"]
    parts = urlsplit(url)
    errs = []
    if parts.scheme != "https":
        errs.append(f"{url}: not https")
    host = (parts.hostname or "").lower()
    if not host or host == "localhost" or host.endswith(".local") \
            or re.fullmatch(r"[\d.]+", host) or ":" in host:
        errs.append(f"{url}: host not allowed")
    if parts.username or parts.password:
        errs.append(f"{url}: credentials in URL")
    return errs


def check_part(part: object, where: str, allowed: set[str]) -> list[str]:
    if not isinstance(part, dict):
        return [f"{where}: not an object"]
    errs = []
    ptype = part.get("type")
    if ptype == "page" and "page" in allowed:
        return [f"{where}: {e}" for e in check_url(part.get("url"))]
    if ptype not in allowed:
        errs.append(f"{where}: type {ptype!r} not allowed here")
    if not isinstance(part.get("sha256"), str) or not _SHA.fullmatch(part["sha256"]):
        errs.append(f"{where}: sha256 missing or not 64 lowercase hex digits")
    sources = part.get("sources")
    if not isinstance(sources, list) or not sources:
        errs.append(f"{where}: sources must be a non-empty list")
    else:
        for u in sources:
            errs += [f"{where}: {e}" for e in check_url(u)]
    for key in ("title", "credit", "label"):
        if key in part and (not isinstance(part[key], str) or clean_text(part[key]) != part[key]):
            errs.append(f"{where}: {key} is not clean plain text")
    if "bytes" in part and not (isinstance(part["bytes"], int) and part["bytes"] > 0):
        errs.append(f"{where}: bytes must be a positive integer")
    return errs


def validate_manifest(m: object) -> list[str]:
    """Structural checks for one manifest. Returns a list of problems."""
    if not isinstance(m, dict):
        return ["manifest is not a JSON object"]
    errs = []
    if m.get("spelunk") != FORMAT_VERSION:
        errs.append(f"spelunk must be {FORMAT_VERSION}")
    title = m.get("title")
    if not isinstance(title, str) or not title or clean_text(title) != title:
        errs.append("title missing or not clean plain text")
    if "game" in m and (not isinstance(m["game"], str) or not _IFDB_ID.fullmatch(m["game"])):
        errs.append("game is not an IFDB id")
    extras = [k for k in ("cover", "manual", "map", "feelies", "hints") if k in m]
    if "story" not in m and not extras:
        errs.append("needs a story or at least one extra")
    if "story" in m:
        s = m["story"]
        errs += check_part(s, "story", STORY_TYPES)
        if isinstance(s, dict):
            if not isinstance(s.get("release"), int):
                errs.append("story: release must be an integer")
            if not isinstance(s.get("serial"), str) or len(s["serial"]) != 6:
                errs.append("story: serial must be six characters")
            if not isinstance(s.get("checksum"), str) or not re.fullmatch(r"[0-9a-f]{4}", s["checksum"]):
                errs.append("story: checksum must be four lowercase hex digits")
            if "ifid" in s and not isinstance(s["ifid"], str):
                errs.append("story: ifid must be a string")
    if "cover" in m:
        errs += check_part(m["cover"], "cover", IMAGE_TYPES)
    if "manual" in m:
        errs += check_part(m["manual"], "manual", {"pdf"} | IMAGE_TYPES)
    if "map" in m:
        errs += check_part(m["map"], "map", {"pdf"} | IMAGE_TYPES)
    for key, allowed in (("feelies", {"pdf"} | IMAGE_TYPES), ("hints", HINT_TYPES)):
        if key in m:
            if not isinstance(m[key], list):
                errs.append(f"{key} must be a list")
            else:
                for i, p in enumerate(m[key]):
                    errs += check_part(p, f"{key}[{i}]", allowed)
    if "links" in m:
        if not isinstance(m["links"], dict):
            errs.append("links must be an object")
        else:
            for k, u in m["links"].items():
                errs += [f"links.{k}: {e}" for e in check_url(u)]
    return errs


MAGIC = {
    "png": lambda b: b.startswith(b"\x89PNG\r\n\x1a\n"),
    "jpeg": lambda b: b.startswith(b"\xff\xd8\xff"),
    "heic": lambda b: b[4:8] == b"ftyp" and b[8:12] in (b"heic", b"heix", b"mif1", b"msf1"),
    "pdf": lambda b: b.startswith(b"%PDF-"),
}


def check_bytes(part: dict, data: bytes, where: str) -> list[str]:
    """Check downloaded bytes against a part: hash, magic bytes, cap."""
    errs = []
    if hashlib.sha256(data).hexdigest() != part["sha256"]:
        errs.append(f"{where}: sha256 mismatch")
    ptype = part["type"]
    if ptype in STORY_TYPES:
        try:
            ident = identify_story(data)
        except NotAStory as e:
            return errs + [f"{where}: not a Z-machine story ({e})"]
        if ident.type != ptype:
            errs.append(f"{where}: header says {ident.type}, manifest says {ptype}")
        for key in ("release", "serial", "checksum"):
            if key in part and part[key] != getattr(ident, key):
                errs.append(f"{where}: {key} {part[key]!r} != header {getattr(ident, key)!r}")
    elif ptype == "hints-json":
        errs += [f"{where}: {p}" for p in hint_markup_problems(data)]
    elif ptype in MAGIC and not MAGIC[ptype](data):
        errs.append(f"{where}: bytes are not {ptype}")
    cap = CAPS["story"] if ptype in STORY_TYPES else CAPS["image"] if ptype in IMAGE_TYPES \
        else CAPS["pdf"] if ptype == "pdf" else CAPS["hints"]
    if len(data) > cap:
        errs.append(f"{where}: {len(data)} bytes is over the cap")
    return errs
