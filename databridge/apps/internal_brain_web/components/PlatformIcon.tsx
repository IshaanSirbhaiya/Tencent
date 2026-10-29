import { BookOpen, FolderOpen, Hash, SquareKanban, type LucideIcon } from 'lucide-react';
import { cn } from '@/lib/cn';
import { PLATFORM_LABEL } from '@/lib/format';

const ICONS: Record<string, LucideIcon> = {
  confluence: BookOpen,
  jira: SquareKanban,
  slack: Hash,
  drive: FolderOpen,
};

/** Platform identity colour (token) for icon tint, borders, graph nodes and chart series. */
export const PLATFORM_TONE: Record<string, { text: string; border: string; bg: string; fill: string }> = {
  confluence: { text: 'text-pf-confluence', border: 'border-pf-confluence/40', bg: 'bg-pf-confluence/10', fill: 'rgb(var(--pf-confluence))' },
  jira: { text: 'text-pf-jira', border: 'border-pf-jira/40', bg: 'bg-pf-jira/10', fill: 'rgb(var(--pf-jira))' },
  slack: { text: 'text-pf-slack', border: 'border-pf-slack/40', bg: 'bg-pf-slack/10', fill: 'rgb(var(--pf-slack))' },
  drive: { text: 'text-pf-drive', border: 'border-pf-drive/40', bg: 'bg-pf-drive/10', fill: 'rgb(var(--pf-drive))' },
};
const NEUTRAL = { text: 'text-muted', border: 'border-line-strong', bg: 'bg-raised', fill: 'rgb(var(--muted))' };

/**
 * Platform mark. The icon is decorative; the platform name is always rendered as text (or sr-only).
 * `tone="platform"` tints the mark with the platform colour (always alongside icon + name).
 */
export function PlatformIcon({
  platform,
  showLabel = true,
  className,
  tone = 'neutral',
  size = 'md',
}: {
  platform: string;
  showLabel?: boolean;
  className?: string;
  tone?: 'neutral' | 'platform';
  size?: 'sm' | 'md';
}) {
  const Icon = ICONS[platform] ?? FolderOpen;
  const label = PLATFORM_LABEL[platform] ?? platform;
  const t = tone === 'platform' ? PLATFORM_TONE[platform] ?? NEUTRAL : NEUTRAL;
  return (
    <span className={cn('inline-flex items-center gap-1.5', className)} data-platform={platform}>
      <span className={cn('inline-flex shrink-0 items-center justify-center rounded border', size === 'sm' ? 'h-4 w-4' : 'h-5 w-5', t.border, t.bg, t.text)}>
        <Icon aria-hidden className={size === 'sm' ? 'h-2.5 w-2.5' : 'h-3 w-3'} />
      </span>
      {showLabel ? <span>{label}</span> : <span className="sr-only">{label}</span>}
    </span>
  );
}
