import Masthead from '../../components/Masthead';
import SessionReader from '../../components/SessionReader';

/**
 * /chat-archives/<share-id>          → the report
 * /chat-archives/<share-id>?chat     → the chat
 *
 * The view is read from the query string on the server and handed to the
 * client reader as its initial mode, so the toggle is linkable and survives a
 * refresh without the client needing useSearchParams.
 */
export default async function SessionPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ chat?: string }>;
}) {
  const { id } = await params;
  const { chat } = await searchParams;

  return (
    <main className="frame frame-reader">
      <Masthead section="The reading room" />
      <SessionReader id={id} initialMode={chat !== undefined ? 'chat' : 'report'} />
    </main>
  );
}
