"""MediaStore implementations for local development and tests.

The Worker stores images in R2 (``r2_media_store.py``); the local server keeps
them in a directory, and tests keep them in a dict. Same port, same keys.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .ports import MediaStore


class MemoryMediaStore(MediaStore):
    def __init__(self):
        #: key → (bytes, content_type)
        self.objects: dict[str, tuple[bytes, str]] = {}
        #: key → custom metadata
        self.metadata: dict[str, dict[str, str]] = {}

    async def put(
        self, key: str, data: bytes, content_type: str, metadata: dict[str, str] | None = None
    ) -> None:
        self.objects[key] = (data, content_type)
        self.metadata[key] = dict(metadata or {})

    async def delete_keys(self, keys: list[str]) -> int:
        for key in keys:
            self.objects.pop(key, None)
            self.metadata.pop(key, None)
        return len(keys)

    async def delete_prefix(self, prefix: str) -> int:
        doomed = [key for key in self.objects if key.startswith(prefix)]
        for key in doomed:
            del self.objects[key]
            self.metadata.pop(key, None)
        return len(doomed)

    async def get(self, key: str) -> tuple[bytes, str] | None:
        return self.objects.get(key)


class DirectoryMediaStore(MediaStore):
    """``<root>/<key>`` holds the bytes, ``<key>.type`` the content type and
    ``<key>.meta.json`` the custom metadata."""

    def __init__(self, root: str):
        self._root = Path(root)

    async def put(
        self, key: str, data: bytes, content_type: str, metadata: dict[str, str] | None = None
    ) -> None:
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.with_name(path.name + ".type").write_text(content_type)
        path.with_name(path.name + ".meta.json").write_text(json.dumps(metadata or {}))

    async def delete_keys(self, keys: list[str]) -> int:
        root = self._root.resolve()
        for key in keys:
            path = (self._root / key).resolve()
            if root not in path.parents:
                raise ValueError(f"key outside the store: {key!r}")
            for f in (path, path.with_name(path.name + ".type"), path.with_name(path.name + ".meta.json")):
                f.unlink(missing_ok=True)
        return len(keys)

    async def delete_prefix(self, prefix: str) -> int:
        # Prefixes are folders here (`chat-archives/<share_id>/`); refuse anything
        # that is not one inside the root.
        folder = (self._root / prefix).resolve()
        if not prefix.endswith("/") or self._root.resolve() not in folder.parents:
            raise ValueError(f"not a folder prefix inside the store: {prefix!r}")
        if not folder.is_dir():
            return 0
        count = sum(
            1 for f in folder.rglob("*")
            if f.is_file() and not f.name.endswith((".type", ".meta.json"))
        )
        shutil.rmtree(folder)
        return count

    async def get(self, key: str) -> tuple[bytes, str] | None:
        path = self._root / key
        if not path.is_file():
            return None
        return path.read_bytes(), path.with_name(path.name + ".type").read_text()
