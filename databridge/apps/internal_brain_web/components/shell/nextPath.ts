// The `?next=` parameter that carries a deep link through sign-in.
//
// Only a same-site PATH is ever accepted, never a URL: anything that could leave the site
// (`https://…`, `//evil.example`, `/\evil.example`, `javascript:`), control characters, or a loop
// back to /login is rejected and the caller falls back to the mode's home page. No open redirect.

const MAX_LEN = 512;

/** `next` if it is a safe same-site path (with optional query and hash), otherwise null. */
export function safeNextPath(next: string | null | undefined): string | null {
  if (!next || next.length > MAX_LEN) return null;
  // Must be an absolute path: exactly one leading slash, no backslash anywhere (browsers treat
  // `\` like `/`, so `/\host` would be protocol-relative), no control characters or whitespace.
  if (!next.startsWith('/') || next.startsWith('//') || next.includes('\\')) return null;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f\s]/.test(next)) return null;
  let u: URL;
  try {
    const base = 'http://same.invalid';
    u = new URL(next, base);
    if (u.origin !== base) return null;
  } catch {
    return null;
  }
  if (u.pathname === '/login' || u.pathname.startsWith('/login/')) return null;
  if (u.pathname.startsWith('/api/')) return null;
  return `${u.pathname}${u.search}${u.hash}`;
}

/** The /login URL that comes back to `path` after sign-in ("/" needs no parameter). */
export function loginHref(path: string | null | undefined): string {
  const safe = safeNextPath(path);
  return safe && safe !== '/' ? `/login?next=${encodeURIComponent(safe)}` : '/login';
}
