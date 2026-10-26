"""Copy pending GenUI images into the media store — the Cron Trigger's job.

Filing records each image as a ``pending`` row and returns; this runs once a
minute (and on the media page's "Copy now") to move the bytes. A batch is ten
images: on the Workers Free plan an invocation gets 50 subrequests, and each
image costs a fetch and an R2 write, plus a D1 update.

Each image is stored at the path fixed when its story was filed —
``chat-archives/<share_id>/<message id>/<NN>.<ext>`` — so the bucket reads as
one folder per story and one per message, and a story's images can be deleted
as a folder. Every object carries its provenance as R2 custom metadata.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .ports import ConversationRepository, ImageFetcher, MediaStore
from .repository import story_prefix

MEDIA_BATCH = 10
MAX_ATTEMPTS = 3
#: A row left `copying` this long belonged to a run that was killed.
STALE_AFTER = timedelta(minutes=5)
MAX_BYTES = 10 * 1024 * 1024

_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/avif": "avif",
}


#: Why a row filed before per-story paths cannot be copied (see 0003).
NO_PATH_ERROR = "filed before images were stored per story — file this story again to copy them"


def media_key(object_path: str, content_type: str) -> str | None:
    """An image's R2 key — its path plus the type's extension — or ``None``
    for a type the archive does not keep."""
    extension = _EXTENSIONS.get(content_type)
    if extension is None:
        return None
    return f"{object_path}.{extension}"


def _provenance(row: dict) -> dict[str, str]:
    """R2 custom metadata: which story and message the object belongs to."""
    parts = row["object_path"].split("/")
    return {
        "share_id": parts[1],
        "message_id": parts[2],
        "source_url": row["content_url"],
        "origin_url": row.get("origin_url") or "",
    }


async def copy_pending(
    repo: ConversationRepository,
    store: MediaStore,
    fetcher: ImageFetcher,
    now: datetime,
    only: tuple[str, str] | None = None,
    limit: int = MEDIA_BATCH,
) -> dict:
    """Copy one batch; returns ``{"copied": n, "failed": n}`` counted per image URL."""
    claimed = await repo.claim_media(
        limit, now.isoformat(), (now - STALE_AFTER).isoformat(), only=only,
        max_attempts=MAX_ATTEMPTS,
    )

    copied = failed = 0
    rows_by_url: dict[str, list[dict]] = {}
    for row in claimed:
        if not row.get("object_path"):
            await repo.fail_media(row["id"], NO_PATH_ERROR, 1)
            failed += 1
            continue
        rows_by_url.setdefault(row["content_url"], []).append(row)

    # One fetch per URL — the same picture can appear twice in one batch — but
    # each image is stored at its own path.
    for url, rows in rows_by_url.items():
        try:
            data, raw_type = await fetcher.fetch(url)
            content_type = (raw_type or "").split(";")[0].strip().lower()
            if content_type not in _EXTENSIONS:
                raise ValueError(f"unsupported content type {content_type or 'missing'}")
            if len(data) > MAX_BYTES:
                raise ValueError(f"image larger than {MAX_BYTES} bytes")
            for row in rows:
                key = media_key(row["object_path"], content_type)
                await store.put(key, data, content_type, _provenance(row))
                await repo.finish_media(row["id"], key, content_type)
            copied += 1
        except Exception as exc:  # noqa: BLE001 - any failure is one attempt
            for row in rows:
                await repo.fail_media(row["id"], str(exc)[:300], MAX_ATTEMPTS)
            failed += 1
    return {"copied": copied, "failed": failed}


async def retract_story(
    repo: ConversationRepository, store: MediaStore, share_id: str, owner_email: str
) -> bool:
    """Retract the caller's copy of a story and free its images in R2.

    D1: the story's rows go (``repo.delete``). R2, two kinds of object:

    - its folder ``chat-archives/<share_id>/`` — deleted once nobody files the
      link any more (two people can file one link, and they share its folder);
    - images copied before per-story folders, named on its rows (``img/…``) —
      deleted unless another story's rows still point at them, since those old
      keys were shared between stories showing the same picture.

    Returns False when the caller had no copy (nothing is deleted).
    """
    prefix = story_prefix(share_id)
    story = await repo.media_for(share_id, owner_email)
    if not await repo.delete(share_id, owner_email):
        return False
    if await repo.get(share_id) is None:
        await store.delete_prefix(prefix)
    old_keys = sorted({
        image["r2_key"] for image in (story or {}).get("images", [])
        if image["r2_key"] and not image["r2_key"].startswith(prefix)
    })
    if old_keys:
        still_used = await repo.referenced_keys(old_keys)
        await store.delete_keys([key for key in old_keys if key not in still_used])
    return True
