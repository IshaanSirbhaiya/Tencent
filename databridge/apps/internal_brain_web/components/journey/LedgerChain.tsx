'use client';
import { Link2 } from 'lucide-react';
import { CodeHash } from '@/components/ui';
import { cn } from '@/lib/cn';
import { fmtDate, fmtTime } from '@/lib/format';
import { useLang } from '@/lib/i18n';

export interface ChainItem {
  /** Global ledger seq (compliance) or the per-request ordinal (receipt). */
  label: string;
  kind: string;
  hash: string;
  at?: string | null;
}

const KIND_NOTE: Record<string, string> = {
  'request.accepted': 'before any search',
  'retrieval.decided': 'before any model call',
  'answer.released': 'before the response left',
  'request.failed': 'failed closed',
  // Act on an answer (POST /ib/act/*): the same request id, sealed in the same chain
  'action.requested': 'before the source check, the model or a stored result',
  'action.proposed': 'before the draft or proposal was shown',
  'action.confirmed': 'confirmed by the person, before anything changed',
  'action.executed': 'created in the source by our code',
  'action.failed': 'nothing changed',
  'action.rejected': 'cancelled; nothing changed',
  // Deliverables from an answer (POST /ib/deliverables*): counts and digests only, never the text
  'deliverable.requested': 'before the sources were re-checked or any model was called',
  'deliverable.created': 'before the table, chart or analysis was shown',
  'deliverable.exported': 'before the file left',
  'deliverable.failed': 'nothing was shown',
};
// The same notes in Chinese (round-5 Opus #12 added action.requested to both).
const KIND_NOTE_ZH: Record<string, string> = {
  'request.accepted': '在任何检索之前',
  'retrieval.decided': '在任何模型调用之前',
  'answer.released': '在回复发出之前',
  'request.failed': '已安全拒绝',
  'action.requested': '在核对来源、调用模型或使用已存结果之前',
  'action.proposed': '在显示草稿或提议之前',
  'action.confirmed': '由本人确认，在任何更改之前',
  'action.executed': '由我们的代码在来源中创建',
  'action.failed': '没有任何更改',
  'action.rejected': '已取消；没有任何更改',
  'deliverable.requested': '在重新核对来源或调用任何模型之前',
  'deliverable.created': '在显示表格、图表或分析之前',
  'deliverable.exported': '在文件发出之前',
  'deliverable.failed': '没有显示任何内容',
};

/**
 * The request's sealed records drawn as a vertical hash chain. Each record's hash covers the one
 * before it in the ledger, so the links are drawn between consecutive records.
 */
export function LedgerChain({ items, testId = 'ledger-chain' }: { items: ChainItem[]; testId?: string }) {
  const { lang } = useLang();
  const notes = lang === 'zh' ? KIND_NOTE_ZH : KIND_NOTE;
  if (!items.length) return <p className="text-body-sm text-muted">No sealed records.</p>;
  return (
    <ol className="relative" data-testid={testId}>
      {items.map((it, i) => (
        <li key={`${it.label}-${it.kind}`} className="relative pb-3 pl-7 last:pb-0">
          {i < items.length - 1 ? <span aria-hidden className="absolute left-[11px] top-6 h-[calc(100%-1rem)] w-px bg-line-strong" /> : null}
          <span
            aria-hidden
            className={cn(
              'absolute left-0 top-1 inline-flex h-6 w-6 items-center justify-center rounded-md border bg-surface',
              it.kind === 'request.failed' ? 'border-bad/60 text-bad' : 'border-line-strong text-muted',
            )}
          >
            <Link2 className="h-3 w-3" />
          </span>
          <div className="rounded-md border border-line bg-bg/40 px-3 py-2">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-mono text-caption tabular-nums text-subtle">{it.label}</span>
              <span className="font-mono text-caption text-fg">{it.kind}</span>
              {notes[it.kind] ? <span className="text-caption text-subtle">· {notes[it.kind]}</span> : null}
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
              <CodeHash value={it.hash} head={10} tail={6} label={`${it.kind} hash`} />
              {it.at ? (
                <time dateTime={it.at} className="text-caption tabular-nums text-muted" title={it.at}>
                  {fmtDate(it.at)} · {fmtTime(it.at)}
                </time>
              ) : null}
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}
