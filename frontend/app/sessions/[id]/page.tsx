import Masthead from '../../components/Masthead';
import SessionReader from '../../components/SessionReader';

export default async function SessionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <main className="frame frame-reader">
      <Masthead section="The reading room" />
      <SessionReader id={id} />
    </main>
  );
}
