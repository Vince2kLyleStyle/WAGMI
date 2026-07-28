import { useState } from 'react';
import Head from 'next/head';
import { useRouter } from 'next/router';
import type { NextPage } from 'next';

const LoginPage: NextPage & { noLayout?: boolean } = () => {
  const router = useRouter();
  const [pw, setPw] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!pw || busy) return;
    setBusy(true);
    setErr('');
    try {
      const res = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: pw }),
      });
      if (res.ok) {
        const from = typeof router.query.from === 'string' ? router.query.from : '/';
        window.location.href = from.startsWith('/') ? from : '/';
      } else {
        setErr('Incorrect password.');
        setBusy(false);
      }
    } catch {
      setErr('Something went wrong — try again.');
      setBusy(false);
    }
  }

  return (
    <>
      <Head>
        <title>WAGMI — Enter</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <meta name="robots" content="noindex" />
      </Head>
      <main
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background:
            'radial-gradient(1200px 600px at 50% -10%, rgba(0,204,136,0.10), transparent 60%), #050508',
          fontFamily: "'Inter', system-ui, -apple-system, sans-serif",
          padding: 24,
        }}
      >
        <form
          onSubmit={submit}
          style={{
            width: '100%',
            maxWidth: 380,
            background: 'rgba(13,13,20,0.7)',
            border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: 16,
            padding: '32px 28px',
            backdropFilter: 'blur(10px)',
            boxShadow: '0 24px 60px rgba(0,0,0,0.5)',
          }}
        >
          <div
            style={{
              width: 40,
              height: 40,
              borderRadius: 10,
              border: '1.5px solid #00cc88',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#00cc88',
              fontWeight: 800,
              fontFamily: 'JetBrains Mono, monospace',
              marginBottom: 18,
            }}
          >
            W
          </div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: '#f0f0f5', margin: '0 0 6px', letterSpacing: -0.3 }}>
            This site is private
          </h1>
          <p style={{ fontSize: 14, color: '#a0a0b8', margin: '0 0 22px', lineHeight: 1.6 }}>
            Enter the password to continue.
          </p>

          <input
            type="password"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            placeholder="Password"
            autoFocus
            autoComplete="current-password"
            style={{
              width: '100%',
              padding: '12px 14px',
              fontSize: 15,
              color: '#f0f0f5',
              background: '#0a0a0f',
              border: `1px solid ${err ? '#ff4466' : 'rgba(255,255,255,0.12)'}`,
              borderRadius: 9,
              outline: 'none',
              marginBottom: 12,
              boxSizing: 'border-box',
            }}
          />
          {err && <div style={{ color: '#ff4466', fontSize: 13, marginBottom: 12 }}>{err}</div>}

          <button
            type="submit"
            disabled={busy || !pw}
            style={{
              width: '100%',
              padding: '12px 14px',
              fontSize: 14,
              fontWeight: 700,
              color: '#050508',
              background: busy || !pw ? '#0a6b4a' : '#00cc88',
              border: 'none',
              borderRadius: 9,
              cursor: busy || !pw ? 'default' : 'pointer',
              transition: 'background 0.15s',
            }}
          >
            {busy ? 'Checking…' : 'Enter'}
          </button>
        </form>
      </main>
    </>
  );
};

LoginPage.noLayout = true;
export default LoginPage;
