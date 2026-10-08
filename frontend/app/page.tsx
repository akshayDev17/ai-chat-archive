import FrontPage from './components/FrontPage';
import Masthead from './components/Masthead';

export default function Home() {
  return (
    <main className="frame frame-wide">
      <Masthead showUpload />
      <FrontPage />
    </main>
  );
}
