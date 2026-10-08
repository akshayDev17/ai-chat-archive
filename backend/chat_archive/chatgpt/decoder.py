"""ChatGPT share-page payload decoding.

The share page embeds the conversation as a React Server Components (RSC)
"flight" payload — a flat JSON array where:

  * strings / numbers / booleans are inline literals,
  * a dict  {"_N": M}  is an object: key string at data[N], value at data[M],
  * a list is an array whose elements are indices into data,
  * a negative integer (-5) means ``None`` (undefined),
  * an integer >= len(data) is a literal number (e.g. a timestamp).
"""

from __future__ import annotations

import json
import re

from ..ports import PayloadDecoder

# Matches `streamController.enqueue("...")` and captures the JS string literal.
_ENQUEUE_RE = re.compile(r'streamController\.enqueue\("((?:[^"\\]|\\.)*)"\)')


class FlightUnpacker:
    """Unpacks one RSC flight payload into a plain Python object.

    Kept as its own class so it can be unit-tested in isolation and reused by
    other ChatGPT-shaped payloads without dragging in HTTP concerns.
    """

    def __init__(self, data: list):
        self._data = data
        self._len = len(data)
        self._memo: dict[int, object] = {}

    def unpack(self) -> object:
        return self._resolve(0)

    def _resolve(self, idx: object) -> object:
        if idx is None or isinstance(idx, bool):
            return idx
        if isinstance(idx, int):
            if idx < 0:
                return None  # the -5 "undefined" marker
            if idx >= self._len:
                return idx  # literal number (timestamp, weight, ...)
            if idx in self._memo:
                return self._memo[idx]
            self._memo[idx] = None  # placeholder breaks any cycle
            value = self._decode_chunk(self._data[idx])
            self._memo[idx] = value
            return value
        return self._decode_chunk(idx)

    def _decode_chunk(self, chunk: object) -> object:
        if isinstance(chunk, list):
            return [
                self._resolve(e) if isinstance(e, int) else self._decode_chunk(e)
                for e in chunk
            ]
        if isinstance(chunk, dict):
            out: dict = {}
            for key, value in chunk.items():
                name = (
                    self._data[int(key[1:])]
                    if isinstance(key, str) and key.startswith("_")
                    else key
                )
                out[name] = (
                    self._resolve(value) if isinstance(value, int) else self._decode_chunk(value)
                )
            return out
        return chunk


class ChatGptFlightDecoder(PayloadDecoder):
    """Decode a ChatGPT share page's HTML into the raw conversation dict."""

    _CONVERSATION_PATH = (
        "loaderData",
        "routes/share.$shareId.($action)",
        "serverResponse",
        "data",
    )

    def decode(self, raw: str) -> dict:
        payload = self._extract_payload(raw)
        root = FlightUnpacker(payload).unpack()
        if not isinstance(root, dict):
            raise ValueError("Flight payload did not decode to an object")
        conversation = root
        for key in self._CONVERSATION_PATH:
            conversation = conversation.get(key) if isinstance(conversation, dict) else None
        if not isinstance(conversation, dict):
            raise ValueError("Could not locate conversation data in payload")
        return conversation

    @staticmethod
    def _extract_payload(html: str) -> list:
        chunks: list[str] = []
        for match in _ENQUEUE_RE.finditer(html):
            # Unescape the JS string literal by re-quoting it as JSON.
            chunks.append(json.loads('"' + match.group(1) + '"'))
        if not chunks:
            raise ValueError("No flight payload found in page")
        # The first enqueue carries the full payload; later chunks are increments.
        return json.loads(chunks[0])
