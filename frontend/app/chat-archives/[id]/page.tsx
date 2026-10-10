import { Suspense } from 'react';
import Story from './story';

/**
 * /chat-archives/<share-id>          → the report
 * /chat-archives/<share-id>?chat     → the chat
 *
 * Static export (`output: 'export'`) has no server to read `params` at request
 * time, so the id is resolved client-side in `story.tsx`. `generateStaticParams`
 * emits one placeholder page whose HTML the serving layer rewrites every
 * `/chat-archives/<uuid>` request onto; the client then reads the real id from
 * the URL. Share ids are UUIDs, so `_story` can never collide with a real one.
 */
export default function SessionPage() {
  return (
    <main className="frame frame-reader">
      <Suspense fallback={null}>
        <Story />
      </Suspense>
    </main>
  );
}

export function generateStaticParams() {
  return [{ id: '_story' }];
}
