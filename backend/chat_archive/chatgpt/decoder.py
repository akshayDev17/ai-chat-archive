"""ChatGPT share-page payload decoding.

chatgpt.com serves a share in one of two page formats, and as of 2026-11-17
serves both: most shares come from its "octane" web app, which carries the
conversation as JSON in ``<script id="__WEB_MOBILE_DATA__">`` (read by
:mod:`.web_mobile`); others — and every deleted or unknown share — still come
from the React Router app, which embeds it as a React Server Components (RSC)
"flight" payload in ``streamController.enqueue("…")`` calls.

The flight payload is a flat JSON array where:

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
from .page_diagnostic import (
    diagnose_missing_conversation,
    LOG_SAMPLE_MAX_CHARS,
    diagnose_missing_payload,
    log_sample,
    payload_log_sample,
)
from .web_mobile import conversation_from_web_mobile, web_mobile_conversation, web_mobile_payload

# Matches `streamController.enqueue("...")` and captures the JS string literal.
_ENQUEUE_RE = re.compile(r'streamController\.enqueue\("((?:[^"\\]|\\.)*)"\)')


class MissingFlightPayload(ValueError):
    """The page held no flight payload. Its message is the stable public error;
    ``diagnostic`` is a safe classification of the page for the response, and
    ``log_sample`` a bounded raw head of the page for server-side logs only."""

    MESSAGE = "No flight payload found in page"

    def __init__(self, diagnostic: dict, log_sample: str):
        super().__init__(self.MESSAGE)
        self.diagnostic = diagnostic
        self.log_sample = log_sample


class MissingConversationData(ValueError):
    """The page held a payload, but no conversation where one belongs. Like
    :class:`MissingFlightPayload`: a stable message, a safe ``diagnostic`` of
    the payload, and a bounded ``log_sample`` of it for server-side logs only."""

    MESSAGE = "Could not locate conversation data in payload"

    def __init__(self, diagnostic: dict, log_sample: str):
        super().__init__(self.MESSAGE)
        self.diagnostic = diagnostic
        self.log_sample = log_sample


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


class ChatGptShareDecoder(PayloadDecoder):
    """Decode a ChatGPT share page's HTML into the raw conversation dict."""

    _FLIGHT_PATH = (
        "loaderData",
        "routes/share.$shareId.($action)",
        "serverResponse",
        "data",
    )
    _WEB_MOBILE_PATH = ("conversation", "messages")

    def decode(self, raw: str) -> dict:
        chunks = self._enqueued_chunks(raw)
        if chunks:
            return self._decode_flight(raw, chunks[0])
        payload = web_mobile_payload(raw)
        if payload is not None:
            return self._decode_web_mobile(raw, payload)
        raise MissingFlightPayload(diagnose_missing_payload(raw), log_sample(raw))

    def _decode_flight(self, html: str, payload: str) -> dict:
        # The first enqueue carries the full payload; later chunks are increments.
        root = FlightUnpacker(json.loads(payload)).unpack()
        if not isinstance(root, dict):
            raise ValueError("Flight payload did not decode to an object")
        conversation = _follow(root, self._FLIGHT_PATH)
        if not isinstance(conversation, dict):
            raise self._missing(
                html, payload, "flight", root, self._FLIGHT_PATH, payload_log_sample(root, self._FLIGHT_PATH)
            )
        return conversation

    def _decode_web_mobile(self, html: str, payload: str) -> dict:
        try:
            data = json.loads(payload)
        except ValueError:
            raise self._missing(
                html, payload, "web_mobile_data", None, self._WEB_MOBILE_PATH, payload[:LOG_SAMPLE_MAX_CHARS]
            ) from None
        conversation = web_mobile_conversation(data)
        if conversation is None:
            raise self._missing(
                html, payload, "web_mobile_data", data, self._WEB_MOBILE_PATH,
                payload_log_sample(data, self._WEB_MOBILE_PATH),
            )
        return conversation_from_web_mobile(conversation)

    @staticmethod
    def _missing(
        html: str, payload: str, kind: str, root: object, path: tuple, sample: str
    ) -> MissingConversationData:
        return MissingConversationData(diagnose_missing_conversation(html, payload, kind, root, path), sample)

    @staticmethod
    def _enqueued_chunks(html: str) -> list[str]:
        # Unescape each JS string literal by re-quoting it as JSON.
        return [json.loads('"' + m.group(1) + '"') for m in _ENQUEUE_RE.finditer(html)]


def _follow(root: object, path: tuple) -> object:
    node = root
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
    return node
