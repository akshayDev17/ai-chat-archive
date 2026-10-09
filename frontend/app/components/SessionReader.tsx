'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import { getSession } from '@/lib/api';
import { linkifyCitations } from '@/lib/citations';
import { splitMention, toChatItems } from '@/lib/chat';
import type { SessionDetail } from '@/types';

export type ReaderMode = 'report' | 'chat';

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
  const router = useRouter();
  const pathname = usePathname();
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<ReaderMode>(initialMode);

  useEffect(() => {
    getSession(id)
      .then(setSession)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load session'));
  }, [id]);

  const report = useMemo(
    () => (session?.report ? linkifyCitations(session.report) : ''),
    [session],
  );
  const items = useMemo(() => (session ? toChatItems(session.messages) : []), [session]);

  // Keep the view in the URL so it is linkable and survives a refresh:
  //   /chat-archives/<id>       → report
  //   /chat-archives/<id>?chat  → chat
  function selectMode(next: ReaderMode) {
    setMode(next);
    router.replace(next === 'chat' ? `${pathname}?chat` : pathname, { scroll: false });
  }

  if (error) return <div className="status error">{error}</div>;
  if (!session) return <p className="fine">Opening the story…</p>;

  return (
    <article className="reader">
      <Link href="/chat-archives" className="back">
        ← All sessions
      </Link>

      <div className="kicker">{mode === 'report' ? 'The report' : 'The transcript'}</div>
      <h1 className="hl">{session.title}</h1>
      <div className="byline">
        {session.source.toUpperCase()}
        {session.citations.length ? ` · ${session.citations.length} CITATIONS` : ''}
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
            {session.citations.length} sources · verbatim transcript
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
              return (
                <div className="row row-assistant" key={i}>
                  <span className="mark">
                    <Sparkle />
                  </span>
                  <div className="msg">
                    <div className="plain">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.content}</ReactMarkdown>
                    </div>
                    <div className="acts">
                      <CopyButton text={item.content} />
                      <span className="more" aria-hidden="true">···</span>
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
    </article>
  );
}
