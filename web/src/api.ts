/**
 * Shared API utilities — import these instead of duplicating resolveApiBase in every page.
 */

export function resolveApiBase(): string {
  const envVal =
    (process.env.NEXT_PUBLIC_API_URL as string | undefined) ||
    (process.env.NEXT_PUBLIC_API_BASE_URL as string | undefined);
  if (envVal && envVal.trim().length > 0) return envVal.trim();
  // Local dev convenience only: hit the bot's local API. In production with no live
  // API configured we return '' so the fetchers serve static snapshots directly
  // (no tunnel required, no slow failed request to a nonexistent localhost).
  if (process.env.NODE_ENV !== 'production') return 'http://localhost:8000';
  return '';
}

/** True when a reachable live API base is configured (tunnel/cloud); false = snapshot-only. */
export function hasLiveApi(): boolean {
  return resolveApiBase().length > 0;
}

/**
 * Headers sent on every live API request. The ngrok-skip-browser-warning header makes
 * an ngrok tunnel (if ever used) return JSON directly instead of its interstitial page.
 * Harmless on any other backend.
 */
export const API_HEADERS: Record<string, string> = {
  'ngrok-skip-browser-warning': 'true',
};

/**
 * Map a live API path to its static-snapshot slug. Keep this in sync with the
 * exporter (bot/tools/snapshot_export.py). Query strings are dropped — each
 * endpoint has one canonical snapshot.
 *   /v1/summary                 -> summary
 *   /v1/trades/history?limit=50 -> trades-history
 *   /v1/trades/equity-curve     -> trades-equity-curve
 *   /v1/llm/market-view         -> llm-market-view
 */
export function pathToSnapshotSlug(path: string): string {
  return path
    .split('?')[0]
    .replace(/^\/+/, '')
    .replace(/^v1\//, '')
    .replace(/\/+$/, '')
    .replace(/\//g, '-');
}

/**
 * Static-snapshot URL for a given API path. Primary source when no live API is
 * configured; fallback when the live API is unreachable. Base is overridable via
 * NEXT_PUBLIC_SNAPSHOT_BASE (e.g. a Vercel Blob base URL); defaults to /data served
 * statically from web/public/data.
 */
export function snapshotUrl(path: string): string {
  const base = (process.env.NEXT_PUBLIC_SNAPSHOT_BASE as string | undefined) || '/data';
  return `${base.replace(/\/+$/, '')}/${pathToSnapshotSlug(path)}.json`;
}

/**
 * Typed fetch wrapper — resolves the API base, returns null on any failure.
 * When no live API is configured it reads the static snapshot directly; when one is
 * configured it tries live first and falls back to the last-known snapshot on failure.
 * Usage: const data = await apiFetch<MyType>('/v1/endpoint?limit=50');
 */
export async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T | null> {
  const base = resolveApiBase();
  if (base) {
    const merged: RequestInit = {
      cache: 'no-store',
      ...options,
      headers: { ...API_HEADERS, ...(options?.headers || {}) },
    };
    try {
      const res = await fetch(`${base}${path}`, merged);
      if (res.ok) return (await res.json()) as T;
    } catch {
      // fall through to snapshot
    }
  }
  // Snapshot: primary source when no live API is configured, else last-known fallback.
  try {
    const res = await fetch(snapshotUrl(path), { cache: 'no-store' });
    if (res.ok) return (await res.json()) as T;
  } catch {
    // give up
  }
  return null;
}
