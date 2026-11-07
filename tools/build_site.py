#!/usr/bin/env python3
"""Build the catalog website into docs/ for GitHub Pages (Settings > Pages >
Deploy from branch > main, /docs).

    python3 tools/build_site.py

Pages: an index of games (A–Z, and by year in years.html), and one page per game listing its releases (the
default first) with "Open in Spelunk" (spelunk://import?url=…) and a download
link. The manifests are copied into docs/games/ so they're served from the
same site. Box art is shown from its source (IFDB); nothing else is hosted.
Standard library only; all text is HTML-escaped.
"""

from __future__ import annotations

import html
import json
import shutil
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs"
SITE = "https://ahacop.github.io/spelunk-catalog/"

CSS = """
:root { --bg:#f6f1e7; --fg:#1d1a16; --muted:#6b6257; --card:#fffdf8; --line:#e2d8c6;
        --accent:#8a3b12; --accent-fg:#fff; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14120f; --fg:#efe8dc; --muted:#a39886; --card:#1d1a16; --line:#332d25;
          --accent:#e08a4f; --accent-fg:#14120f; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font: 16px/1.5 ui-serif, Georgia, "Times New Roman", serif; }
a { color: var(--accent); }
header, main, footer { max-width: 960px; margin: 0 auto; padding: 0 16px; }
header { padding-top: 32px; }
h1 { font-size: 2rem; margin: 0 0 4px; letter-spacing: .02em; }
.sub { color: var(--muted); margin: 0 0 24px; }
.grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 16px;
        list-style:none; padding:0; margin:0 0 40px; }
.grid a { display:block; text-decoration:none; color:inherit; }
.cover { width:100%; aspect-ratio: 3/4; object-fit: cover; border-radius:6px;
         background: var(--line); display:block; border:1px solid var(--line); }
.nocover { display:flex; align-items:center; justify-content:center; text-align:center;
           padding: 12px; color: var(--muted); font-size: .9rem; }
.grid .t { font-weight: 600; margin-top: 6px; line-height:1.25; }
.grid .n { color: var(--muted); font-size: .85rem; }
.game { display:grid; grid-template-columns: 200px 1fr; gap: 24px; margin: 8px 0 32px; }
@media (max-width: 560px) { .game { grid-template-columns: 1fr; } .game .cover { max-width: 220px; } }
.release { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
           padding: 14px 16px; margin: 0 0 12px; }
.release h3 { margin: 0 0 4px; font-size: 1.05rem; }
.badge { display:inline-block; font: 600 .7rem/1.6 system-ui, sans-serif; text-transform: uppercase;
         letter-spacing:.06em; background: var(--accent); color: var(--accent-fg);
         padding: 0 8px; border-radius: 99px; margin-left: 6px; vertical-align: 2px; }
.has { color: var(--muted); font-size: .9rem; margin: 0 0 10px; }
.btn { display:inline-block; font: 600 .9rem/1 system-ui, sans-serif; padding: 10px 14px;
       border-radius: 6px; text-decoration:none; margin: 0 8px 6px 0; }
.btn.primary { background: var(--accent); color: var(--accent-fg); }
.btn.secondary { border: 1px solid var(--line); color: var(--fg); }
.views { font: 600 .9rem/1 system-ui, sans-serif; margin: 0 0 20px; }
.views a, .views span { display:inline-block; padding: 8px 12px; border-radius: 99px; margin-right: 6px; }
.views span { background: var(--accent); color: var(--accent-fg); }
.views a { border: 1px solid var(--line); color: var(--fg); text-decoration: none; }
h2.year { font-size: 1.4rem; margin: 0 0 12px; border-bottom: 1px solid var(--line); padding-bottom: 4px; }
details { margin: 8px 0 24px; } summary { cursor: pointer; color: var(--muted); }
footer { color: var(--muted); font-size: .85rem; padding-bottom: 40px; }
code { font-size: .85em; }
"""


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def page(title: str, body: str, depth: int) -> str:
    up = "../" * depth
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<link rel="stylesheet" href="{up}style.css">
</head>
<body>
<header>{f'<p class="sub"><a href="{up}index.html">← All games</a></p>' if depth else ''}</header>
<main>
{body}
</main>
<footer>
<p>Each <code>.spelunk</code> file describes one release and links to files hosted elsewhere
(eblong.com, IFDB, the Infocom Documentation Project, infocom-hints-html). This site hosts only the
manifests. Spelunk checks every file's SHA-256 before using it.</p>
</footer>
</body>
</html>
"""


def cover_img(m: dict, alt: str) -> str:
    c = m.get("cover")
    if c and c.get("sources"):
        return f'<img class="cover" src="{esc(c["sources"][0])}" alt="{esc(alt)}" loading="lazy" referrerpolicy="no-referrer">'
    return f'<div class="cover nocover">{esc(alt)}</div>'


def contents(m: dict) -> str:
    bits = ["Story file"]
    if "cover" in m:
        bits.append("box art")
    if "manual" in m:
        bits.append("manual")
    hints = m.get("hints", [])
    if hints:
        maps = sum(1 for h in hints if h.get("type") in ("pdf", "png", "jpeg", "heic"))
        bits.append("InvisiClues" + (" with map" if maps else ""))
    return ", ".join(bits)


def first_serial(paths: list[str]) -> str:
    """The earliest YYMMDD serial among a game's releases, to order games
    released in the same year (Infocom's serials are build dates)."""
    serials = []
    for p in paths:
        serial = json.loads((ROOT / p).read_text())["story"]["serial"]
        if serial.isdigit() and serial[0] in "789":
            serials.append(serial)
    return min(serials, default="999999")


def views(current: str) -> str:
    out = []
    for name, href in (("A–Z", "index.html"), ("By year", "years.html")):
        out.append(f"<span>{name}</span>" if name == current else f'<a href="{href}">{name}</a>')
    return f'<p class="views">{"".join(out)}</p>'


def main() -> int:
    index = json.loads((ROOT / "data" / "index.json").read_text())
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "games").mkdir(parents=True)
    (OUT / "style.css").write_text(CSS.strip() + "\n")
    (OUT / ".nojekyll").write_text("")

    cards = {}
    games = sorted(index.items(), key=lambda kv: kv[1]["title"].lower().removeprefix("the "))
    for tuid, g in games:
        default = json.loads((ROOT / g["default"]).read_text())
        rel_html = []
        for r in g["releases"]:
            src = ROOT / r["path"]
            m = json.loads(src.read_text())
            dest = OUT / r["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            url = SITE + r["path"]
            is_default = r["path"] == g["default"]
            badge = '<span class="badge">Recommended</span>' if is_default else ""
            rel_html.append(f"""<div class="release">
<h3>{esc(m["story"]["label"])}{badge}</h3>
<p class="has">{esc(contents(m))}</p>
<a class="btn primary" href="spelunk://import?url={esc(quote(url, safe=''))}">Open in Spelunk</a>
<a class="btn secondary" href="{esc(Path(r["path"]).name)}" download>Download .spelunk</a>
</div>""")
        first, rest = rel_html[0], rel_html[1:]
        more = (f"<details><summary>{len(rest)} other release{'s' if len(rest) != 1 else ''}"
                f"</summary>{''.join(rest)}</details>") if rest else ""
        ifdb = default.get("links", {}).get("ifdb")
        body = f"""<div class="game">
<div>{cover_img(default, g["title"])}</div>
<div>
<h1>{esc(g["title"])}</h1>
<p class="sub">{f"{esc(g['published'])} · " if g.get("published") else ""}{len(g["releases"])} release{'s' if len(g["releases"]) != 1 else ''}{f' · <a href="{esc(ifdb)}">IFDB</a>' if ifdb else ''}</p>
{first}
{more}
</div>
</div>"""
        (OUT / "games" / tuid / "index.html").write_text(page(g["title"], body, 2))
        year = g.get("published")
        cards[tuid] = (f"""<li><a href="games/{esc(tuid)}/index.html">{cover_img(default, g["title"])}
<div class="t">{esc(g["title"])}</div>
<div class="n">{f"{year} · " if year else ""}{len(g["releases"])} release{'s' if len(g["releases"]) != 1 else ''}</div></a></li>""")

    body = f"""<h1>Spelunk catalog</h1>
<p class="sub">{len(index)} Infocom games for Spelunk. Tap a game, then <b>Open in Spelunk</b>.</p>
{views("A–Z")}
<ul class="grid">
{''.join(cards.values())}
</ul>"""
    (OUT / "index.html").write_text(page("Spelunk catalog", body, 0))

    # Chronological: by IFDB's first-published year, then earliest serial.
    # A game IFDB has no year for goes by its earliest build (serial 84xxxx: 1984).
    def year_of(g: dict) -> str:
        serial = first_serial([r["path"] for r in g["releases"]])
        return str(g.get("published") or (f"19{serial[:2]}" if serial != "999999" else "Year unknown"))
    by_year: dict[str, list[str]] = {}
    for tuid, g in sorted(index.items(), key=lambda kv: (
            year_of(kv[1]), first_serial([r["path"] for r in kv[1]["releases"]]),
            kv[1]["title"].lower())):
        by_year.setdefault(year_of(g), []).append(cards[tuid])
    sections = "".join(f'<h2 class="year">{esc(y)}</h2>\n<ul class="grid">\n{"".join(c)}\n</ul>\n'
                       for y, c in by_year.items())
    body = f"""<h1>Spelunk catalog</h1>
<p class="sub">{len(index)} Infocom games for Spelunk, in the order they came out.</p>
{views("By year")}
{sections}"""
    (OUT / "years.html").write_text(page("Spelunk catalog by year", body, 0))
    print(f"docs/: {len(index)} game pages, "
          f"{sum(len(g['releases']) for g in index.values())} manifests", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
