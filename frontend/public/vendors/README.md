# Vendor marks

One mark per AI vendor whose sessions this archive can hold. The `source` column
already reads `chatgpt | gemini | claude | elicit`, so each of those is meant to
have a file here:

| file | vendor | status |
|---|---|---|
| `chatgpt.svg` | ChatGPT / OpenAI | ✓ |
| `gemini.svg` | Google Gemini | ✓ |
| `claude.svg` | Claude / Anthropic | ✓ |
| `elicit.svg` | Elicit | **missing — slot reserved** |

## Provenance

The three present are verbatim copies from [Simple Icons](https://simpleicons.org)
(v16, CC0-1.0):

- `https://cdn.jsdelivr.net/npm/simple-icons@16/icons/openai.svg` → `chatgpt.svg`
- `https://cdn.jsdelivr.net/npm/simple-icons@16/icons/googlegemini.svg` → `gemini.svg`
- `https://cdn.jsdelivr.net/npm/simple-icons@16/icons/claude.svg` → `claude.svg`

Renamed to the vendor rather than the company, because that is what `source`
stores and what a reader recognises. They are single-path `0 0 24 24` SVGs, which
is why `globals.css` can render them as a `mask` and let them take
`currentColor` — one file works on paper, on ink and on the accent without a
second asset.

Simple Icons' licence covers the files; the marks themselves remain trademarks of
their owners, used here only to identify which tool a conversation came from.

## Why `elicit.svg` is absent

Simple Icons has no Elicit mark, and the alternatives were to trace one by eye or
to lift one from somewhere it is not licensed to be lifted from. Both would put a
mark in this folder that is wrong or not ours to ship, which is worse than a gap
in a table nobody has needed yet.

Until the file exists, Elicit sessions fall back to the monogram tile that every
unrecognised source already uses — see `frontend/app/components/SourceIcon.tsx`.
Drop a `24×24` single-path SVG at `elicit.svg` and it will be picked up with no
code change.

## Swapping a mark

Replace the file. Nothing references these by content — the lookup is by vendor
name in `frontend/lib/vendors.ts` — so a new version needs no code edit. Keep the
`0 0 24 24` viewBox and a single path, or the mask will crop or double up.
