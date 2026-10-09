'use client';

import { useState } from 'react';
import { ingestSession, isSignInRequired } from '@/lib/api';

/**
 * The paste-a-share-link box, living in the masthead rather than a tab.
 * On success it announces `archive:updated` on the window, which FrontPage
 * listens for — so the two stay decoupled instead of sharing a parent state.
 */
export default function UploadInline() {
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setNote(null);
    try {
      const result = await ingestSession(url);
      setNote({
        text: `“${result.title}” added — ${result.messages} messages, ${result.citations} citations.`,
        error: false,
      });
      setUrl('');
      window.dispatchEvent(new Event('archive:updated'));
    } catch (error) {
      if (isSignInRequired(error)) {
        // The session expired between page load and submit. Because Cloudflare
        // Access answers a fetch() with a redirect to its login page — which
        // fetch follows as a GET, dropping the POST body — the import would
        // otherwise fail silently. Saying so plainly is the fix.
        setNote({
          text: 'Your session expired — sign in again to import.',
          error: true,
        });
      } else {
        setNote({
          text: error instanceof Error ? error.message : 'Import failed',
          error: true,
        });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="upload" onSubmit={submit}>
      <span className="upload-lb">Add</span>
      <input
        type="text"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="Paste a ChatGPT share link"
        aria-label="ChatGPT share link"
      />
      <button type="submit" disabled={busy}>
        {busy ? 'Importing…' : 'Import'}
      </button>
      {note ? (
        <span className={note.error ? 'upload-msg error' : 'upload-msg'}>{note.text}</span>
      ) : null}
    </form>
  );
}
