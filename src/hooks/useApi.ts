import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';

export interface ApiState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

/**
 * Loads data from the API, optionally polling. While a reload is in flight the previous data
 * stays visible; errors are surfaced, never replaced with placeholder values.
 */
export function useApi<T>(load: () => Promise<T>, deps: unknown[], intervalMs?: number): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  const run = useCallback(() => {
    const mine = ++seq.current;
    setLoading(true);
    loadRef.current()
      .then((d) => {
        if (mine !== seq.current) return;
        setData(d);
        setError(null);
      })
      .catch((e) => {
        if (mine !== seq.current) return;
        setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
      })
      .finally(() => {
        if (mine === seq.current) setLoading(false);
      });
  }, []);

  useEffect(() => {
    setData(null);
    run();
    if (!intervalMs) return;
    const t = setInterval(run, intervalMs);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading, reload: run };
}
