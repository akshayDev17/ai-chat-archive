import { Suspense } from 'react';
import LoginClient from './client';

/**
 * The sign-in screen, at its OWN PATH — `/chat-archives/login`.
 *
 * It used to be `/chat-archives?login`, and the query-parameter trick bought
 * exactly one thing: nothing had to be reserved under `/chat-archives/`, so a
 * bare `<share-id>` segment could never collide with it. That was the wrong
 * trade.
 *
 * A query string is not part of a URL *path*, and Cloudflare Access scopes an
 * application by path only:
 *
 *   "Query strings (such as ?foo=bar) are not supported in Access application
 *    paths."
 *   https://developers.cloudflare.com/cloudflare-one/access-controls/policies/app-paths/
 *
 * So `?login` was unreachable by any Access rule: `/chat-archives` and
 * `/chat-archives?login` were one Access-scoped URL, and the login screen could
 * never be given its own policy. As a real path it can:
 *
 *   /chat-archives        the shelf
 *   /chat-archives/login  this screen      <- independently scoped
 *   /chat-archives/<uuid> a public story   <- independently scoped
 *
 * Access wildcards make that concrete: `example.com/chat-archives/*` covers the
 * two children but "does not cover the parent path", so the shelf can be
 * protected while this screen and the stories stay reachable.
 *
 * The collision that the query parameter avoided is still avoided, for free:
 * share ids are UUIDs, so the static segment `login` can never be one, and
 * Next.js resolves static segments before dynamic ones.
 *
 * `?next` is read here (not on the front page) and handed to AuthFlow, which
 * sanitizes it before navigating. Static export has no server, so the query is
 * read client-side in `client.tsx`.
 */
export default function LoginPage() {
  return (
    <main className="frame">
      <Suspense fallback={null}>
        <LoginClient />
      </Suspense>
    </main>
  );
}
