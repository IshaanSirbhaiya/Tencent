'use client';
// "Add a document" (demo-proof, D-24): a Slack note posted AS the signed-in person, or a real file
// uploaded into a Google Drive folder. The brain learns either one the way it learns any change:
// the source's signed webhook, then re-fetch, mask and index. Nothing here writes the index.
//
// - Slack only (the stage UI never offers Confluence; PLAN §4.4 item 3).
// - The channel list comes ONLY from the person's own /ib/map, so it lists channels they can
//   already read and leaks nothing. The server re-checks live membership at post time.
// - On a refusal the sheet shows one neutral sentence; it never says why (404 is one answer).
import { FileUp, MessageSquarePlus } from 'lucide-react';
import { useCallback, useRef, useState } from 'react';
import { Badge, Button, Field, Input, SheetContent, Tabs, TabsContent, TabsList, TabsTrigger, inputClass } from '@/components/ui';
import { rememberPost } from '@/components/graph/useLiveChanges';
import { ApiError, liveApi } from '@/lib/api';
import { cn } from '@/lib/cn';
import { useMessages } from '@/lib/i18n';
import { graphMessages } from '@/lib/i18n/graph';

export const MAX_NOTE = 2000;
const MAX_FILE = 5 * 1024 * 1024;
export const SAMPLE_FILES = [
  'DB Migration — Wave 4 cutover plan v2.docx',
  'Onboarding buddy guide.md',
  'Payment outage — post-incident review.pptx',
  'Q4 FX fee schedule — SG & ID (draft for review).xlsx',
  'WhatsApp Chat with Ops on-call.txt',
  'vendor-sla-tracker.csv',
];

export interface Channel {
  id: string;
  name: string;
}

type Status = { kind: 'idle' } | { kind: 'busy' } | { kind: 'ok'; text: string } | { kind: 'error'; text: string };

/** The note form's state, owned by the explorer so the Presenter can fill and post it. */
export function useAddNote(channels: Channel[]) {
  const t = useMessages(graphMessages);
  const def = channels.find((c) => c.name === 'db-migration')?.id ?? channels[0]?.id ?? '';
  const [channel, setChannel] = useState<string>(def);
  const [text, setText] = useState('');
  const [status, setStatus] = useState<Status>({ kind: 'idle' });
  const busy = useRef(false);
  const chosen = channels.some((c) => c.id === channel) ? channel : def;

  const post = useCallback(
    async (override?: { channel?: string; text?: string }): Promise<boolean> => {
      if (busy.current) return false;
      const target = override?.channel ?? chosen;
      const body = (override?.text ?? text).trim();
      if (!target || !body || body.length > MAX_NOTE) return false;
      busy.current = true;
      setStatus({ kind: 'busy' });
      try {
        const r = await liveApi.addDocument({ platform: 'slack', target, text: body });
        rememberPost(r.doc_id);
        const name = channels.find((c) => c.id === target)?.name ?? target;
        setStatus({ kind: 'ok', text: t('add.posted', { channel: name }) });
        return true;
      } catch (e) {
        const st = e instanceof ApiError ? e.status : 0;
        setStatus({ kind: 'error', text: st === 429 ? t('add.tooMany') : st === 404 || st === 422 ? t('add.refused') : t('add.failed') });
        return false;
      } finally {
        busy.current = false;
      }
    },
    [chosen, text, channels, t],
  );

  return { channel: chosen, setChannel, text, setText, status, setStatus, post };
}

export type AddNote = ReturnType<typeof useAddNote>;

function toB64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

export function AddDocumentSheet({ note, channels, testId = 'add-doc' }: { note: AddNote; channels: Channel[]; testId?: string }) {
  const t = useMessages(graphMessages);
  const [tab, setTab] = useState<'note' | 'file'>('note');
  const [file, setFile] = useState<File | null>(null);
  const [folder, setFolder] = useState('fd-3');
  const [title, setTitle] = useState('');
  const [up, setUp] = useState<Status>({ kind: 'idle' });
  const [soon, setSoon] = useState(false);

  const upload = async () => {
    if (!file) return;
    if (file.size > MAX_FILE) {
      setUp({ kind: 'error', text: t('add.tooBig') });
      return;
    }
    setUp({ kind: 'busy' });
    try {
      const content_b64 = toB64(await file.arrayBuffer());
      const r = await liveApi.uploadFile({ filename: file.name, content_b64, folder: folder.trim(), ...(title.trim() ? { title: title.trim() } : {}) });
      rememberPost(r.doc_id);
      setUp({ kind: 'ok', text: t('add.uploaded', { name: title.trim() || file.name }) });
    } catch (e) {
      const st = e instanceof ApiError ? e.status : 0;
      // A server without the upload route answers 405 (no POST handler) on some stacks: say "coming soon".
      if (st === 405 || st === 501) setSoon(true);
      setUp({ kind: 'error', text: st === 429 ? t('add.tooMany') : st === 404 || st === 422 || st === 413 ? t('add.uploadRefused') : t('add.failed') });
    }
  };

  const msg = (s: Status, id: string) =>
    s.kind === 'ok' || s.kind === 'error' ? (
      <p role="status" className={cn('text-caption', s.kind === 'ok' ? 'text-ok' : 'text-bad')} data-testid={id}>
        {s.text}
      </p>
    ) : null;

  return (
    <SheetContent
      title={
        <span className="flex items-center gap-2">
          {t('add.title')} <Badge tone="warn">{t('demo.badge')}</Badge>
        </span>
      }
      description={t('add.desc')}
      width="md"
      data-testid={testId}
    >
      <Tabs value={tab} onValueChange={(v) => setTab(v as 'note' | 'file')}>
        <TabsList aria-label={t('add.title')}>
          <TabsTrigger value="note">
            <MessageSquarePlus aria-hidden className="mr-1.5 h-3.5 w-3.5" />
            {t('add.tabNote')}
          </TabsTrigger>
          <TabsTrigger value="file">
            <FileUp aria-hidden className="mr-1.5 h-3.5 w-3.5" />
            {t('add.tabFile')}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="note" className="mt-4 space-y-4">
          {channels.length === 0 ? (
            <p className="text-body-sm text-muted">{t('add.noChannels')}</p>
          ) : (
            <form
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                void note.post();
              }}
            >
              <Field id={`${testId}-channel`} label={t('add.channel')} hint={t('add.channelHint')}>
                <select id={`${testId}-channel`} className={inputClass} value={note.channel} onChange={(e) => note.setChannel(e.target.value)} data-testid={`${testId}-channel`}>
                  {channels.map((c) => (
                    <option key={c.id} value={c.id}>
                      #{c.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field id={`${testId}-text`} label={t('add.text')}>
                <textarea
                  id={`${testId}-text`}
                  className={cn(inputClass, 'min-h-[8rem] resize-y py-2')}
                  maxLength={MAX_NOTE}
                  value={note.text}
                  onChange={(e) => note.setText(e.target.value)}
                  aria-describedby={`${testId}-count`}
                  data-testid={`${testId}-text`}
                />
              </Field>
              <div className="flex items-center gap-3">
                <Button type="submit" variant="primary" busy={note.status.kind === 'busy'} disabled={!note.text.trim() || note.status.kind === 'busy'} data-testid={`${testId}-post`}>
                  {note.status.kind === 'busy' ? t('add.posting') : t('add.post')}
                </Button>
                <span id={`${testId}-count`} className="text-caption tabular-nums text-subtle">
                  {t('add.count', { n: note.text.length.toLocaleString('en-SG') })}
                </span>
              </div>
              {msg(note.status, `${testId}-status`)}
            </form>
          )}
        </TabsContent>

        <TabsContent value="file" className="mt-4 space-y-4">
          {soon ? (
            <p className="text-body-sm text-muted" data-testid={`${testId}-soon`}>
              {t('add.soon')}
            </p>
          ) : (
            <form
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                void upload();
              }}
            >
              <Field id={`${testId}-file`} label={t('add.file')} hint={t('add.fileHint')}>
                <input
                  id={`${testId}-file`}
                  type="file"
                  accept=".docx,.xlsx,.pptx,.pdf,.csv,.md,.txt"
                  className={cn(inputClass, 'py-1.5')}
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  data-testid={`${testId}-file`}
                />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field id={`${testId}-folder`} label={t('add.folder')}>
                  <Input id={`${testId}-folder`} value={folder} onChange={(e) => setFolder(e.target.value)} pattern="[A-Za-z0-9_-]{1,128}" data-testid={`${testId}-folder`} />
                </Field>
                <Field id={`${testId}-ftitle`} label={t('add.fileTitle')}>
                  <Input id={`${testId}-ftitle`} value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} />
                </Field>
              </div>
              <Button type="submit" variant="primary" busy={up.kind === 'busy'} disabled={!file || up.kind === 'busy'} data-testid={`${testId}-upload`}>
                {up.kind === 'busy' ? t('add.uploading') : t('add.upload')}
              </Button>
              {msg(up, `${testId}-upload-status`)}
              <div>
                <p className="text-caption font-medium text-fg">{t('add.samples')}</p>
                <ul className="mt-1 space-y-0.5">
                  {SAMPLE_FILES.map((f) => (
                    <li key={f}>
                      <a className="text-caption text-accent underline-offset-2 hover:underline" href={`/finals/live_kit/files/${encodeURIComponent(f)}`} download>
                        {f}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            </form>
          )}
        </TabsContent>
      </Tabs>
    </SheetContent>
  );
}
