'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { AFTER_SIGN_IN, safeNext } from '@/lib/nav';

type Stage = 'email' | 'otp' | 'verifying' | 'confirmed';

/**
 * The sign-in flow, ending at the copy desk.
 *
 * `next` comes from the URL so a visitor bounced off the copy desk returns
 * there. It is sanitized before being used as a navigation target — see
 * `lib/nav.ts`.
 */
export default function AuthFlow({ next }: { next?: string | null }) {
  const router = useRouter();
  const destination = safeNext(next);
  const [stage, setStage] = useState<Stage>('email');
  const [email, setEmail] = useState('');
  const [digits, setDigits] = useState<string[]>(Array(6).fill(''));
  const refs = useRef<Array<HTMLInputElement | null>>([]);

  // Auto-advance from "verifying" to "confirmed".
  useEffect(() => {
    if (stage !== 'verifying') return;
    const t = setTimeout(() => setStage('confirmed'), 1300);
    return () => clearTimeout(t);
  }, [stage]);

  function handleDigit(idx: number, value: string) {
    const ch = value.replace(/\D/g, '').slice(-1);
    const next = [...digits];
    next[idx] = ch;
    setDigits(next);
    if (ch && idx < 5) refs.current[idx + 1]?.focus();
  }

  // ── email ──────────────────────────────────────────
  if (stage === 'email') {
    return (
      <section>
        <div className="kicker">Reader access</div>
        <h1 className="hl">Sign in to the archive</h1>
        <p className="standfirst">A one-time code is emailed — no password is ever stored.</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (email.trim()) setStage('otp');
          }}
        >
          <div className="field">
            <label htmlFor="email">Email address</label>
            <input
              id="email"
              type="email"
              required
              autoFocus
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <button type="submit" className="btn">Send one-time code</button>
        </form>
      </section>
    );
  }

  // ── OTP ────────────────────────────────────────────
  if (stage === 'otp') {
    return (
      <section>
        <div className="kicker">Reader access</div>
        <h1 className="hl">Enter the six-digit code</h1>
        <p className="standfirst">
          Emailed to <em>{email}</em> · the code expires in 10 minutes.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setStage('verifying');
          }}
        >
          <div className="otp-row">
            {digits.map((d, i) => (
              <input
                key={i}
                ref={(el) => {
                  refs.current[i] = el;
                }}
                inputMode="numeric"
                maxLength={2}
                value={d}
                onChange={(e) => handleDigit(i, e.target.value)}
                aria-label={`Digit ${i + 1}`}
              />
            ))}
          </div>
          <button type="submit" className="btn">Verify</button>
        </form>
        <p className="fine" style={{ marginTop: '1rem' }}>
          Didn&apos;t get it? <a href="#" style={{ color: 'var(--accent)' }}>Resend code</a>
        </p>
      </section>
    );
  }

  // ── verifying ──────────────────────────────────────
  if (stage === 'verifying') {
    return (
      <section>
        <div className="kicker">Reader access</div>
        <h1 className="hl">Verifying your code…</h1>
        <div className="spinner" aria-hidden="true" />
        <p className="fine">Checking the one-time code against the ledger.</p>
      </section>
    );
  }

  // ── confirmed ──────────────────────────────────────
  return (
    <section>
      <div className="kicker">Reader access</div>
      <div className="check" aria-hidden="true">✓</div>
      <h1 className="hl">You are on the desk</h1>
      <p className="standfirst">Signed in. The copy desk is open — file a share link to set it.</p>
      <button type="button" className="btn" onClick={() => router.push(destination)}>
        Open the copy desk
      </button>
    </section>
  );
}
