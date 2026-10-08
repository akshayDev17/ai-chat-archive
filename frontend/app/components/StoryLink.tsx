'use client';

import Link from 'next/link';
import { byline, headline, teaser } from '@/lib/story';
import type { Session } from '@/types';

export type StoryVariant = 'lead' | 'flow-lead' | 'flow';

interface Props {
  session: Session;
  variant: StoryVariant;
}

export default function StoryLink({ session, variant }: Props) {
  const title = headline(session);
  const standfirst = teaser(session, variant === 'flow' ? 120 : 240);

  return (
    <Link
      className={`story story-${variant}`}
      href={`/sessions/${encodeURIComponent(session.id)}`}
    >
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
  );
}
