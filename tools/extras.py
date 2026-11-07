"""Find manuals, maps, feelies and hints for a game, from the sources named in
ahacop/spelunk#17, #18 and #22:

- IFDB's links for the game (manuals, maps, feelies; hint files and pages),
- the Infocom Documentation Project's InvisiClues story files
  (https://infodoc.plover.net/hints/<name>.z5),
- infocom-hints-html's scraped JSON (github.com/dfabulich/infocom-hints-html),
  pinned to one commit so its hash stays stable.

Every candidate is downloaded once (cached in data/extras.json), typed by its
magic bytes and hashed. Anything that isn't PNG, JPEG, HEIC, PDF, a Z-machine
hint story or hint JSON is skipped and listed in data/skipped-extras.json:
GIF, SVG, TIFF and zips never get into a manifest.
"""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit

from spelunklib import CAPS, MAGIC, NotAStory, check_url, clean_text, identify_story

INFODOC_HINT = "https://infodoc.plover.net/hints/{}.z5"
HINTS_REPO_API = "https://api.github.com/repos/dfabulich/infocom-hints-html"
HINTS_RAW = "https://raw.githubusercontent.com/dfabulich/infocom-hints-html/{sha}/{path}"

HINT_WORDS = re.compile(r"\b(hint|hints|invisiclue|invisiclues|clue ?book)\b", re.I)
MAP_WORDS = re.compile(r"\bmaps?\b", re.I)
MANUAL_WORDS = re.compile(r"\b(manual|instructions|documentation|docs|booklet|guide)\b", re.I)
FEELIE_WORDS = re.compile(r"\b(feelies?|package|box|leaflet|letter|dossier|props?|insert|card|poster|scans?)\b", re.I)
SKIP_WORDS = re.compile(r"\b(walkthrough|solution|transcript|source|review|interview|map ?maker)\b", re.I)


def https(url: str) -> str:
    """IFDB has many http:// links; try them over https (the app only fetches https)."""
    p = urlsplit(url)
    return urlunsplit(("https",) + tuple(p)[1:]) if p.scheme == "http" else url


def sniff(data: bytes) -> str | None:
    for t, test in MAGIC.items():
        if test(data):
            return t
    try:
        return identify_story(data).type
    except NotAStory:
        pass
    try:
        if isinstance(json.loads(data), (dict, list)):
            return "json"
    except ValueError:
        pass
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if b"<svg" in data[:1024].lower():
        return "svg"
    if data[:4] == b"PK\x03\x04":
        return "zip"
    if data.lstrip()[:15].lower().startswith((b"<!doctype html", b"<html")):
        return "html"
    return None


def classify(link: dict) -> str | None:
    """Which slot an IFDB link belongs in, from its title and description."""
    if link.get("isGame"):
        return None
    text = " ".join(str(link.get(k) or "") for k in ("title", "desc", "fmtname"))
    if SKIP_WORDS.search(text):
        return None
    if HINT_WORDS.search(text):
        return "hints"
    if MAP_WORDS.search(text):
        return "map"
    if MANUAL_WORDS.search(text):
        return "manual"
    if FEELIE_WORDS.search(text):
        return "feelies"
    return None


class Extras:
    def __init__(self, fetcher, cache: dict, skipped: list, log, offline: bool):
        self.fetcher, self.cache, self.skipped, self.log = fetcher, cache, skipped, log
        self.offline = offline
        self._hint_index: dict[str, str] | None = None

    # -- downloading -------------------------------------------------------

    def fetch(self, url: str, cap: int) -> dict:
        """Download and type one candidate, cached by URL."""
        if url in self.cache or self.offline:
            return self.cache.get(url, {"error": "not cached"})
        if check_url(url):
            entry = {"error": "url not allowed"}
        else:
            self.log(f"GET {url}")
            data = self.fetcher.get(url, cap)
            if data is None:
                entry = {"error": "not found"}
            else:
                entry = {"type": sniff(data), "sha256": hashlib.sha256(data).hexdigest(),
                         "bytes": len(data)}
        self.cache[url] = entry
        return entry

    def part(self, url: str, slot: str, title: str = "") -> dict | None:
        cap = CAPS["hints"] if slot == "hints" else CAPS["pdf"]
        e = self.fetch(url, cap)
        t = e.get("type")
        ok = {"hints": {"z5", "z8", "json"}}.get(slot, {"png", "jpeg", "heic", "pdf"})
        if "error" in e or t not in ok:
            self.skipped.append({"url": url, "slot": slot, "why": e.get("error") or f"type {t}"})
            return None
        p = {"type": "hints-json" if t == "json" else t, "sha256": e["sha256"],
             "bytes": e["bytes"], "sources": [url]}
        if title:
            p["title"] = clean_text(title, 80)
        return p

    # -- sources -----------------------------------------------------------

    def hint_index(self) -> dict[str, str]:
        """Map a short game name (e.g. zork1) to a pinned raw URL of its scraped JSON."""
        if self._hint_index is not None:
            return self._hint_index
        self._hint_index = load_hint_index(self) if not self.offline else {}
        return self._hint_index

    def for_game(self, stem: str, ifdb_links: list[dict]) -> dict:
        """Extras for one game. stem is eblong's short name, e.g. zork1."""
        out: dict = {"feelies": [], "hints": []}
        for link in ifdb_links:
            slot = classify(link)
            url = link.get("url")
            if not slot or not isinstance(url, str):
                continue
            url = https(url)
            if slot == "hints" and not re.search(r"\.(z5|z8|json)$", urlsplit(url).path, re.I):
                if not check_url(url):
                    out["hints"].append({"type": "page", "url": url})
                continue
            p = self.part(url, slot, link.get("title") or "")
            if not p:
                continue
            if slot in ("manual", "map"):
                out.setdefault(slot, p)
            else:
                out[slot].append(p)
        if stem:
            p = self.part(INFODOC_HINT.format(stem), "hints", "InvisiClues")
            if p:
                out["hints"].append(p)
            raw = self.hint_index().get(stem)
            if raw:
                p = self.part(raw, "hints", "InvisiClues")
                if p:
                    out["hints"].append(p)
        # one entry per file
        for key in ("feelies", "hints"):
            seen, uniq = set(), []
            for p in out[key]:
                k = p.get("sha256") or p.get("url")
                if k not in seen:
                    seen.add(k)
                    uniq.append(p)
            out[key] = uniq
        return {k: v for k, v in out.items() if v}


def load_hint_index(ex: Extras) -> dict[str, str]:
    raw = ex.fetcher.get(f"{HINTS_REPO_API}/commits/HEAD", CAPS["manifest"])
    if not raw:
        return {}
    sha = json.loads(raw).get("sha", "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        return {}
    raw = ex.fetcher.get(f"{HINTS_REPO_API}/git/trees/{sha}?recursive=1", CAPS["image"])
    if not raw:
        return {}
    index = {}
    for item in json.loads(raw).get("tree", []):
        path = item.get("path", "")
        m = re.fullmatch(r"scraped-json/([A-Za-z0-9_-]+)\.json", path)
        if m:
            index[m.group(1).lower()] = HINTS_RAW.format(sha=sha, path=path)
    ex.log(f"infocom-hints-html @ {sha[:7]}: {len(index)} hint files")
    return index


def merge(manifest: dict, extras: dict) -> dict:
    """Add generated extras without touching ones added by hand: a slot that
    is already filled stays, and list entries are added only if new."""
    for slot in ("manual", "map"):
        if slot in extras and slot not in manifest:
            manifest[slot] = extras[slot]
    for slot in ("feelies", "hints"):
        have = manifest.setdefault(slot, [])
        keys = {p.get("sha256") or p.get("url") for p in have}
        for p in extras.get(slot, []):
            if (p.get("sha256") or p.get("url")) not in keys:
                have.append(p)
        if not have:
            del manifest[slot]
    return manifest
