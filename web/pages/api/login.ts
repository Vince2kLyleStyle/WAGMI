import type { NextApiRequest, NextApiResponse } from 'next';

/**
 * Password check for the site gate. On the correct password it sets the gate
 * cookie (the secret token, httpOnly) that middleware.ts verifies. Free — runs as
 * a Vercel serverless function.
 */
export default function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== 'POST') return res.status(405).json({ ok: false });

  let password = '';
  try {
    const body = typeof req.body === 'string' ? JSON.parse(req.body) : req.body;
    password = (body && body.password) || '';
  } catch {
    password = '';
  }

  const expected = process.env.SITE_PASSWORD || '';
  const secret = process.env.SITE_GATE_SECRET || '';

  if (expected && secret && password === expected) {
    const thirtyDays = 60 * 60 * 24 * 30;
    res.setHeader(
      'Set-Cookie',
      `cos_gate=${secret}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${thirtyDays}`,
    );
    return res.status(200).json({ ok: true });
  }
  return res.status(401).json({ ok: false, error: 'wrong password' });
}
