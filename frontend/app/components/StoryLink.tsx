'use client';

import Link from 'next/link';
import { imagesLabel, mediaHref, type MediaCounts } from '@/lib/media';
import { byline, headline, teaser } from '@/lib/story';
import type { Session } from '@/types';

export type StoryVariant = 'lead' | 'flow-lead' | 'flow';

interface Props {
  session: Session;
  variant: StoryVariant;
  /** Set only for the signed-in filer's own stories that have images. */
  images?: MediaCounts;
}

/**
 * One story on the front page: the card links through to its report, and —
 * for your own stories with images — a line beneath it links to their copy
 * progress. That line sits beside the card's link, not inside it: links do not
 * nest.
 */
export default function StoryLink({ session, variant, images }: Props) {
  const title = headline(session);
  const standfirst = teaser(session, variant === 'flow' ? 120 : 240);

  return (
    <div className={`story story-${variant}`}>
      <Link className="story-card" href={`/chat-archives/${encodeURIComponent(session.id)}`}>
        {variant !== 'flow' ? <div className="kicker">Lead</div> : null}

        {variant === 'flow' ? (
          <h3 className="head">{title}</h3>
        ) : (
          <h2 className="head">{title}</h2>
        )}

        <div className="by">{byline(session)}</div>
        {standfirst ? <p className="tease">{standfirst}</p> : null}
        <span className="read">Read the report →</span>
      </Link>
      {images ? (
        // A full navigation: the media page reads `?id` once, on load.
        <a className="story-media" href={mediaHref(session.id)}>
          {imagesLabel(images)} →
        </a>
      ) : null}
    </div>
  );
}
