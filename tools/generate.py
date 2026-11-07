#!/usr/bin/env python3
"""Generate one .spelunk manifest per Z-machine story release on eblong.com/infocom.

    python3 tools/generate.py              # fetch only what isn't cached yet
    python3 tools/generate.py --offline    # rebuild manifests from the caches only
    python3 tools/generate.py --recheck    # re-download every story to catch changes

Politeness: one request at a time, at least --delay seconds apart per host
(default 5), Retry-After honoured on 429/503, and it stops after repeated
failures. Story hashes are cached in data/stories.json and IFDB lookups in
data/ifdb.json, so a normal refresh only downloads new files.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlsplit, quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spelunklib import (  # noqa: E402
    CAPS, NotAStory, StoryIdentity, clean_text, identify_story, validate_manifest,
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
GAMES = ROOT / "games"

EBLONG_BASE = "https://eblong.com/infocom/"
EBLONG_CATALOG = urljoin(EBLONG_BASE, "catalog.json")
IFDB_IFICTION = "https://ifdb.org/viewgame?ifiction&ifid={}"
USER_AGENT = "spelunk-catalog-generator/1 (+https://github.com/ahacop/spelunk-catalog)"
STORY_EXT = re.compile(r"\.z[1-8]$", re.I)


class Fetcher:
    def __init__(self, delay: float, max_failures: int = 5):
        self.delay = delay
        self.last: dict[str, float] = {}
        self.failures = 0
        self.max_failures = max_failures

    def get(self, url: str, cap: int) -> bytes | None:
        """GET url, or None for a 404. Raises after too many other failures."""
        host = urlsplit(url).hostname or ""
        for attempt in range(4):
            wait = self.last.get(host, 0) + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.last[host] = time.monotonic()
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    if urlsplit(resp.geturl()).scheme != "https":
                        raise RuntimeError(f"{url} redirected to non-https {resp.geturl()}")
                    data = resp.read(cap + 1)
                    if len(data) > cap:
                        raise RuntimeError(f"{url} is over {cap} bytes")
                    self.failures = 0
                    return data
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code in (429, 503):
                    retry = e.headers.get("Retry-After", "")
                    pause = int(retry) if retry.isdigit() else 60 * (attempt + 1)
                    log(f"  {e.code} from {host}, pausing {pause}s")
                    time.sleep(min(pause, 900))
                    continue
                self._fail(f"{url}: HTTP {e.code}")
                return None
            except (urllib.error.URLError, TimeoutError) as e:
                self._fail(f"{url}: {e}")
                time.sleep(self.delay * (attempt + 2))
        self._fail(f"{url}: gave up after retries")
        return None

    def _fail(self, msg: str) -> None:
        self.failures += 1
        log(f"  ! {msg}")
        if self.failures >= self.max_failures:
            raise SystemExit(f"Stopping: {self.failures} failures in a row (last: {msg})")


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def load(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


# --- discovery ------------------------------------------------------------

def discover(fetcher: Fetcher) -> dict[str, str]:
    """Map each story URL on eblong to a title hint (may be empty).

    Walks eblong's catalog.json for any string naming a .z1-.z8 file, taking
    the nearest "title"/"name" as a hint. Falls back to the index page's links.
    """
    found: dict[str, str] = {}
    raw = fetcher.get(EBLONG_CATALOG, CAPS["image"])
    if raw:
        def walk(node, title=""):
            if isinstance(node, dict):
                t = node.get("title") or node.get("name") or title
                t = t if isinstance(t, str) else title
                for v in node.values():
                    walk(v, t)
            elif isinstance(node, list):
                for v in node:
                    walk(v, title)
            elif isinstance(node, str) and STORY_EXT.search(urlsplit(node).path):
                url = urljoin(EBLONG_BASE, node if "/" in node else f"gamefiles/{node}")
                found.setdefault(url, title)
        walk(json.loads(raw))
        log(f"catalog.json lists {len(found)} story files")
    if not found:
        page = fetcher.get(EBLONG_BASE, CAPS["image"])
        for href in re.findall(r'href="([^"]+)"', (page or b"").decode("utf-8", "replace")):
            href = html.unescape(href)
            if STORY_EXT.search(urlsplit(href).path):
                found.setdefault(urljoin(EBLONG_BASE, href), "")
        log(f"index page links {len(found)} story files")
    return {u: t for u, t in found.items() if u.startswith("https://")}


# --- IFDB -----------------------------------------------------------------

def ifdb_lookup(fetcher: Fetcher, ifid: str) -> dict | None:
    raw = fetcher.get(IFDB_IFICTION.format(quote(ifid)), CAPS["manifest"])
    if not raw:
        return None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    def first(tag):
        el = next((e for e in root.iter() if e.tag.split("}")[-1] == tag), None)
        return (el.text or "").strip() if el is not None else ""
    tuid = first("tuid")
    if not re.fullmatch(r"[0-9a-z]{16}", tuid):
        return None
    return {"tuid": tuid, "title": first("title"), "author": first("author")}


# --- main -----------------------------------------------------------------

def manifest_for(url: str, ident: StoryIdentity, nbytes: int, game: dict) -> dict:
    return {
        "spelunk": 1,
        "title": clean_text(game["title"]) or "Untitled",
        "game": game["tuid"],
        "story": {
            "ifid": ident.ifid,
            "release": ident.release,
            "serial": ident.serial,
            "checksum": ident.checksum,
            "label": f"Release {ident.release} ({ident.serial})",
            "type": ident.type,
            "sha256": ident.sha256,
            "bytes": nbytes,
            "sources": [url],
        },
        "links": {"ifdb": f"https://ifdb.org/viewgame?id={game['tuid']}"},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--delay", type=float, default=5.0, help="seconds between requests to one host")
    ap.add_argument("--offline", action="store_true", help="use cached data only")
    ap.add_argument("--recheck", action="store_true", help="re-download stories already hashed")
    ap.add_argument("--limit", type=int, default=0, help="stop after downloading N new stories")
    args = ap.parse_args()

    fetcher = Fetcher(args.delay)
    stories = load(DATA / "stories.json", {})  # url -> {sha256, bytes, version, release, serial, checksum} or {error}
    ifdb = load(DATA / "ifdb.json", {})        # ifid -> {tuid, title, author} or null

    if args.offline:
        urls = {u: v.get("hint", "") for u, v in stories.items()}
    else:
        urls = discover(fetcher)
        if not urls:
            log("No story files found on eblong; nothing changed.")
            return 1

    downloaded = 0
    for url, hint in sorted(urls.items()):
        entry = stories.get(url)
        if args.offline or (entry and "sha256" in entry and not args.recheck):
            continue
        if args.limit and downloaded >= args.limit:
            break
        log(f"GET {url}")
        data = fetcher.get(url, CAPS["story"])
        downloaded += 1
        if data is None:
            stories[url] = {"error": "not found", "hint": hint}
        else:
            try:
                i = identify_story(data)
                if entry and "sha256" in entry and entry["sha256"] != i.sha256:
                    log(f"  ! bytes changed since last run: {url}")
                stories[url] = {"sha256": i.sha256, "bytes": len(data), "version": i.version,
                                "release": i.release, "serial": i.serial,
                                "checksum": i.checksum, "hint": hint}
            except NotAStory as e:
                stories[url] = {"error": f"not a story: {e}", "hint": hint}
        save(DATA / "stories.json", stories)  # checkpoint after every download

    written: set[Path] = set()
    unmatched = []
    seen_sha: dict[str, str] = {}
    for url, s in sorted(stories.items()):
        if url not in urls or "sha256" not in s:
            continue
        ident = StoryIdentity(s["version"], s["release"], s["serial"], s["checksum"], s["sha256"])
        if ident.sha256 in seen_sha:  # same bytes at two URLs: add as a mirror
            path = seen_sha[ident.sha256]
            m = json.loads(Path(path).read_text())
            if url not in m["story"]["sources"]:
                m["story"]["sources"].append(url)
                Path(path).write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
            continue
        game = None
        for ifid in ident.ifid_variants:
            if ifid not in ifdb and not args.offline:
                log(f"IFDB {ifid}")
                ifdb[ifid] = ifdb_lookup(fetcher, ifid)
                save(DATA / "ifdb.json", ifdb)
            if ifdb.get(ifid):
                game = ifdb[ifid]
                break
        if not game:
            unmatched.append({"url": url, "ifid": ident.ifid, "hint": s.get("hint", "")})
            continue
        m = manifest_for(url, ident, s["bytes"], game)
        problems = validate_manifest(m)
        if problems:
            log(f"  ! invalid manifest for {url}: {problems}")
            continue
        path = GAMES / game["tuid"] / f"r{ident.release}-s{clean_text(ident.serial)}.spelunk"
        if path.exists():
            # Keep hand-added extras, title and links; refresh only the story,
            # keeping any extra mirrors after the eblong source.
            old = json.loads(path.read_text())
            mirrors = [u for u in old.get("story", {}).get("sources", []) if u != url]
            label = old.get("story", {}).get("label")
            old["story"] = m["story"]
            old["story"]["sources"] += mirrors
            if label:
                old["story"]["label"] = label
            m = old
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
        written.add(path)
        seen_sha[ident.sha256] = str(path)

    save(DATA / "unmatched.json", unmatched)
    stale = [p for p in GAMES.glob("*/*.spelunk") if p not in written]
    log(f"\n{len(written)} manifests written, {len(unmatched)} stories not on IFDB "
        f"(data/unmatched.json), {downloaded} downloads this run.")
    if stale:
        log("Manifests no longer matching an eblong file (not deleted):")
        for p in stale:
            log(f"  {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
