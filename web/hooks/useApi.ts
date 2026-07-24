import useSWR from 'swr';
import { resolveApiBase, API_HEADERS, snapshotUrl } from '../src/api';

/**
 * useApi — SWR wrapper with null-key support (pass empty string/null/undefined to skip fetching).
 *
 * When no live API is configured (production, no tunnel) the fetcher reads the static
 * snapshot directly. When one is configured it tries live first and falls back to the
 * last-known snapshot on failure — so widgets keep rendering instead of hanging on
 * "Loading…".
 */
export function useApi<T>(
  path: string | null | undefined,
  opts?: { refreshInterval?: number; fallbackData?: T },
) {
  const apiBase = resolveApiBase();
  // SWR cache key is the raw path so it stays stable regardless of live-vs-snapshot source.
  const key = path ? path : null;

  const fetcher = async (p: string): Promise<T> => {
    if (apiBase) {
      try {
        const r = await fetch(`${apiBase}${p}`, { headers: API_HEADERS });
        if (r.ok) return (await r.json()) as T;
      } catch {
        // fall through to snapshot
      }
    }
    // Snapshot: primary source when no live API is configured, else last-known fallback.
    const snap = await fetch(snapshotUrl(p), { cache: 'no-store' });
    if (!snap.ok) throw new Error(snap.statusText);
    return (await snap.json()) as T;
  };

  const { data, error, isLoading, mutate } = useSWR<T>(
    key,
    fetcher,
    { refreshInterval: opts?.refreshInterval, fallbackData: opts?.fallbackData },
  );
  return { data, error, isLoading, mutate };
}
