import UploadInline from './UploadInline';

function edition(): string {
  const now = new Date();
  const date = now.toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
    year: 'numeric',
  });
  return `Pune · ${date}`;
}

export default function Masthead({
  section,
  showUpload = false,
}: {
  section?: string;
  showUpload?: boolean;
}) {
  return (
    <header className="masthead">
      <div className="row">
        <div className="mh-left">
          <span className="name">The AI Digest</span>
          <span className="edition">{section ?? edition()}</span>
        </div>
        {showUpload ? <UploadInline /> : null}
      </div>
      <div className="tagline">An AI conversation newspaper · the sessions, as stories</div>
    </header>
  );
}
