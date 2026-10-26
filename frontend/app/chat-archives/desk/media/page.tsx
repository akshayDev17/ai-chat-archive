import MediaDesk from '../../../components/MediaDesk';
import Masthead from '../../../components/Masthead';

/**
 * The media page — `/chat-archives/desk/media`, under the copy desk.
 *
 * Where a filer watches the images of their GenUI stories being copied into the
 * archive, and retries or hurries the copy. Signed-in only: the page bounces to
 * sign in, and every endpoint it calls lives under `/api/desk/`.
 */
export default function MediaPage() {
  return (
    <main className="frame">
      <Masthead section="Copy desk · Media" />
      <MediaDesk />
    </main>
  );
}
