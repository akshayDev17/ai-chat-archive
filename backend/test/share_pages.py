"""Synthetic ChatGPT share pages, in both formats chatgpt.com serves.

Not a test module (no ``test_`` prefix): the decoder tests import it.

  * :data:`OCTANE_SHARE` — ``fixtures/octane_share.html``, the structure of a
    real octane share page as chatgpt.com served it on 2026-11-17 (the
    ``__WEB_MOBILE_DATA__`` JSON, its reference and DIL shapes, the decoy JSON
    scripts beside it), with every piece of content replaced by synthetic text.
  * :func:`flight_page` — a React Router page whose ``streamController.enqueue``
    flight payload encodes any object, for the older format.
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
OCTANE_SHARE = (FIXTURES / "octane_share.html").read_text(encoding="utf-8")


def _flight_encode(value: object, data: list) -> int:
    index = len(data)
    data.append(None)
    if isinstance(value, dict):
        chunk: dict = {}
        for key, item in value.items():
            key_index = len(data)
            data.append(key)
            chunk[f"_{key_index}"] = _flight_encode(item, data)
        data[index] = chunk
    elif isinstance(value, list):
        data[index] = [_flight_encode(item, data) for item in value]
    else:
        data[index] = value
    return index


def flight_page(root: dict, title: str = "ChatGPT", body: str = "") -> str:
    """A share page carrying ``root`` as its flight payload."""
    data: list = []
    _flight_encode(root, data)
    literal = json.dumps(json.dumps(data, separators=(",", ":")))
    return (
        f"<!DOCTYPE html><html><head><title>{title}</title>"
        '<script src="https://cdn.oaistatic.com/assets/entry.js"></script></head>'
        f"<body>{body}<script>window.__reactRouterContext.streamController.enqueue({literal});"
        "</script></body></html>"
    )


def flight_share(conversation: dict, title: str = "ChatGPT") -> str:
    """A flight page whose share route serves ``conversation``."""
    return flight_page(
        {
            "loaderData": {
                "root": {"disableSSR": False},
                "routes/share.$shareId.($action)": {"serverResponse": {"data": conversation}},
            },
            "actionData": None,
            "errors": None,
        },
        title=title,
    )


def octane_page(data: object, title: str = "ChatGPT") -> str:
    """An octane page whose ``__WEB_MOBILE_DATA__`` is ``data``."""
    blob = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    return (
        f'<!DOCTYPE html><html><head><title>{title}</title>'
        '<link rel="stylesheet" href="/unauth-mweb/assets/app.css"></head><body>'
        f'<script id="__WEB_MOBILE_DATA__" type="application/json">{blob}</script></body></html>'
    )
