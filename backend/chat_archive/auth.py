"""Who is asking? — authentication as a port.

The archive is per-email, so *every* write and every listing needs an
authenticated identity. Rather than scatter that knowledge through the router,
it lives behind :class:`IdentityProvider`: the router asks the port "who is
this?", gets an :class:`Identity` or ``None``, and never learns how the answer
was produced.

That seam matters because the mechanism is genuinely undecided:

* :class:`CloudflareAccessIdentity` reads the identity Cloudflare Access
  already resolved. Access sits in front of the Worker, so there is no
  hand-rolled crypto here at all — but Access is a *hostname/path* gate, and
  its one-time-PIN screen is served by Cloudflare, not by us.
* :class:`DevIdentity` is the local stand-in, so the same router and the same
  repository run on a laptop with no Access, no domain and no email provider.

Swapping in a self-hosted OTP flow later means adding one more implementation
of this ABC. Nothing else in the backend changes — that is the point.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

from .models import normalize_email
from .urls import path_of


@dataclass(frozen=True)
class Identity:
    """An authenticated reader."""

    email: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "email", normalize_email(self.email))


class IdentityProvider(ABC):
    """Resolve the caller of a request to an :class:`Identity`, or ``None``."""

    @abstractmethod
    async def identify(self, request) -> Identity | None:
        """Return the caller's identity, or ``None`` if they are anonymous.

        Implementations must **fail closed**: any error, missing binding or
        unexpected shape resolves to ``None``, never to a default identity.
        """


class AnonymousIdentity(IdentityProvider):
    """Always anonymous. The safe default, and the test double."""

    async def identify(self, request) -> Identity | None:
        return None


class DevIdentity(IdentityProvider):
    """Local-development identity: one fixed reader.

    Used by ``local_server.py`` so the frontend can be exercised end to end
    without Cloudflare. It is *never* wired into the Worker.
    """

    def __init__(self, email: str):
        self._email = normalize_email(email)

    async def identify(self, request) -> Identity | None:
        return Identity(self._email) if self._email else None


class CloudflareAccessIdentity(IdentityProvider):
    """Read the identity Cloudflare Access already established.

    Citations, precisely attributed — the two facts live on two different pages:

    * That ``ctx`` exists as ``self.ctx`` in a Python ``WorkerEntrypoint``:
      the Context API page says ctx is exposed "As a class property of the
      ``WorkerEntrypoint`` class (``this.ctx``)".
      https://developers.cloudflare.com/workers/runtime-apis/context/

    * That ``ctx.access`` carries the identity:
      "When Cloudflare Access authenticates a request that directly invokes
      your Worker, the Worker can read the signed-in user's identity […]
      through ``ctx.access``", and "``ctx.access`` is ``undefined`` if Access
      did not authenticate the request."
      https://developers.cloudflare.com/workers/configuration/cloudflare-access/

      (Note: the Context API page does **not** document ``ctx.access``, even
      though Cloudflare's own Access changelog links to an anchor there. Cite
      the configuration page, not the Context page.)

    **Python support is undocumented.** Every ``ctx.access`` example in
    Cloudflare's docs is JavaScript, and the string never appears on a Python
    page. ``self.ctx`` demonstrably exists in Python (the docs' own Cache API
    example uses ``self.ctx.waitUntil``), so ``self.ctx.access`` may well work —
    but that is an inference, not a citation. It must be verified empirically on
    the deployed Worker, which is what ``/api/health`` is for.

    If it does not exist, ``identify`` returns ``None`` and every protected
    route answers 401. That is the correct failure direction, but it is also why
    :meth:`IdentityProvider.identify` failing closed is not by itself enough —
    something has to *tell you* it failed closed.

    **It does fail, intermittently, for signed-in readers.** A live test saw
    about one ``/api/desk/*`` call in six answer 401 while siblings in the same
    page load, with the same cookies, answered 200. In workerd, ``ctx.access``
    is built from the current request's Access info and ``getIdentity()`` is an
    RPC to Cloudflare's identity service that "resolves to ``undefined`` when no
    identity is associated with the request"; an uncaught throw here would have
    been a 500, not a 401. So a 401 means ``ctx.access`` was absent or the
    lookup came back without an email, and which of the two is only visible in
    production. Hence:

    1. ``getIdentity()`` gets a second try when it throws or has no email.
    2. Every 401 this produces logs one line naming the branch that fired, and
       every rescue by the retry logs what the first try hit, so the cause can
       be read off the Worker's logs. The lines carry the request path, branch
       names and error class/message, never a token, cookie, header value or
       email.
    """

    def __init__(self, ctx, log: Callable[[str], None] = print):
        self._ctx = ctx
        self._log = log

    async def identify(self, request) -> Identity | None:
        email, attempts = await self._email_from_access()
        if not attempts:
            return Identity(email)

        record = {
            "event": "desk_identity_recovered" if email else "desk_identity_missing",
            "path": path_of(str(request.url)),
            "branch": attempts[0]["branch"],
            "attempts": attempts,
        }
        self._log(json.dumps(record))
        return Identity(email) if email else None

    async def _email_from_access(self) -> tuple[str | None, list[dict]]:
        """The email from ``ctx.access``, and each failed attempt before it."""
        access = getattr(self._ctx, "access", None)
        if access is None:
            return None, [{"branch": "access_absent"}]

        failures = []
        for _ in range(2):
            try:
                identity = await access.getIdentity()
            except Exception as exc:
                failures.append(
                    {
                        "branch": "get_identity_threw",
                        "error_class": type(exc).__name__,
                        "error": str(exc)[:200],
                    }
                )
                continue
            email = getattr(identity, "email", None)
            if email:
                return str(email), failures
            failures.append({"branch": "resolved_without_email"})
        return None, failures
