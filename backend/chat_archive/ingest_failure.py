"""The 400 body for a failed ingest, shared by the Worker and the local server.

The body is always ``{"error": str(exc)}``, unchanged for every caller. When
the failure carries a ``diagnostic`` (a decoder's safe classification of what
it was given, e.g. ``MissingFlightPayload``), the body gains a ``diagnostic``
field beside ``error``, and the diagnostic plus the failure's bounded
``log_sample`` are written to the server log — which, on the Worker, is the
Cloudflare Worker log — so a root-cause analysis has both.
"""

from __future__ import annotations

import json
from typing import Callable


def ingest_failure_body(exc: Exception, log: Callable[[str], None] = print) -> dict:
    """The JSON body for ``exc``; logs its diagnostic, if it has one."""
    body: dict = {"error": str(exc)}
    diagnostic = getattr(exc, "diagnostic", None)
    if diagnostic is None:
        return body
    body["diagnostic"] = diagnostic
    log(
        "[ingest] "
        + json.dumps(
            {
                "event": "ingest_diagnostic",
                "error": str(exc),
                "diagnostic": diagnostic,
                "sample": getattr(exc, "log_sample", None),
            }
        )
    )
    return body
