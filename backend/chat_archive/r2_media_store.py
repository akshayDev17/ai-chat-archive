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
        # `env.MEDIA` is the SDK's `_BindingWrapper`, which converts results:
        # a missing object arrives as None, `arrayBuffer()` as a Python
        # memoryview (not a JS buffer with `.to_bytes()`), and `httpMetadata`
        # as an attribute-dict. Both buffer shapes are accepted, so this does
        # not break again if the SDK stops wrapping.
        found = await self._bucket.get(key)
        if not found:
            return None
        buffer = await found.arrayBuffer()
        data = buffer.to_bytes() if hasattr(buffer, "to_bytes") else bytes(buffer)
        metadata = getattr(found, "httpMetadata", None)
        content_type = getattr(metadata, "contentType", None) or "application/octet-stream"
        return data, content_type
