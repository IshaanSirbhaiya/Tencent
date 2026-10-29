'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { EngineError } from '@/lib/engine-api';

export interface EngineQuery<T> {
  data: T | null;
  error: EngineError | null;
  loading: boolean;
  reload: () => void;
}

/**
 * Fetch one engine resource. `key` null = do not fetch. The previous request is aborted when the key
 * changes, so a slow answer for an old selection can never overwrite a newer one.
 */
export function useEngine<T>(key: string | null, fetcher: (signal: AbortSignal) => Promise<T>): EngineQuery<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<EngineError | null>(null);
  const [loading, setLoading] = useState<boolean>(key !== null);
  const [tick, setTick] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    if (key === null) {
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }
    const ctl = new AbortController();
    setLoading(true);
    setError(null);
    fetcherRef
      .current(ctl.signal)
      .then((d) => {
        if (!ctl.signal.aborted) {
          setData(d);
          setLoading(false);
        }
      })
      .catch((e: unknown) => {
        if (ctl.signal.aborted) return;
        setData(null);
        setError(e instanceof EngineError ? e : new EngineError(0, 'bad', e instanceof Error ? e.message : String(e)));
        setLoading(false);
      });
    return () => ctl.abort();
  }, [key, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

/** prefers-reduced-motion or the app's own Reduce motion setting (data-motion="reduce"). */
export function useReducedMotion(): boolean {
  const [reduce, setReduce] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const read = () => setReduce(mq.matches || document.documentElement.getAttribute('data-motion') === 'reduce');
    read();
    mq.addEventListener('change', read);
    const obs = new MutationObserver(read);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-motion'] });
    return () => {
      mq.removeEventListener('change', read);
      obs.disconnect();
    };
  }, []);
  return reduce;
}
