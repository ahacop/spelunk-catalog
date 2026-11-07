"""Find box art, the manual and InvisiClues for a game, from the sources named in
ahacop/spelunk#17, #18 and #22:

- IFDB's links for the game (manuals, maps, feelies; hint files and pages),
- the Infocom Documentation Project (infodoc.plover.net): its manuals and
  maps PDFs and InvisiClues story files, from its index pages,
- infocom-hints-html's scraped JSON (github.com/dfabulich/infocom-hints-html),
  pinned to one commit so its hash stays stable.

Hints are files only (hint .z5/.z8 or JSON) for the app's own viewer; hint
web pages are never added.

Every candidate is downloaded once (cached in data/extras.json), typed by its
magic bytes and hashed. Anything that isn't PNG, JPEG, HEIC, PDF, a Z-machine
hint story or hint JSON is skipped and listed in data/skipped-extras.json:
GIF, SVG, TIFF and zips never get into a manifest.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from spelunklib import (
    CAPS, MAGIC, NotAStory, check_url, clean_text, hint_markup_problems, identify_story,
)

OK_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".heic", ".z5", ".z8", ".json"}
INFODOC_INDEX = "https://infodoc.plover.net/{}/index.html"  # manuals, maps, hints
HINTS_REPO = "https://github.com/dfabulich/infocom-hints-html"
# eblong's names -> the 8-character names infodoc and infocom-hints-html use,
# where cutting to 8 characters isn't enough (beyondzork -> beyondzo works).
HINT_NAMES = {"hitchhiker": "hhgg", "leathergoddesses": "lgop", "lurkinghorror": "lurking"}
INFODOC_NAMES = {"leathergoddesses": "leather"}
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
    text = " ".join(str(link.get(k) or "") for k in ("title", "desc", "format", "fmtname"))
    if SKIP_WORDS.search(text):
        return None
    if HINT_WORDS.search(text):
        return "hints"
    if MAP_WORDS.search(text):
        return None  # no maps, fan-made or otherwise
    if MANUAL_WORDS.search(text):
        return "manual"
    return None


class Extras:
    def __init__(self, fetcher, cache: dict, skipped: list, log, offline: bool):
        self.fetcher, self.cache, self.skipped, self.log = fetcher, cache, skipped, log
        self.offline = offline
        self._hints_commit: str | None = None
        self._infodoc: dict[str, dict[str, str]] = {}

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
                # Not cached, so a dead or blocked host is retried next run.
                return {"error": "download failed"}
            else:
                entry = {"type": sniff(data), "sha256": hashlib.sha256(data).hexdigest(),
                         "bytes": len(data)}
                if entry["type"] == "json":
                    entry["markup"] = hint_markup_problems(data)
        self.cache[url] = entry
        return entry

    def part(self, url: str, slot: str, title: str = "") -> dict | None:
        cap = CAPS["image"] if slot == "cover" else CAPS["pdf"]
        e = self.fetch(url, cap)
        t = e.get("type")
        ok = {"hints": {"z5", "z8", "json", "pdf", "png", "jpeg", "heic"},
              "cover": {"png", "jpeg", "heic"}}.get(
            slot, {"png", "jpeg", "heic", "pdf"})
        if e.get("markup"):
            self.skipped.append({"url": url, "slot": slot, "why": "; ".join(e["markup"])})
            return None
        if "error" in e or t not in ok:
            self.skipped.append({"url": url, "slot": slot, "why": e.get("error") or f"type {t}"})
            return None
        p = {"type": "hints-json" if t == "json" else t, "sha256": e["sha256"],
             "bytes": e["bytes"], "sources": [url]}
        if title:
            p["title"] = clean_text(title, 80)
        return p

    # -- sources -----------------------------------------------------------

    def hints_commit(self) -> str:
        """infocom-hints-html's current commit, so its JSON URLs (and hashes) are pinned."""
        if self._hints_commit is None:
            self._hints_commit = "" if self.offline else ls_remote_head(HINTS_REPO)
            key = f"commit:{HINTS_REPO}"
            if self._hints_commit:
                self.cache[key] = {"sha": self._hints_commit}
            elif key in self.cache:  # offline: the commit used last time
                self._hints_commit = self.cache[key]["sha"]
            if self._hints_commit:
                self.log(f"infocom-hints-html @ {self._hints_commit[:7]}")
        return self._hints_commit

    def infodoc(self, kind: str) -> dict[str, str]:
        """The Infocom Documentation Project's files of one kind (manuals,
        maps or hints), by 8-character name, e.g. {"zork1": "https://…/maps/zork1.pdf"}."""
        if kind not in self._infodoc:
            index: dict[str, str] = {}
            key = f"index:{INFODOC_INDEX.format(kind)}"
            raw = None if self.offline else self.fetcher.get(INFODOC_INDEX.format(kind), CAPS["manifest"])
            if raw:
                self.cache[key] = {"html": raw.decode("utf-8", "replace")}
            elif key in self.cache:  # offline, or infodoc unreachable: last copy
                raw = self.cache[key]["html"].encode()
            base = INFODOC_INDEX.format(kind)
            for href in re.findall(r'href="([^"]+\.(?:pdf|z5|z8))"', (raw or b"").decode("utf-8", "replace"), re.I):
                url = urljoin(base, href)
                index.setdefault(Path(urlsplit(url).path).stem.lower(), url)
            self._infodoc[kind] = index
            if raw:
                self.log(f"infodoc {kind}: {len(index)} PDFs")
        return self._infodoc[kind]

    def for_game(self, stem: str, ifdb_links: list[dict], cover_url: str = "") -> dict:
        """Extras for one game: box art, manual (with its feelies) and
        InvisiClues. stem is eblong's short name, e.g. zork1."""
        out: dict = {"feelies": [], "hints": []}
        if cover_url:
            p = self.part(cover_url, "cover")
            if p:
                p["credit"] = "Cover art from IFDB"
                out["cover"] = p
        for link in ifdb_links:
            slot = classify(link)
            url = link.get("url")
            if not slot or not isinstance(url, str):
                continue
            url = https(url)
            if slot == "hints" and not re.search(r"\.(z5|z8|json)$", urlsplit(url).path, re.I):
                # Hints are files the app shows in its own viewer; no web pages.
                self.skipped.append({"url": url, "slot": slot, "why": f"not a hint file ({link.get('format')})"})
                continue
            ext = Path(urlsplit(url).path).suffix.lower()
            if ext and ext not in OK_EXT:
                # Don't download what can't go in a manifest anyway.
                self.skipped.append({"url": url, "slot": slot, "why": f"file type {ext}"})
                continue
            p = self.part(url, slot, link.get("title") or "")
            if not p:
                continue
            if slot == "manual":
                out.setdefault(slot, p)
            else:
                out[slot].append(p)
        if stem:
            name = HINT_NAMES.get(stem, stem[:8])
            names = [n for n in (INFODOC_NAMES.get(stem), HINT_NAMES.get(stem), stem[:8]) if n]
            # InvisiClues: infodoc's hint story file and the booklet's map (by Infocom).
            for slot, kind, title in (("manual", "manuals", "Manual"), ("hints", "hints", "InvisiClues"),
                                      ("hints", "maps", "InvisiClues map")):
                url = next((self.infodoc(kind)[n] for n in names if n in self.infodoc(kind)), None)
                if not url or (slot != "hints" and slot in out):
                    continue
                p = self.part(url, slot, title)
                if p and slot == "hints":
                    out["hints"].append(p)
                elif p:
                    out[slot] = p
            sha = self.hints_commit()
            if sha:
                p = self.part(HINTS_RAW.format(sha=sha, path=f"scraped-json/{name}.json"),
                              "hints", "InvisiClues")
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


def ls_remote_head(repo: str) -> str:
    try:
        out = subprocess.run(["git", "ls-remote", repo, "HEAD"], capture_output=True,
                             text=True, timeout=60, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    sha = out.split()[0] if out.split() else ""
    return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else ""


def merge(manifest: dict, extras: dict) -> dict:
    """Add generated extras without touching ones added by hand: a slot that
    is already filled stays, and list entries are added only if new."""
    for slot in ("cover", "manual"):
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
