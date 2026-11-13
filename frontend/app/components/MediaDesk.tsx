'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  copiedImageSrc,
  copyMediaNow,
  isSignInRequired,
  mediaFor,
  mediaSummary,
  retryMedia,
  whoami,
} from '@/lib/api';
import type { MediaFiling, MediaImage, MediaStory, Visibility } from '@/types';
import ArchiveImage from './ArchiveImage';
import { VisibilityLabel } from './VisibilityControl';

const HERE = '/chat-archives/desk/media';
/** How often to re-read while anything is still being copied. */
const POLL_MS = 10_000;

/**
 * The media page — `/chat-archives/desk/media`.
 *
 * Images in GenUI replies are copied into the archive's storage in the
 * background, a batch every minute, because filing cannot do it inline on the
 * Workers Free plan. This page makes that visible and gives the filer control:
 * progress per story, every image's status and last error, "Retry failed" for
 * images that gave up, and "Copy now" to run a batch without waiting.
 *
 * `?id=<share-id>` opens one story. The id is read from the URL once, the same
 * way `story.tsx` reads its mode, so the static export needs no dynamic route.
 */
export default function MediaDesk() {
  const [checking, setChecking] = useState(true);
  const [storyId] = useState<string | null>(() =>
    typeof window === 'undefined' ? null : new URLSearchParams(window.location.search).get('id'),
  );

  useEffect(() => {
    let cancelled = false;
    const bounce = () =>
      window.location.replace(`/chat-archives/login?next=${encodeURIComponent(HERE)}`);
    whoami()
      .then((email) => {
        if (cancelled) return;
        if (!email) return bounce();
        setChecking(false);
      })
      .catch(() => {
        if (!cancelled) bounce();
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (checking) return <p className="fine">Opening the media desk…</p>;
  return storyId ? <StoryMedia id={storyId} /> : <MediaList />;
}

function statusWord(f: { copied: number; failed: number; pending: number; total: number }): string {
  if (f.copied === f.total) return 'Done';
  if (f.pending > 0) return 'Copying';
  return 'Needs attention';
}

/** Re-run `load` every POLL_MS while `active` holds. */
function usePolling(load: () => void, active: boolean) {
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [load, active]);
}

function MediaList() {
  const [filings, setFilings] = useState<MediaFiling[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    mediaSummary()
      .then(setFilings)
      .catch((e) => setError(e instanceof Error ? e.message : 'Could not load your media'));
  }, []);
  useEffect(load, [load]);
  usePolling(load, Boolean(filings?.some((f) => f.pending > 0)));

  return (
    <div className="desk media-desk">
      <section className="desk-intro">
        <div className="kicker">Your images</div>
        <h1 className="hl">Media</h1>
        <p className="standfirst">
          Pictures in your filed stories are copied into the archive in the background, a batch
          every minute, so a story keeps its images even after ChatGPT&rsquo;s copies are gone.
        </p>
      </section>

      {error ? <div className="status error">{error}</div> : null}
      {!filings && !error ? <p className="fine">Reading your filings…</p> : null}
      {filings && !filings.length ? (
        <p className="fine">None of your filed stories has images.</p>
      ) : null}

      {filings && filings.length ? (
        <ul className="media-list">
          {filings.map((f) => (
            <li key={f.id}>
              {/* A full navigation: the story view reads `?id` once, on load. */}
              <a href={`${HERE}?id=${encodeURIComponent(f.id)}`} className="media-row">
                <span className="media-title">
                  {f.title}
                  {f.visibility === 'private' ? (
                    <>
                      {' '}
                      <VisibilityLabel visibility="private" />
                    </>
                  ) : null}
                </span>
                <span className="media-count">
                  {f.copied} / {f.total} copied
                  {f.failed ? ` · ${f.failed} failed` : ''}
                </span>
                <span className={`media-state media-${statusWord(f).split(' ')[0].toLowerCase()}`}>
                  {statusWord(f)}
                </span>
              </a>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function StoryMedia({ id }: { id: string }) {
  const [story, setStory] = useState<MediaStory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<'retry' | 'copy' | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(() => {
    mediaFor(id)
      .then(setStory)
      .catch((e) =>
        setError(
          isSignInRequired(e)
            ? 'Your session expired — sign in again.'
            : e instanceof Error
              ? e.message
              : 'Could not load this story’s media',
        ),
      );
  }, [id]);
  useEffect(load, [load]);

  const images = story?.images ?? [];
  const copied = images.filter((i) => i.status === 'copied').length;
  const failed = images.filter((i) => i.status === 'failed').length;
  const pending = images.length - copied - failed;
  usePolling(load, pending > 0);

  async function act(kind: 'retry' | 'copy') {
    setBusy(kind);
    setNote(null);
    try {
      if (kind === 'retry') {
        const reset = await retryMedia(id);
        setNote(`${reset} image${reset === 1 ? '' : 's'} back in the queue.`);
      } else {
        const result = await copyMediaNow(id);
        setNote(
          `Copied ${result.copied}${result.failed ? `, ${result.failed} failed this round` : ''}.`,
        );
      }
      load();
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'That did not work');
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="desk media-desk">
      <a href={HERE} className="back">
        ← All your media
      </a>
      {error ? <div className="status error">{error}</div> : null}
      {!story && !error ? <p className="fine">Reading this story’s images…</p> : null}

      {story ? (
        <>
          <section className="desk-intro">
            <div className="kicker">
              Media · {statusWord({ copied, failed, pending, total: images.length })}
              {story.visibility === 'private' ? (
                <>
                  {' · '}
                  <VisibilityLabel visibility="private" />
                </>
              ) : null}
            </div>
            <h1 className="hl">{story.title}</h1>
            <p className="fine">
              {copied} / {images.length} copied
              {failed ? ` · ${failed} failed` : ''}
              {pending ? ` · ${pending} waiting` : ''}
              {' · '}
              <a href={`/chat-archives/${encodeURIComponent(id)}?chat`} className="linkish">
                Read the story
              </a>
            </p>
            <div className="media-actions">
              {failed ? (
                <button type="button" className="btn" disabled={busy !== null} onClick={() => act('retry')}>
                  {busy === 'retry' ? 'Retrying…' : 'Retry failed'}
                </button>
              ) : null}
              {pending ? (
                <button
                  type="button"
                  className="btn ghost"
                  disabled={busy !== null}
                  onClick={() => act('copy')}
                >
                  {busy === 'copy' ? 'Copying…' : 'Copy now'}
                </button>
              ) : null}
            </div>
            {note ? (
              <p className="file-msg" role="status">
                {note}
              </p>
            ) : null}
          </section>

          <ul className="media-grid">
            {images.map((image) => (
              <MediaTile key={image.id} image={image} visibility={story.visibility} />
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}

function MediaTile({ image, visibility }: { image: MediaImage; visibility: Visibility }) {
  return (
    <li className={`media-tile media-${image.status}`}>
      {image.status === 'copied' && image.r2_key ? (
        <ArchiveImage src={copiedImageSrc(image.r2_key, visibility)} alt="" loading="lazy" />
      ) : (
        <div className="media-placeholder">{image.status === 'failed' ? '!' : '…'}</div>
      )}
      <div className="media-meta">
        <span>
          <span className="media-state-word">{image.status}</span>
          {image.attempts ? ` · ${image.attempts} attempt${image.attempts === 1 ? '' : 's'}` : ''}
        </span>
        {image.last_error ? <span className="media-error">{image.last_error}</span> : null}
        {image.origin_url ? (
          <a href={image.origin_url} target="_blank" rel="noreferrer" className="media-origin">
            source page ↗
          </a>
        ) : null}
      </div>
    </li>
  );
}
