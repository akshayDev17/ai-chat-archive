'use client';

import { useEffect, useRef } from 'react';
import type { Source } from '@/types';
import { domainOf } from '@/lib/vendors';
import SourceIcon from './SourceIcon';

/**
 * The sources behind one reply, as a panel down the right-hand edge.
 *
 * A sidebar rather than a popover under the `···`, which is both what ChatGPT
 * does and the better fit here: a reply can cite a dozen pages, and a list that
 * long would push the rest of the transcript off the screen if it opened inline.
 * Down the side it stays out of the reading column while remaining open *beside*
 * the sentence it belongs to, which is the whole point of opening it.
 *
 * Each row is a badge, the domain, then the title — the ordering is doing real
 * work. The domain says who is speaking far faster than a headline does, and the
 * headline is what you actually decide on. Two type sizes and a mute colour do
 * all of it. ChatGPT also shows a snippet; `content_references.snippet` was
 * empty in every session inspected, and an empty third line is worse than two.
 *
 * The type is ours: serif titles, mono-uppercase credits, the same paper and ink
 * as the rest of the paper. The number on each row matches the inline marker in
 * the transcript, so `[3]` in the prose and `3` here are visibly the same thing.
 */
export default function SourcesPanel({
  sources,
  onClose,
}: {
  sources: Source[];
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    // A drawer that traps nothing and returns focus nowhere is a keyboard trap
    // in reverse: you can tab into it and then not find your way back.
    closeRef.current?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <>
      {/* Catches the click that closes, and does it on `mousedown` so the click
          is not then delivered to whatever sits underneath. */}
      <div className="src-scrim" onMouseDown={onClose} aria-hidden="true" />

      <aside className="src-drawer" role="dialog" aria-modal="true" aria-label="Sources">
        <header className="src-head">
          <span className="src-head-title">Sources</span>
          <span className="src-count">{sources.length}</span>
          <button
            type="button"
            className="src-close"
            onClick={onClose}
            ref={closeRef}
            aria-label="Close sources"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </header>

        <ol className="src-list">
          {sources.map((source) => (
            <li key={`${source.index}-${source.url}`}>
              <a href={source.url} target="_blank" rel="noopener noreferrer">
                <span className="src-num">{source.index}</span>
                <SourceIcon url={source.url} />
                <span className="src-body">
                  <span className="src-domain">{domainOf(source.url)}</span>
                  <span className="src-title">{source.title}</span>
                  <span className="src-meta">
                    {source.attribution || domainOf(source.url)}
                    {source.pub_date ? (
                      <>
                        {' · '}
                        {new Date(source.pub_date * 1000).toLocaleDateString('en-US', {
                          month: 'short',
                          day: 'numeric',
                          year: 'numeric',
                        })}
                      </>
                    ) : null}
                  </span>
                </span>
              </a>
            </li>
          ))}
        </ol>
      </aside>
    </>
  );
}
