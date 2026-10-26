'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import { filingStatus, getSession, type FilingStatus } from '@/lib/api';
import { imagesLabel, mediaHref } from '@/lib/media';
import { linkifyCitations } from '@/lib/citations';
import { hasGenUI, stripGenUI } from '@/lib/genui';
import { splitMention, toChatItems } from '@/lib/chat';
import { vendorLabel, vendorMark } from '@/lib/vendors';
import type { SessionDetail, Source } from '@/types';
import GenUIMessage from './GenUI';
import Retract from './Retract';
import SourcesPanel from './SourcesPanel';

export type ReaderMode = 'report' | 'chat';

/**
 * The mark beside each assistant turn: the vendor that produced the reply.
 *
 * Was a generic four-point star, which said "an AI wrote this" and nothing
 * else. The archive is meant to hold more than one tool, so it says *which* —
 * falling back to the star for a vendor with no mark yet (Elicit), which also
 * keeps the shape familiar while it waits for its file.
 */
function BotMark({ vendor, className }: { vendor: string; className?: string }) {
  const mark = vendorMark(vendor);
  if (mark) {
    return (
      <span
        className={`${className ?? ''} bot-mark`}
        style={{ maskImage: `url(${mark})`, WebkitMaskImage: `url(${mark})` }}
        role="img"
        aria-label={vendorLabel(vendor)}
      />
    );
  }
  return <Sparkle className={className} />;
}

function Sparkle({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 2.5l1.9 6.3 6.1 1.8-6.1 1.9L12 18.8l-1.9-6.3L4 10.6l6.1-1.8z" />
    </svg>
  );
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      title={done ? 'Copied' : 'Copy'}
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setDone(true);
          setTimeout(() => setDone(false), 1200);
        });
      }}
    >
      {done ? (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="m20 6-11 11-5-5" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <rect x="9" y="9" width="12" height="12" rx="2" />
          <path d="M5 15V5a2 2 0 0 1 2-2h10" />
        </svg>
      )}
    </button>
  );
}

export default function SessionReader({
  id,
  initialMode,
}: {
  id: string;
  initialMode: ReaderMode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<ReaderMode>(initialMode);
  // Whether the reader filed this story — and so may retract it and follow its
  // image copies. Starts "not yours" and stays so for everyone else, so the
  // controls never flash.
  const [filing, setFiling] = useState<FilingStatus>({ yours: false, images: null });
  // Which reply's sources drawer is open. One at a time — two drawers would
  // just stack, and the index is kept so the `···` that opened it can show it
  // is the active one.
  const [openSources, setOpenSources] = useState<{ index: number; sources: Source[] } | null>(
    null,
  );

  useEffect(() => {
    getSession(id)
      .then(setSession)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load session'));
    filingStatus(id).then(setFiling);
  }, [id]);

  const report = useMemo(
    () => (session?.report ? linkifyCitations(session.report) : ''),
    [session],
  );
  const items = useMemo(() => (session ? toChatItems(session.messages) : []), [session]);

  /**
   * Distinct web sources the *conversation itself* cites, across all turns.
   *
   * Not the same number as `session.citations`, which is what the generated
   * report wrote into its own bibliography — a reply can cite eighteen pages
   * while the report's bibliography lists none, and the transcript header was
   * showing that report number and reading "0 sources" above a wall of links.
   */
  const conversationSources = useMemo(() => {
    const urls = new Set<string>();
    for (const message of session?.messages ?? []) {
      for (const source of message.sources ?? []) urls.add(source.url);
    }
    return urls.size;
  }, [session]);

  // Keep the view in the URL so it is linkable and survives a refresh:
  //   /chat-archives/<id>       → report
  //   /chat-archives/<id>?chat  → chat
  //
  // Written in place rather than with `router.replace`: that is a Next.js
  // *navigation*, which re-requests the route — and, through the Suspense
  // boundary above the reader, re-mounted it — so every toggle re-fetched the
  // session. `history.replaceState` writes the same URL without a navigation,
  // so the toggle stays a state change and the session is fetched once.
  function selectMode(next: ReaderMode) {
    setMode(next);
    window.history.replaceState(null, '', next === 'chat' ? `${pathname}?chat` : pathname);
  }

  if (error) return <div className="status error">{error}</div>;
  if (!session) return <p className="fine">Opening the story…</p>;

  return (
    <article className="reader">
      <div className="reader-top">
        <Link href="/chat-archives" className="back">
          ← All sessions
        </Link>
        {filing.yours ? (
          <div className="reader-tools">
            {filing.images ? (
              // A full navigation: the media page reads `?id` once, on load.
              <a href={mediaHref(id)} className="story-media">
                {imagesLabel(filing.images)} →
              </a>
            ) : null}
            <Retract id={id} onRetracted={() => router.push('/chat-archives')} />
          </div>
        ) : null}
      </div>

      <div className="kicker">{mode === 'report' ? 'The report' : 'The transcript'}</div>
      <h1 className="hl">{session.title}</h1>
      <div className="byline">
        {session.source.toUpperCase()}
        {mode === 'report'
          ? session.citations.length
            ? ` · ${session.citations.length} CITATIONS`
            : ''
          : conversationSources
            ? ` · ${conversationSources} SOURCES`
            : ''}
      </div>

      <div className="rtoggle" role="tablist" aria-label="View">
        <button
          role="tab"
          aria-selected={mode === 'report'}
          className={mode === 'report' ? 'on' : ''}
          onClick={() => selectMode('report')}
        >
          Report
        </button>
        <button
          role="tab"
          aria-selected={mode === 'chat'}
          className={mode === 'chat' ? 'on' : ''}
          onClick={() => selectMode('chat')}
        >
          Chat
        </button>
      </div>

      {mode === 'report' ? (
        <div className="report">
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>
            {report}
          </ReactMarkdown>
        </div>
      ) : (
        <div className="convo">
          <div className="stamp">
            {conversationSources
              ? `${conversationSources} sources · verbatim transcript`
              : 'Verbatim transcript'}
          </div>

          {items.map((item, i) => {
            if (item.kind === 'user') {
              const { mention, rest } = splitMention(item.content);
              return (
                <div className="row row-user" key={i}>
                  <div className="bubble">
                    {mention ? (
                      <span className="chip-inline">
                        <Sparkle className="chip-mark" />
                        {mention}
                      </span>
                    ) : null}
                    {rest}
                  </div>
                </div>
              );
            }

            if (item.kind === 'assistant') {
              const open = openSources?.index === i;
              // ChatGPT's component markup (cards, icon rows, images) renders
              // through the GenUI registry; plain replies keep the Markdown path.
              const genui = hasGenUI(item.raw);
              return (
                <div className="row row-assistant" key={i}>
                  <span className="mark">
                    <BotMark vendor={session.source} />
                  </span>
                  <div className="msg">
                    <div className="plain">
                      {genui ? (
                        <GenUIMessage
                          text={item.raw}
                          sources={item.sources}
                          media={item.media}
                          storyId={id}
                        />
                      ) : (
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.content}</ReactMarkdown>
                      )}
                    </div>
                    <div className="acts">
                      <CopyButton text={genui ? stripGenUI(item.raw) : item.content} />
                      {item.sources.length ? (
                        // The `···` was decoration until there was something
                        // behind it. Now it discloses the reply's sources in the
                        // side drawer, which keeps a long list out of the prose
                        // while leaving it one click away.
                        <button
                          type="button"
                          className={open ? 'more on' : 'more'}
                          title={`Sources (${item.sources.length})`}
                          aria-label={`Sources, ${item.sources.length}`}
                          aria-expanded={open}
                          onClick={() =>
                            setOpenSources(open ? null : { index: i, sources: item.sources })
                          }
                        >
                          ···
                        </button>
                      ) : (
                        <span className="more idle" aria-hidden="true">···</span>
                      )}
                    </div>
                  </div>
                </div>
              );
            }

            if (item.kind === 'tools') {
              return (
                <div className="row row-tool" key={i}>
                  <div className="toolnote">
                    {item.count > 1
                      ? `${item.count} tool outputs redacted by ChatGPT`
                      : 'Tool output redacted by ChatGPT'}
                  </div>
                </div>
              );
            }

            return (
              <div className="sysnote" key={i}>
                {item.content}
              </div>
            );
          })}

          <div className="disclaimer">ChatGPT can make mistakes. Check important info.</div>
        </div>
      )}

      {/* Outside the transcript on purpose: a fixed drawer must not sit inside
          anything that can clip it, and it belongs to the page rather than to
          the reply that opened it. */}
      {openSources ? (
        <SourcesPanel sources={openSources.sources} onClose={() => setOpenSources(null)} />
      ) : null}
    </article>
  );
}
