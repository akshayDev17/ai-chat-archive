'use client';

import { useEffect, useRef } from 'react';
import type { Source } from '@/types';
import { domainOf } from '@/lib/vendors';
import SourceIcon from './SourceIcon';

/**
 * The sources behind one reply, in a panel hung off its `···` button.
 *
 * Laid out like ChatGPT's Activity panel — a badge, the domain, then the title
 * on its own line — because that shape is doing real work: the domain tells you
 * *who is speaking* far faster than a headline does, and the headline is what
 * you actually decide on. Two type sizes and a mute colour do all of it.
 *
 * Where it differs is the paper: the title is set in the serif the rest of the
 * site uses rather than a UI sans, the domain is mono-uppercase like every other
 * credit line here, and the numbers match the inline citation markers in the
 * transcript above.
 *
 * Snippets are not shown. ChatGPT has them; `content_references` carries a
 * `snippet` field that is empty in every session inspected so far, and an empty
 * third line would be worse than two.
 */
export default function SourcesPanel({
  sources,
  onClose,
}: {
  sources: Source[];
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  // Close on Escape and on a click outside, which is what any popover owes a
  // keyboard user — the button that opened it stays focusable and reachable.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener('keydown', onKey);
    // `mousedown`, not `click`: closing on click would swallow the click that
    // was meant for whatever the reader clicked next.
    document.addEventListener('mousedown', onClick);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onClick);
    };
  }, [onClose]);

  return (
    <div className="src-panel" ref={ref} role="dialog" aria-label="Sources">
      <div className="src-head">
        <span>Sources</span>
        <span className="src-count">{sources.length}</span>
      </div>

      <ol className="src-list">
        {sources.map((source) => (
          <li key={`${source.index}-${source.url}`}>
            <a href={source.url} target="_blank" rel="noopener noreferrer">
              <SourceIcon url={source.url} />
              <span className="src-body">
                <span className="src-domain">
                  {domainOf(source.url)}
                  {source.pub_date ? (
                    <span className="src-date">
                      {' · '}
                      {new Date(source.pub_date * 1000).toLocaleDateString('en-US', {
                        month: 'short',
                        day: 'numeric',
                        year: 'numeric',
                      })}
                    </span>
                  ) : null}
                </span>
                <span className="src-title">{source.title}</span>
              </span>
              <span className="src-num">{source.index}</span>
            </a>
          </li>
        ))}
      </ol>
    </div>
  );
}
