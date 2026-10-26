"""MediaStore backed by the Worker's R2 binding (``env.MEDIA``).

Only importable inside the Workers runtime — the ``js`` / ``pyodide`` imports
are deferred to call time, as in ``make_worker_fetch``, so the module itself
still imports in plain Python.

R2 wants a JS byte buffer, not Python ``bytes``: the bytes are copied into a
``Uint8Array`` with ``assign``. The content type travels as R2 ``httpMetadata``
so ``get`` can hand it back for the ``Content-Type`` header.
https://developers.cloudflare.com/r2/api/workers/workers-api-reference/
"""

from __future__ import annotations

from .ports import MediaStore


class R2MediaStore(MediaStore):
    def __init__(self, bucket):
        self._bucket = bucket

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        from js import Object, Uint8Array
        from pyodide.ffi import to_js

        body = Uint8Array.new(len(data))
        body.assign(data)
        options = to_js(
            {"httpMetadata": {"contentType": content_type}},
            dict_converter=Object.fromEntries,
        )
        await self._bucket.put(key, body, options)

    async def get(self, key: str) -> tuple[bytes, str] | None:
        found = await self._bucket.get(key)
        if found is None:
            return None
        data = (await found.arrayBuffer()).to_bytes()
        metadata = getattr(found, "httpMetadata", None)
        content_type = getattr(metadata, "contentType", None) or "application/octet-stream"
        return data, content_type
