"""MediaStore backed by the Worker's R2 binding (``env.MEDIA``).

Only usable inside the Workers runtime — the ``js`` import
are deferred to call time, as in ``make_worker_fetch``, so the module itself
still imports in plain Python.

``env.MEDIA`` is the SDK's binding wrapper: it converts Python arguments
(dicts, lists) to JS and converts results back. R2 wants a JS byte buffer, so
the bytes are copied into a ``Uint8Array``. The content type travels as
``httpMetadata`` (for the ``Content-Type`` header on read) and the story and
message as ``customMetadata``, so every object says where it came from.
https://developers.cloudflare.com/r2/api/workers/workers-api-reference/
"""

from __future__ import annotations

import re

from .ports import MediaStore

#: The only prefix this store will bulk-delete: one story's folder.
_STORY_FOLDER = re.compile(r"^chat-archives/[A-Za-z0-9-]+/$")


class R2MediaStore(MediaStore):
    def __init__(self, bucket):
        self._bucket = bucket

    async def put(
        self, key: str, data: bytes, content_type: str, metadata: dict[str, str] | None = None
    ) -> None:
        from js import Uint8Array

        body = Uint8Array.new(len(data))
        body.assign(data)
        # A plain dict: the SDK's binding wrapper converts it to a JS object.
        await self._bucket.put(
            key,
            body,
            {"httpMetadata": {"contentType": content_type}, "customMetadata": dict(metadata or {})},
        )

    async def delete_keys(self, keys: list[str]) -> int:
        if keys:
            await self._bucket.delete(list(keys))
        return len(keys)

    async def delete_prefix(self, prefix: str) -> int:
        """Delete one story's folder (``chat-archives/<share_id>/``), page by page."""
        if not _STORY_FOLDER.match(prefix):
            raise ValueError(f"not a story folder: {prefix!r}")
        deleted = 0
        cursor = None
        while True:
            options = {"prefix": prefix, **({"cursor": cursor} if cursor else {})}
            listing = await self._bucket.list(options)
            keys = [getattr(o, "key") for o in getattr(listing, "objects", None) or []]
            if keys:
                await self._bucket.delete(keys)
                deleted += len(keys)
            if not getattr(listing, "truncated", False):
                return deleted
            cursor = getattr(listing, "cursor", None)

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
