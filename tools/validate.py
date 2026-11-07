#!/usr/bin/env python3
"""Validate .spelunk manifests.

    python3 tools/validate.py                       # check every manifest's structure
    python3 tools/validate.py --download FILE...    # also fetch and verify these files' sources

Structure: the version-1 format from ahacop/spelunk#31, https-only sources,
clean plain-text strings, allowed part types only (no SVG), and the path
games/<game>/r<release>-s<serial>.spelunk. With --download every source of
every part is fetched and checked for sha256, magic bytes, size cap, and for
stories the header's release, serial and checksum.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spelunklib import CAPS, STORY_TYPES, check_bytes, validate_manifest  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "spelunk-catalog-validator/1 (+https://github.com/ahacop/spelunk-catalog)"


def check_file(path: Path) -> tuple[list[str], dict | None]:
    raw = path.read_bytes()
    if len(raw) > CAPS["manifest"]:
        return ["over the 256 KB manifest cap"], None
    try:
        m = json.loads(raw)
    except ValueError as e:
        return [f"not JSON: {e}"], None
    errs = validate_manifest(m)
    if isinstance(m, dict) and any(isinstance(h, dict) and h.get("type") == "page" for h in m.get("hints", []) or []):
        errs.append("hints must be files for the app's viewer, not web pages")
    rel = path.resolve().relative_to(ROOT)
    if rel.parts[0] == "games" and isinstance(m, dict) and isinstance(m.get("story"), dict):
        s = m["story"]
        want = Path("games", str(m.get("game")), f"r{s.get('release')}-s{s.get('serial')}.spelunk")
        if rel != want:
            errs.append(f"should be at {want}")
    return errs, m


def parts(m: dict):
    for key in ("story", "cover", "manual"):
        if key in m:
            yield key, m[key]
    for key in ("feelies", "hints"):
        for i, p in enumerate(m.get(key, [])):
            yield f"{key}[{i}]", p


def download(url: str, cap: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        if urlsplit(resp.geturl()).scheme != "https":
            raise RuntimeError(f"redirected to non-https {resp.geturl()}")
        data = resp.read(cap + 1)
    if len(data) > cap:
        raise RuntimeError("over the size cap")
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--download", nargs="*", metavar="FILE", help="manifests whose sources to fetch")
    ap.add_argument("--delay", type=float, default=5.0, help="seconds between downloads")
    args = ap.parse_args()

    failed = 0
    seen: dict[str, Path] = {}
    files = sorted((ROOT / "games").glob("*/*.spelunk"))
    for path in files:
        errs, m = check_file(path)
        sha = (m or {}).get("story", {}).get("sha256") if isinstance(m, dict) else None
        if isinstance(sha, str):
            if sha in seen:
                errs.append(f"same story as {seen[sha].relative_to(ROOT)}")
            seen[sha] = path
        if errs:
            failed += 1
            for e in errs:
                print(f"{path.relative_to(ROOT)}: {e}")
    print(f"{len(files)} manifests checked, {failed} with problems")

    for name in args.download or []:
        path = Path(name).resolve()
        errs, m = check_file(path)
        if errs or m is None:
            continue  # already reported above
        for where, part in parts(m):
            if part.get("type") == "page":
                continue
            ptype = part["type"]
            cap = CAPS["story"] if ptype in STORY_TYPES else max(CAPS.values())
            for url in part["sources"]:
                try:
                    data = download(url, cap)
                    problems = check_bytes(part, data, f"{where} {url}")
                except Exception as e:  # noqa: BLE001 - report every failure kind
                    problems = [f"{where} {url}: {e}"]
                for p in problems:
                    print(f"{path.relative_to(ROOT)}: {p}")
                failed += bool(problems)
                print(f"  {'ok ' if not problems else 'BAD'} {url}")
                time.sleep(args.delay)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
