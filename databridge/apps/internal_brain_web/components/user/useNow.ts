'use client';
import { useEffect, useState } from 'react';

/** The current time, refreshed every `everyMs` (for "checked live 12 s ago"). */
export function useNow(everyMs = 5000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const h = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(h);
  }, [everyMs]);
  return now;
}
