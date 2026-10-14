/**
 * A source link we are willing to put in an href: absolute http(s) only. Anything else
 * (javascript:, data:, relative paths, junk) returns null and the caller shows plain text.
 */
export function safeHref(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.href : null;
  } catch {
    return null;
  }
}
