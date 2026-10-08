import AuthFlow from '../components/AuthFlow';
import Masthead from '../components/Masthead';

export default function Login() {
  return (
    <main className="frame">
      <Masthead section="Reader access" />
      <AuthFlow />
    </main>
  );
}
