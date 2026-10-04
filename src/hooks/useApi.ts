import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { requestLoop } from './requestLoop';

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
  const reloadRef = useRef<() => void>(() => {});
  const loadRef = useRef(load);
  loadRef.current = load;
  const reload = useCallback(() => reloadRef.current(), []);

  useEffect(() => {
    const loop = requestLoop(loadRef.current, {
      start: () => setLoading(true),
      success: (result) => { setData(result); setError(null); },
      error: (e) => setError(e instanceof ApiError ? e : new ApiError(0, String(e))),
      settled: () => setLoading(false),
    }, intervalMs);
    reloadRef.current = loop.reload;
    setData(null);
    setError(null);
    loop.reload();
    return () => { loop.dispose(); reloadRef.current = () => {}; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, intervalMs]);

  return { data, error, loading, reload };
}
