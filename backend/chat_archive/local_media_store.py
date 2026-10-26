"""MediaStore implementations for local development and tests.

The Worker stores images in R2 (``r2_media_store.py``); the local server keeps
them in a directory, and tests keep them in a dict. Same port, same keys.
"""

from __future__ import annotations

from pathlib import Path

from .ports import MediaStore


class MemoryMediaStore(MediaStore):
    def __init__(self):
        #: key → (bytes, content_type)
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)

    async def get(self, key: str) -> tuple[bytes, str] | None:
        return self.objects.get(key)


class DirectoryMediaStore(MediaStore):
    """``<root>/<key>`` holds the bytes and ``<root>/<key>.type`` the content type."""

    def __init__(self, root: str):
        self._root = Path(root)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.with_name(path.name + ".type").write_text(content_type)

    async def get(self, key: str) -> tuple[bytes, str] | None:
        path = self._root / key
        if not path.is_file():
            return None
        return path.read_bytes(), path.with_name(path.name + ".type").read_text()
