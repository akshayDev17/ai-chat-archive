# Sources

Two different things in this codebase are called "citations". They come from
different places, mean different things, and were conflated badly enough that a
reply citing eighteen URLs displayed none.

| | where it lives | what it is | stored as |
|---|---|---|---|
| **The report's bibliography** | inside the generated markdown summary | whatever the summary skill chose to list | `reports.citations` |
| **The conversation's sources** | `message.metadata.content_references` | ChatGPT's inline source pills, and the panel at the foot of a reply | `sources` |

Both are now parsed and shown, in the two places they belong: the report's
bibliography renders with the report, and the conversation's sources sit behind
the `···` on each assistant turn in the transcript.

## Why the conversation's sources needed recovering

They were being **deleted**. `lib/chat.ts` stripped ChatGPT's private-use
citation tokens out of the message text, and a comment there explained why:

> The share payload does not give us the token → URL mapping, so we strip them
> rather than invent links.

That was wrong. The mapping is the whole contents of `content_references`:

```json
{
  "type": "grouped_webpages",
  "matched_text": "\ue200cite\ue202turn0search0\ue202turn0search4\ue201",
  "start_idx": 420,
  "end_idx": 452,
  "items": [{
    "title": "United States-India Joint Statement – The White House",
    "url": "https://www.whitehouse.gov/briefings-statements/2026/02/…",
    "attribution": "The White House",
    "pub_date": 1770336000
  }]
}
```

`matched_text` is the token, `start_idx`/`end_idx` is where it sits in the
message, and `items` says what it points at. Joining those two halves turns
noise into a link. The parser now does that, and the reader splices a real link
in at each recorded span.

## Only two of the reference types are sources

`content_references` is a mixed bag. Observed so far:

| `type` | what it is | treated as |
|---|---|---|
| `grouped_webpages` | the inline citation pill | **a source** |
| `url` | a link the model wrote, with its own label | **a source** |
| `sources_footnote` | a zero-width marker at `start == end == len(text)` | skipped — a position, not a citation |
| `followup_a` | suggested follow-up questions | skipped — not a citation at all |
| `entity`, `entity_metadata` | place cards (`Charkop Mangroves`) | skipped for now |
| `map` | a map widget | skipped for now |
| `image_group` | an image carousel | skipped for now |

The last three are **dropped**, which is a placeholder rather than a decision.
They are rendered as widgets in ChatGPT and have no textual form; what matters
here is that they do not leak into the prose as
`entity["turn0business0","Charkop Mangroves"]`, which is what stripping only the
markers produced. Rendering them is the artifact-segregation work.

## Two traps, both now covered by tests

### The offsets are code points; JavaScript indexes UTF-16

A reply containing twelve emoji (🇺🇸 🇷🇺 🇮🇳 🛢 💻 🏥 📦) was off by twelve, so
every span replaced the wrong characters and left the tail of the token behind
as literal `turn0news10` in the prose. Sessions with no emoji were correct,
which made it look random and made it look like a content problem.

The conversion happens in `chat_archive/serializers.py::_utf16_offsets`, at the
wire, because the wire's consumer is JavaScript. The stored form stays code
points, which is what Python slices by.

### Trimming the text shifts every offset

`_render_parts` stripped the message for display while ChatGPT's offsets were
measured against its own untrimmed text, so any leading whitespace moved every
citation. It now trims for display and shifts the offsets to match.

`_span_is_trustworthy` guards both cases: a span is only used if it is in range
and `content[start:end]` actually contains a private-use token. A bad offset must
never be allowed to rewrite text — the source is still listed in the panel, it
just gets no marker.

## Vendor marks

`frontend/public/vendors/` holds one mark per vendor whose sessions this archive
can hold, keyed by `conversations.source` (`chatgpt | gemini | claude | elicit`).
Adding a vendor is a file plus a row in `lib/vendors.ts`.

The bot mark beside each assistant turn is the vendor's; a source badge is the
vendor's if the URL belongs to one, and otherwise a monogram — the domain's
initial on a tint derived from the domain, so a site always looks the same.

**Not remote favicons.** Fetching `google.com/s2/…` for every source would hand a
third party the reading list of a private archive, on every page load, forever.
When real favicons are wanted they belong fetched once at ingest and stored
alongside the source.

See `frontend/public/vendors/README.md` for provenance, and for why `elicit.svg`
is a reserved slot with no file.

## Design reference

`docs/reference/chatgpt-sources-ui.png` is the ChatGPT Activity panel this
feature is modelled on: a badge, the domain, then the title, per row. The
ordering is the part worth copying — the domain tells you who is speaking faster
than a headline does, and the headline is what you actually decide on. The
typography is ours.
