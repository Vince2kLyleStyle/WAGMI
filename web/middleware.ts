import { NextRequest, NextResponse } from 'next/server';

/**
 * Site-wide password gate (free — runs on Vercel's edge, no Pro plan needed).
 * Everything is blocked until the visitor enters the password on /login, which
 * sets an httpOnly cookie the middleware checks. The cookie holds a secret token
 * (SITE_GATE_SECRET), never the password itself.
 *
 * Excluded from the gate: the login page + its API, and Next's own static assets.
 */
export const config = {
  matcher: ['/((?!api/login|login|_next/static|_next/image|favicon.ico).*)'],
};

export function middleware(req: NextRequest) {
  const secret = process.env.SITE_GATE_SECRET || '';
  const gate = req.cookies.get('cos_gate')?.value;

  // If no gate is configured, fail OPEN (never lock people out by misconfiguration).
  if (!secret) return NextResponse.next();
  if (gate && gate === secret) return NextResponse.next();

  const url = req.nextUrl.clone();
  url.pathname = '/login';
  url.search = `?from=${encodeURIComponent(req.nextUrl.pathname)}`;
  return NextResponse.redirect(url);
}
