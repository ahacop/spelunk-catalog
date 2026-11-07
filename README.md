# Spelunk catalog

`.spelunk` manifests for Z-machine stories, for the [Spelunk](https://github.com/ahacop/spelunk)
iOS app. Each manifest describes **one release** of a game and points at files hosted elsewhere
(mostly [eblong.com/infocom](https://eblong.com/infocom/)). This repo holds only the JSON.

## Layout

```
games/<ifdb-id>/r<release>-s<serial>.spelunk   one manifest per release, every release on eblong
data/index.json      per game: its releases, the default (latest standard release) first
data/stories.json    cache: each eblong story URL -> sha256, size, header identity
data/ifdb.json       cache: IFID -> IFDB id, title, author
data/unmatched.json  stories IFDB doesn't know yet (no manifest written)
data/extras.json     cache: each extras URL -> type, sha256, size
data/skipped-extras.json  extras found but left out (GIF, SVG, zip, dead link...)
tools/extras.py      finds box art, manuals and InvisiClues for each game
tools/generate.py    builds the manifests from eblong + IFDB
tools/validate.py    checks manifests (structure; with --download, the files too)
tools/spelunklib.py  header identity, sanitising and format rules
```

Releases of one game share a folder and the same `game` id, which is how the app groups them.

## Format

Version 1, as specified in ahacop/spelunk#31 (kept by #73):

```json
{
  "spelunk": 1,
  "title": "Zork I",
  "game": "0dbnusxunq7fw5ro",
  "story": {
    "ifid": "ZCODE-88-840726", "release": 88, "serial": "840726", "checksum": "a129",
    "label": "Release 88 (840726)", "type": "z3", "sha256": "…", "bytes": 92160,
    "sources": ["https://eblong.com/infocom/gamefiles/zork1-r88-s840726.z3"]
  },
  "links": { "ifdb": "https://ifdb.org/viewgame?id=0dbnusxunq7fw5ro" }
}
```

## Extras

Each manifest has the game, its box art, its manual and its InvisiClues, nothing else:

- **Box art** (`cover`): the game's cover image on IFDB.
- **Manual** (`manual`): the Infocom Documentation Project's PDF (infodoc.plover.net), or the
  manual IFDB links to. The scans include the feelies.
- **InvisiClues** (`hints`): infodoc's hint story file (`.z5`),
  [infocom-hints-html](https://github.com/dfabulich/infocom-hints-html)'s scraped JSON (pinned to
  one commit so the hash stays stable), and the InvisiClues booklet's map, drawn by Infocom, as a
  PDF titled "InvisiClues map". There is no separate `map` key.

Hints are always files for Spelunk's own viewer, never web pages. Hint JSON may contain only the
HTML tags `p`, `div`, `br`, `b`, `strong`, `i`, `em`, `ul`, `ol`, `li` and `pre`, with no
attributes; the app renders them natively, never with WebKit.

Every file is downloaded once, typed by its magic bytes and hashed. Only PNG, JPEG, HEIC, PDF,
hint stories and hint JSON get in; GIFs, SVGs, zips and dead links are listed in
`data/skipped-extras.json`. `--no-extras` skips this step.

Extras added by hand win: the generator never replaces a filled `cover` or `manual`, only adds new
`hints`, and on refresh rewrites only `story` (keeping title, label and mirrors).

## Refreshing

```sh
python3 tools/generate.py            # downloads only stories it hasn't hashed yet
python3 tools/generate.py --latest-only   # only each game's default release
python3 tools/generate.py --recheck  # re-downloads everything to catch changed files
python3 tools/generate.py --offline  # rebuilds manifests from the caches, no network
```

It makes one request at a time, at least 5 seconds apart per host (`--delay`), honours
`Retry-After`, and stops after five failures in a row. A first run over the whole Infocom
collection takes a while on purpose.

IFIDs follow the Treaty of Babel: `ZCODE-<release>-<serial>`, plus `-<checksum>` unless the
serial is a date (starts with 8 or 9).

## Checks

CI runs `tools/validate.py` and the unit tests on every push and PR, with no network: format
version, required fields, `https`-only sources on public hosts, plain-text strings without control
or bidi characters, allowed part types only (PNG, JPEG, HEIC, PDF, z1–z8, hint files; no SVG, no
hint web pages), and the file path. `python3 tools/validate.py --download FILE...` also fetches a
manifest's files and checks sha256, magic bytes, size caps, hint markup and, for stories, the
header's release, serial and checksum; run it by hand when adding a manifest.

The app re-checks everything itself: every manifest is untrusted, including these.
