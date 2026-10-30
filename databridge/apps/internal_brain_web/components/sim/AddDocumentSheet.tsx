'use client';
// "Add a document" (demo-proof, D-24): a Slack note posted AS the signed-in person, or a real file
// uploaded into a Google Drive folder. The brain learns either one the way it learns any change:
// the source's signed webhook, then re-fetch, mask and index. Nothing here writes the index.
//
// - Slack only (the stage UI never offers Confluence; PLAN §4.4 item 3).
// - The channel list comes ONLY from the person's own /ib/map, so it lists channels they can
//   already read and leaks nothing. The server re-checks live membership at post time.
// - The upload folder list comes ONLY from GET /sim/upload-folders (D-34): the folders the person
//   may upload into now, by name, checked live by the server. No copy here names a folder. A typed
//   folder ID stays as a collapsed "Advanced" fallback, shown only when that list is empty or fails.
// - On a refusal the sheet shows one neutral sentence; it never says why (404 is one answer).
// - Round-3 review H3: after a successful post or upload the form is cleared, so one more Enter on
//   stage cannot post the same note twice; focus goes back to the empty field. L4: success is a
//   polite status region that stays mounted; an error is an alert.
import { FileUp, MessageSquarePlus } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Badge, Button, Field, Input, SheetContent, Tabs, TabsContent, TabsList, TabsTrigger, inputClass } from '@/components/ui';
import { rememberPost } from '@/components/graph/useLiveChanges';
import { ApiError, liveApi } from '@/lib/api';
import { cn } from '@/lib/cn';
import type { UploadFolder } from '@/lib/types';
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
        setText(''); // posted: an extra Enter must not post the same note again
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

function fmtSize(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

const FOLDER_ID = /^[A-Za-z0-9_-]{1,128}$/;

type Folders = { state: 'loading' } | { state: 'ok'; list: UploadFolder[] } | { state: 'failed' };

/**
 * `presenterOwned`: the presenter's grow step opened the sheet (round-3 Fable #1). Focus then goes
 * to the sheet itself, not its Close button or the text field, so Space keeps driving the script
 * (controller.ts lets `data-presenter-owned` dialogs through) and never closes the sheet or types.
 *
 * Upload folder (D-34): a list of folder NAMES from GET /sim/upload-folders, which returns only the
 * folders this person may upload into now (the server re-checks at upload time anyway). The typed
 * folder ID is a collapsed "Advanced" fallback, offered only when that list is empty or fails.
 */
export function AddDocumentSheet({ note, channels, testId = 'add-doc', presenterOwned = false }: { note: AddNote; channels: Channel[]; testId?: string; presenterOwned?: boolean }) {
  const t = useMessages(graphMessages);
  const [tab, setTab] = useState<'note' | 'file'>('note');
  const [file, setFile] = useState<File | null>(null);
  const [folder, setFolder] = useState(''); // the Advanced fallback: a typed folder ID
  const [picked, setPicked] = useState(''); // the folder chosen from the list (its id)
  const [folders, setFolders] = useState<Folders>({ state: 'loading' });
  const asked = useRef(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const owned = useRef(presenterOwned);
  owned.current = presenterOwned;
  const [title, setTitle] = useState('');
  const [up, setUp] = useState<Status>({ kind: 'idle' });
  const [soon, setSoon] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const postRef = useRef<HTMLButtonElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const uploadRef = useRef<HTMLButtonElement>(null);

  // The submit button is disabled while busy, so focus would fall to <body>: put it back once the
  // request settles (success: the now-empty field; error: the button, to try again).
  const noteKind = note.status.kind;
  const prevNote = useRef(noteKind);
  useEffect(() => {
    // presenter-owned: focus stays on the sheet (Space must not type into the emptied field)
    if (prevNote.current === 'busy' && (noteKind === 'ok' || noteKind === 'error'))
      (owned.current ? contentRef.current : noteKind === 'ok' ? textRef.current : postRef.current)?.focus();
    prevNote.current = noteKind;
  }, [noteKind]);
  const prevUp = useRef(up.kind);
  useEffect(() => {
    if (prevUp.current === 'busy' && (up.kind === 'ok' || up.kind === 'error')) (up.kind === 'ok' ? fileRef.current : uploadRef.current)?.focus();
    prevUp.current = up.kind;
  }, [up.kind]);

  // The folder list is fetched once, the first time the file tab is shown.
  useEffect(() => {
    if (tab !== 'file' || asked.current) return;
    asked.current = true;
    liveApi
      .uploadFolders()
      .then((list) => setFolders({ state: 'ok', list: Array.isArray(list) ? list.filter((f) => f && typeof f.id === 'string' && typeof f.path === 'string' && FOLDER_ID.test(f.id)) : [] }))
      .catch(() => setFolders({ state: 'failed' }));
  }, [tab]);
  const listed = folders.state === 'ok' && folders.list.length > 0;
  const target = listed ? (folders.list.some((f) => f.id === picked) ? picked : '') : folders.state === 'loading' ? '' : folder.trim();

  const pick = (f: File | null) => {
    setFile(f);
    if (f && f.size > MAX_FILE) setUp({ kind: 'error', text: t('add.tooBig') });
    else setUp({ kind: 'idle' });
  };

  const upload = async () => {
    if (!file || up.kind === 'busy' || !FOLDER_ID.test(target)) return;
    if (file.size > MAX_FILE) {
      setUp({ kind: 'error', text: t('add.tooBig') });
      return;
    }
    setUp({ kind: 'busy' });
    try {
      const content_b64 = toB64(await file.arrayBuffer());
      const r = await liveApi.uploadFile({ filename: file.name, content_b64, folder: target, ...(title.trim() ? { title: title.trim() } : {}) });
      rememberPost(r.doc_id);
      // uploaded: clear the file and title so the same file is not uploaded twice by accident
      setFile(null);
      setTitle('');
      if (fileRef.current) fileRef.current.value = '';
      setUp({ kind: 'ok', text: t('add.uploaded', { name: title.trim() || file.name }) });
    } catch (e) {
      const st = e instanceof ApiError ? e.status : 0;
      // A server without the upload route answers 405 (no POST handler) on some stacks: say "coming soon".
      if (st === 405 || st === 501) setSoon(true);
      setUp({ kind: 'error', text: st === 429 ? t('add.tooMany') : st === 404 || st === 422 || st === 413 ? t('add.uploadRefused') : t('add.failed') });
    }
  };

  // A polite status region that stays mounted (its text changes), and an alert for errors.
  // Both stay rendered (never display:none), so assistive tech hears the text when it arrives.
  const msg = (s: Status, id: string) => (
    <div className="text-caption">
      <p role="status" aria-live="polite" className="text-ok" data-testid={id}>
        {s.kind === 'ok' ? s.text : ''}
      </p>
      <p role="alert" className="text-bad" data-testid={`${id}-error`}>
        {s.kind === 'error' ? s.text : ''}
      </p>
    </div>
  );

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
      ref={contentRef}
      {...(presenterOwned ? { 'data-presenter-owned': '' } : {})}
      onOpenAutoFocus={(e) => {
        if (!owned.current) return;
        e.preventDefault();
        contentRef.current?.focus();
      }}
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
                <select id={`${testId}-channel`} aria-describedby={`${testId}-channel-hint`} className={inputClass} value={note.channel} onChange={(e) => note.setChannel(e.target.value)} data-testid={`${testId}-channel`}>
                  {channels.map((c) => (
                    <option key={c.id} value={c.id}>
                      #{c.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field id={`${testId}-text`} label={t('add.text')}>
                <textarea
                  ref={textRef}
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
                <Button ref={postRef} type="submit" variant="primary" busy={note.status.kind === 'busy'} disabled={!note.text.trim() || note.status.kind === 'busy'} data-testid={`${testId}-post`}>
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
                  ref={fileRef}
                  id={`${testId}-file`}
                  type="file"
                  accept=".docx,.xlsx,.pptx,.pdf,.csv,.md,.txt"
                  className={cn(inputClass, 'py-1.5')}
                  onChange={(e) => pick(e.target.files?.[0] ?? null)}
                  aria-describedby={`${testId}-file-hint ${testId}-picked`}
                  data-testid={`${testId}-file`}
                />
              </Field>
              <p id={`${testId}-picked`} className="-mt-2 text-caption text-muted empty:hidden" data-testid={`${testId}-picked`}>
                {file ? t('add.picked', { name: file.name, size: fmtSize(file.size) }) : ''}
              </p>
              <div className="grid gap-4 sm:grid-cols-2">
                {listed ? (
                  <Field id={`${testId}-folders`} label={t('add.folder')} hint={t('add.folderHint')}>
                    <select
                      id={`${testId}-folders`}
                      aria-describedby={`${testId}-folders-hint`}
                      className={inputClass}
                      value={target}
                      onChange={(e) => setPicked(e.target.value)}
                      required
                      data-testid={`${testId}-folders`}
                    >
                      <option value="" disabled>
                        {t('add.folderChoose')}
                      </option>
                      {folders.list.map((f) => (
                        <option key={f.id} value={f.id}>
                          {f.path}
                        </option>
                      ))}
                    </select>
                  </Field>
                ) : (
                  <div className="space-y-1.5">
                    <p className="text-xs font-medium text-fg">{t('add.folder')}</p>
                    <p className="text-caption text-muted" role="status" aria-live="polite" data-testid={`${testId}-folders-state`}>
                      {folders.state === 'loading' ? t('add.foldersLoading') : folders.state === 'failed' ? t('add.foldersFailed') : t('add.foldersNone')}
                    </p>
                  </div>
                )}
                <Field id={`${testId}-ftitle`} label={t('add.fileTitle')}>
                  <Input id={`${testId}-ftitle`} value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} />
                </Field>
              </div>
              {folders.state !== 'loading' && !listed ? (
                <details className="rounded-md border border-line px-3 py-2" data-testid={`${testId}-advanced`}>
                  <summary className="cursor-pointer text-xs font-medium text-fg">{t('add.folderAdvanced')}</summary>
                  <div className="mt-3">
                    <Field id={`${testId}-folder`} label={t('add.folderId')} hint={t('add.folderIdHint')}>
                      <Input id={`${testId}-folder`} aria-describedby={`${testId}-folder-hint`} value={folder} onChange={(e) => setFolder(e.target.value)} pattern="[A-Za-z0-9_-]{1,128}" autoComplete="off" spellCheck={false} data-testid={`${testId}-folder`} />
                    </Field>
                  </div>
                </details>
              ) : null}
              <Button ref={uploadRef} type="submit" variant="primary" busy={up.kind === 'busy'} disabled={!file || up.kind === 'busy' || file.size > MAX_FILE || !FOLDER_ID.test(target)} data-testid={`${testId}-upload`}>
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
