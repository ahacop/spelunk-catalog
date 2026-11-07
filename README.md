# Spelunk catalog

`.spelunk` manifests for Z-machine stories, for the [Spelunk](https://github.com/ahacop/spelunk)
iOS app. Each manifest describes **one release** of a game and points at files hosted elsewhere
(mostly [eblong.com/infocom](https://eblong.com/infocom/)). This repo holds only the JSON.

## Layout

```
games/<ifdb-id>/r<release>-s<serial>.spelunk   one manifest per release
data/stories.json    cache: each eblong story URL -> sha256, size, header identity
data/ifdb.json       cache: IFID -> IFDB id, title, author
data/unmatched.json  stories IFDB doesn't know yet (no manifest written)
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

Extras (`cover`, `manual`, `map`, `feelies`, `hints`) can be added by hand. The generator only
rewrites `story` when it refreshes, and keeps extras, title, label and any extra mirror sources.

## Refreshing

```sh
python3 tools/generate.py            # downloads only stories it hasn't hashed yet
python3 tools/generate.py --recheck  # re-downloads everything to catch changed files
python3 tools/generate.py --offline  # rebuilds manifests from the caches, no network
```

It makes one request at a time, at least 5 seconds apart per host (`--delay`), honours
`Retry-After`, and stops after five failures in a row. A first run over the whole Infocom
collection takes a while on purpose.

IFIDs follow the Treaty of Babel: `ZCODE-<release>-<serial>`, plus `-<checksum>` unless the
serial is a date (starts with 8 or 9).

## Checks

CI runs `tools/validate.py` on every push and PR: format version, required fields, `https`-only
sources on public hosts, plain-text strings without control or bidi characters, allowed part
types only (PNG, JPEG, HEIC, PDF, z1–z8, hints; no SVG), and the file path. Manifests a PR adds
or changes are downloaded and checked for sha256, magic bytes, size caps and, for stories, the
header's release, serial and checksum.

The app re-checks everything itself: every manifest is untrusted, including these.
