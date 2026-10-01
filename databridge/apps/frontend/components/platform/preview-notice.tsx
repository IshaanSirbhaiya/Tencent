import { FlaskConical, Radio } from "lucide-react";

/**
 * The line every unwired platform surface carries.
 *
 * A placeholder that does not announce itself is indistinguishable from a claim. This says which
 * gateway route replaces the shape on screen, so a reader can tell "designed" from "measured" at a
 * glance and knows exactly what has to exist for it to become real.
 */
export function PreviewNotice({ endpoint, children }: { endpoint: string; children?: React.ReactNode }) {
  return (
    <div
      data-testid="preview-notice"
      className="flex items-start gap-2 rounded border border-band-spot/40 bg-band-spot/[0.06] px-3 py-2 text-2xs text-subtle"
    >
      <FlaskConical className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-spot" />
      <span>
        <span className="font-medium text-fg">Preview — wired in Phase 5.</span> The layout and the
        payload shape are real; the figures are illustrative synthetic values from{" "}
        <code>config/platform-preview.json</code>. Live data arrives from{" "}
        <code className="text-fg">{endpoint}</code>, and nothing on this page changes when it does.
        {children ? <> {children}</> : null}
      </span>
    </div>
  );
}

/** Section shell shared by the platform surfaces, so five pages agree on what a panel looks like. */
export function Panel({
  title,
  hint,
  className,
  children,
}: {
  title: string;
  hint?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={`flex flex-col rounded-lg border border-border bg-surface ${className ?? ""}`}>
      <div className="flex items-baseline justify-between gap-3 border-b border-border px-4 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-subtle">{title}</h2>
        {hint ? <span className="text-2xs text-subtle">{hint}</span> : null}
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

/**
 * The same line, but for a surface that TRIES the gateway first.
 *
 * When the route answers, this says so and names it — a reader should never have to guess whether
 * the numbers in front of them were measured. When it does not, it degrades to `PreviewNotice` and
 * carries the reason the fetch failed rather than silently showing shapes.
 */
export function SourceNotice({
  live,
  endpoint,
  reason,
  children,
}: {
  live: boolean;
  endpoint: string;
  reason?: string | null;
  children?: React.ReactNode;
}) {
  if (!live) {
    return (
      <PreviewNotice endpoint={endpoint}>
        {reason ? <span className="opacity-80">Last attempt: {reason}.</span> : null}
        {children}
      </PreviewNotice>
    );
  }
  return (
    <div
      data-testid="live-notice"
      className="flex items-start gap-2 rounded border border-band-auto/40 bg-band-auto/[0.06] px-3 py-2 text-2xs text-subtle"
    >
      <Radio className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-auto" />
      <span>
        <span className="font-medium text-fg">Live.</span> Served by{" "}
        <code className="text-fg">{endpoint}</code>.{children ? <> {children}</> : null}
      </span>
    </div>
  );
}
