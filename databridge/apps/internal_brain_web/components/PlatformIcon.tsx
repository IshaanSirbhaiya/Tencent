import { BookOpen, FolderOpen, Hash, SquareKanban, type LucideIcon } from 'lucide-react';
import { clsx } from 'clsx';
import { PLATFORM_LABEL } from '@/lib/format';

const ICONS: Record<string, LucideIcon> = {
  confluence: BookOpen,
  jira: SquareKanban,
  slack: Hash,
  drive: FolderOpen,
};

/** Platform mark. The icon is decorative; the platform name is always rendered as text (or sr-only). */
export function PlatformIcon({ platform, showLabel = true, className }: { platform: string; showLabel?: boolean; className?: string }) {
  const Icon = ICONS[platform] ?? FolderOpen;
  const label = PLATFORM_LABEL[platform] ?? platform;
  return (
    <span className={clsx('inline-flex items-center gap-1.5', className)}>
      <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded border border-line-strong bg-raised text-muted">
        <Icon aria-hidden className="h-3 w-3" />
      </span>
      {showLabel ? <span>{label}</span> : <span className="sr-only">{label}</span>}
    </span>
  );
}
