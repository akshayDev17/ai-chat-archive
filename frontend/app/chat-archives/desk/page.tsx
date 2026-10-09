import CopyDesk from '../../components/CopyDesk';
import Masthead from '../../components/Masthead';

/**
 * The copy desk — `/chat-archives/desk`.
 *
 * Named for the place in a newsroom where copy arriving from outside is filed
 * and set into the paper, which is exactly what happens here: a share link goes
 * in, a story comes out. It sits in the same vocabulary as the rest of the
 * site — `THE READING ROOM` for the reader, `READER ACCESS` for sign-in, so
 * `COPY DESK` for filing.
 *
 * It is a sibling of `/chat-archives/login`, not a child of the front page, so
 * that Cloudflare Access can scope it independently:
 *
 *   /chat-archives        the edition         (public)
 *   /chat-archives/login  sign in             (public, or bypassed)
 *   /chat-archives/desk   file copy           (protected)
 *   /chat-archives/<uuid> one story           (public)
 *
 * That is the whole reason this is a path and not a query parameter: Access
 * scopes by path, and "Query strings (such as ?foo=bar) are not supported in
 * Access application paths." A `?desk` would have been un-scopeable, exactly as
 * `?login` was.
 */
export default function DeskPage() {
  return (
    <main className="frame">
      <Masthead section="Copy desk" />
      <CopyDesk />
    </main>
  );
}
