"""Copy pending GenUI images into the media store — the Cron Trigger's job.

Filing records each image as a ``pending`` row and returns; this runs once a
minute (and on the media page's "Copy now") to move the bytes. A batch is ten
images: on the Workers Free plan an invocation gets 50 subrequests, and each
image costs a fetch and an R2 write, plus a D1 update.

Keys are content-addressed by URL — ``img/<sha256(content_url)>.<ext>`` — so the
same picture in two stories is stored once, and a re-filed story finds its
images already copied.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from .ports import ConversationRepository, ImageFetcher, MediaStore

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


def media_key(content_url: str, content_type: str) -> str | None:
    """Where an image is stored, or ``None`` for a type the archive does not keep."""
    extension = _EXTENSIONS.get(content_type)
    if extension is None:
        return None
    return f"img/{hashlib.sha256(content_url.encode()).hexdigest()}.{extension}"


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
        limit, now.isoformat(), (now - STALE_AFTER).isoformat(), only=only
    )

    # One fetch per URL: the same picture can appear twice in one batch, and
    # finish_media marks every row with that URL at once.
    rows_by_url: dict[str, list[str]] = {}
    for row in claimed:
        rows_by_url.setdefault(row["content_url"], []).append(row["id"])

    copied = failed = 0
    for url, ids in rows_by_url.items():
        try:
            data, raw_type = await fetcher.fetch(url)
            content_type = (raw_type or "").split(";")[0].strip().lower()
            key = media_key(url, content_type)
            if key is None:
                raise ValueError(f"unsupported content type {content_type or 'missing'}")
            if len(data) > MAX_BYTES:
                raise ValueError(f"image larger than {MAX_BYTES} bytes")
            await store.put(key, data, content_type)
            await repo.finish_media(url, key, content_type)
            copied += 1
        except Exception as exc:  # noqa: BLE001 - any failure is one attempt
            for media_id in ids:
                await repo.fail_media(media_id, str(exc)[:300], MAX_ATTEMPTS)
            failed += 1
    return {"copied": copied, "failed": failed}
