'use client';
// The living style guide for page agents and reviewers. Every primitive, both themes.
// The numbers on this page are SAMPLE values for the style guide only (labelled as such); product
// pages must render backend figures (PLAN rule 5).
import {
  ArrowRight,
  Bell,
  CircleCheck,
  Copy,
  FileText,
  Filter,
  Inbox,
  MoreHorizontal,
  Plus,
  Search,
  Settings,
  ShieldCheck,
  Trash2,
  Zap,
} from 'lucide-react';
import { useState } from 'react';
import { Page } from '@/components/shell/Page';
import {
  Avatar,
  Badge,
  Button,
  Card,
  CardBody,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
  CodeHash,
  DataTable,
  Dialog,
  DialogClose,
  DialogContent,
  DialogTrigger,
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  EmptyState,
  Field,
  IconButton,
  Input,
  KpiTile,
  Kbd,
  PermissionBadge,
  Pill,
  PlatformIcon,
  Popover,
  PopoverContent,
  PopoverTrigger,
  ScrollArea,
  SectionHeader,
  SegmentedControl,
  Separator,
  Sheet,
  SheetContent,
  SheetTrigger,
  Skeleton,
  SkeletonText,
  Stat,
  StatGrid,
  StatusBadge,
  SwitchField,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Tooltip,
  type ColumnDef,
} from '@/components/ui';
import { Sparkline } from '@/components/ui/sparkline';

const PLATFORMS = ['confluence', 'jira', 'slack', 'drive'] as const;

interface Row {
  req: string;
  who: string;
  platform: (typeof PLATFORMS)[number];
  latency: number;
  status: 'ok' | 'warn' | 'error';
}
const ROWS: Row[] = [
  { req: 'req_5e1d07c3d4', who: 'jdoe', platform: 'jira', latency: 1312, status: 'ok' },
  { req: 'req_5e1d08a1b2', who: 'priya.sre', platform: 'confluence', latency: 978, status: 'ok' },
  { req: 'req_5e1d0bb2c3', who: 'ken.ext', platform: 'slack', latency: 1604, status: 'warn' },
  { req: 'req_5e1d0cc3d4', who: 'leo.ops', platform: 'drive', latency: 2210, status: 'error' },
];
const COLUMNS: ColumnDef<Row>[] = [
  { accessorKey: 'req', header: 'Request', cell: (c) => <span className="font-mono text-xs">{c.getValue<string>()}</span> },
  { accessorKey: 'who', header: 'Person' },
  { accessorKey: 'platform', header: 'Platform', cell: (c) => <PlatformIcon platform={c.getValue<string>()} tone="platform" /> },
  { accessorKey: 'latency', header: 'Latency (ms)', meta: { numeric: true } },
  {
    accessorKey: 'status',
    header: 'Status',
    enableSorting: false,
    cell: (c) => {
      const s = c.getValue<Row['status']>();
      return <StatusBadge status={s}>{s === 'ok' ? 'Answered' : s === 'warn' ? 'Fallback' : 'Failed'}</StatusBadge>;
    },
  },
];

const SPARK = [12, 18, 14, 22, 19, 27, 31, 28, 35];

function Section({ id, title, description, children }: { id: string; title: string; description?: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={id} className="space-y-4 border-t border-line pt-8">
      <SectionHeader id={id} title={title} description={description} />
      {children}
    </section>
  );
}

function Swatch({ name, varName, text }: { name: string; varName: string; text?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <span aria-hidden className="h-8 w-8 shrink-0 rounded-md border border-line" style={{ background: `rgb(var(--${varName}))` }} />
      <span className="min-w-0">
        <span className={`block text-xs font-medium ${text ? '' : 'text-fg'}`} style={text ? { color: `rgb(var(--${varName}))` } : undefined}>
          {name}
        </span>
        <span className="block font-mono text-micro text-subtle">--{varName}</span>
      </span>
    </div>
  );
}

/** A compact sample rendered inside a forced theme scope. */
function ThemeSample({ theme }: { theme: 'dark' | 'light' }) {
  return (
    <div data-theme-scope={theme} className="rounded-lg border border-line bg-bg p-4 text-fg" style={{ colorScheme: theme }}>
      <p className="mb-3 text-micro font-semibold uppercase tracking-wider text-subtle">{theme === 'dark' ? 'Dark (default)' : 'Light'}</p>
      <div className="space-y-3">
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" size="sm" icon={Zap}>
            Primary
          </Button>
          <Button size="sm">Secondary</Button>
          <Button size="sm" variant="ghost">
            Ghost
          </Button>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <StatusBadge status="ok">OK</StatusBadge>
          <StatusBadge status="warn">Stale</StatusBadge>
          <StatusBadge status="error">Failed</StatusBadge>
          <PermissionBadge state="full" />
          <PermissionBadge state="partial" />
          <PermissionBadge state="locked" />
        </div>
        <div className="flex flex-wrap gap-3 text-xs">
          {PLATFORMS.map((p) => (
            <PlatformIcon key={p} platform={p} tone="platform" />
          ))}
        </div>
        <KpiTile label="Answers released (sample)" value="1,284" delta={{ text: '+12%', direction: 'up', sentiment: 'good' }} sparkline={<Sparkline values={SPARK} />} />
        <p className="text-sm text-muted">
          Muted text on bg. <span className="text-subtle">Subtle text.</span> <span className="text-accent">Accent link.</span>
        </p>
      </div>
    </div>
  );
}

export default function StyleGuidePage() {
  const [seg, setSeg] = useState<'day' | 'week' | 'month'>('week');
  const [sw, setSw] = useState(true);
  const [check, setCheck] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <Page
      eyebrow="Developer · Design system"
      title="Style guide"
      description="Every Walnut DataBridge primitive, in both themes. Import from @/components/ui. Values on this page are samples for the guide only."
      width="wide"
    >
      <div className="space-y-10">
        <Section id="sg-themes" title="Both themes" description="Dark is the default (projector-tested). Light meets WCAG AA. High contrast is a modifier in Settings.">
          <div className="grid gap-4 md:grid-cols-2">
            <ThemeSample theme="dark" />
            <ThemeSample theme="light" />
          </div>
        </Section>

        <Section id="sg-colour" title="Colour tokens" description="Near-neutral greys and one accent. Every other hue is semantic and always paired with an icon and text.">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
            {['bg', 'surface', 'raised', 'overlay', 'line', 'line-strong'].map((v) => (
              <Swatch key={v} name={v} varName={v} />
            ))}
            {['fg', 'muted', 'subtle', 'accent', 'accent-solid', 'focus'].map((v) => (
              <Swatch key={v} name={v} varName={v} />
            ))}
            {['ok', 'warn', 'bad', 'perm-full', 'perm-partial', 'perm-locked'].map((v) => (
              <Swatch key={v} name={v} varName={v} />
            ))}
            {PLATFORMS.map((p) => (
              <Swatch key={p} name={p} varName={`pf-${p}`} />
            ))}
          </div>
        </Section>

        <Section id="sg-type" title="Type scale" description="Inter (bundled locally). Body is 15 px in User mode and 13 px in Developer mode; every figure uses tabular numerals.">
          <Card>
            <CardBody className="space-y-2">
              <p className="text-display">Display 30 · Walnut DataBridge</p>
              <p className="text-title">Title 22 · Mission control</p>
              <p className="text-heading">Heading 18 · Request journey</p>
              <p className="text-heading-sm">Heading-sm 15 · Sources</p>
              <p className="text-body">Body (mode-dependent) · Answers only from what you can open.</p>
              <p className="text-body-sm text-muted">Body-sm · Secondary copy and table cells.</p>
              <p className="text-caption text-muted">Caption 12 · Updated 2 hours ago</p>
              <p className="text-micro uppercase tracking-wider text-subtle">Micro 11 · Section eyebrow</p>
              <p className="text-kpi tabular-nums">Kpi 28 · 5,958</p>
              <p className="font-mono text-xs">Mono · 9f2c1a7e…be41d0</p>
            </CardBody>
          </Card>
        </Section>

        <Section id="sg-buttons" title="Buttons">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" icon={Plus}>
              Primary
            </Button>
            <Button icon={Filter}>Secondary</Button>
            <Button variant="outline">Outline</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="danger" icon={Trash2}>
              Danger
            </Button>
            <Button variant="link" iconRight={ArrowRight}>
              Link
            </Button>
            <Button busy>Busy</Button>
            <Button disabled>Disabled</Button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="xs">xs</Button>
            <Button size="sm">sm</Button>
            <Button size="md">md</Button>
            <Button size="lg">lg</Button>
            <Separator orientation="vertical" className="mx-2 h-6" />
            <IconButton icon={Settings} label="Settings" />
            <IconButton icon={Bell} label="Notifications" variant="secondary" />
            <IconButton icon={Plus} label="Add" variant="primary" />
            <Tooltip content="Tooltips add a hint to a control that is already named.">
              <IconButton icon={Copy} label="Copy link" noTitle />
            </Tooltip>
          </div>
        </Section>

        <Section id="sg-badges" title="Badges, pills, status, permission, platform, avatar">
          <div className="flex flex-wrap items-center gap-2">
            <Badge>Neutral</Badge>
            <Badge tone="accent">Accent</Badge>
            <Badge tone="ok" icon={CircleCheck}>
              Verified
            </Badge>
            <Badge tone="warn">Warn</Badge>
            <Badge tone="bad">Bad</Badge>
            <Badge tone="locked">Locked</Badge>
            <Pill icon={ShieldCheck}>Internal · Engineering</Pill>
            <Pill tone="accent">Answers only from what you can open</Pill>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status="ok">Within SLO</StatusBadge>
            <StatusBadge status="warn">Older source</StatusBadge>
            <StatusBadge status="error">Chain broken</StatusBadge>
            <StatusBadge status="neutral">Unknown</StatusBadge>
            <PermissionBadge state="full" />
            <PermissionBadge state="partial" />
            <PermissionBadge state="locked" label="Ken cannot open" />
          </div>
          <div className="flex flex-wrap items-center gap-4 text-sm">
            {PLATFORMS.map((p) => (
              <PlatformIcon key={p} platform={p} />
            ))}
            <Separator orientation="vertical" className="h-5" />
            {PLATFORMS.map((p) => (
              <PlatformIcon key={`t-${p}`} platform={p} tone="platform" />
            ))}
          </div>
          <div className="flex items-center gap-2">
            <Avatar name="Jordan Doe" size="sm" />
            <Avatar name="Maya Tan" />
            <Avatar name="Ken Ito" size="lg" label="Ken Ito" />
            <span className="ml-2 inline-flex items-center gap-1 text-sm text-muted">
              Press <Kbd>Ctrl</Kbd>
              <Kbd>K</Kbd> for the command palette
            </span>
          </div>
        </Section>

        <Section id="sg-kpi" title="KPI tiles and stats" description="Exact backend figures with tabular numerals; a footnote names the source.">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <KpiTile label="Requests (sample)" value="1,284" delta={{ text: '+12%', direction: 'up', sentiment: 'neutral', context: 'vs prev. 7 days' }} sparkline={<Sparkline values={SPARK} />} footnote="Source: /audit/stats · ledger seq 1–3,852" />
            <KpiTile label="Leaks in evaluation (sample)" value="0" unit="/ 5,958" footnote="Source: eval_summary.json" />
            <KpiTile label="p50 latency (sample)" value="1,180" unit="ms" delta={{ text: '−84 ms', direction: 'down', sentiment: 'good' }} />
            <KpiTile label="Loading" value="—" loading />
          </div>
          <Card>
            <CardBody>
              <StatGrid cols={4}>
                <Stat label="Model" value="hy3" mono />
                <Stat label="Policy" value="gate-policy-2026.10" mono />
                <Stat label="Citations" value="3" />
                <Stat label="Grounding drops" value="0" />
              </StatGrid>
            </CardBody>
          </Card>
        </Section>

        <Section id="sg-cards" title="Cards and section headers">
          <div className="grid gap-4 md:grid-cols-2">
            <Card aria-labelledby="sg-card-a">
              <CardHeader actions={<IconButton icon={MoreHorizontal} label="More actions" size="sm" />}>
                <CardTitle id="sg-card-a">Source freshness</CardTitle>
                <CardDescription>Last sync per connector, checked every 30 s.</CardDescription>
              </CardHeader>
              <CardBody className="space-y-2 text-sm">
                {PLATFORMS.map((p) => (
                  <div key={p} className="flex items-center justify-between">
                    <PlatformIcon platform={p} tone="platform" />
                    <StatusBadge status={p === 'drive' ? 'warn' : 'ok'}>{p === 'drive' ? 'Outside SLO' : 'Within SLO'}</StatusBadge>
                  </div>
                ))}
              </CardBody>
              <CardFooter>Sample values.</CardFooter>
            </Card>
            <Card interactive className="p-4">
              <SectionHeader as="h3" eyebrow="Eyebrow" title="Interactive card" description="Hover state for clickable cards. Use a real link or button inside for keyboard access." />
              <div className="mt-3">
                <Button size="sm" variant="outline" iconRight={ArrowRight}>
                  Open
                </Button>
              </div>
            </Card>
          </div>
        </Section>

        <Section id="sg-table" title="Data table" description="TanStack Table: sortable headers (aria-sort), Tab to a row, ArrowUp/Down between rows, Enter opens.">
          <DataTable
            caption="Sample requests"
            columns={COLUMNS}
            data={ROWS}
            getRowId={(r) => r.req}
            onRowClick={(r) => setSelected(r.req)}
            rowLabel={(r) => `Open request ${r.req}`}
            isRowSelected={(r) => r.req === selected}
          />
          <p className="text-caption text-muted" role="status">
            {selected ? `Selected ${selected}` : 'No row selected.'}
          </p>
          <DataTable caption="Empty table" columns={COLUMNS} data={[]} empty="No requests in this window." density="compact" />
        </Section>

        <Section id="sg-hash" title="Code hash">
          <div className="flex flex-wrap items-center gap-3">
            <CodeHash value="9f2c1a7e44d0b1c9e3a8f6d2c5b7e0a1f4d9c8b2a7e6f5d4c3b2a1e0f9d8be41d0" />
            <CodeHash value="req_5e1d07c3d4" label="request id" />
            <CodeHash value={null} />
          </div>
        </Section>

        <Section id="sg-forms" title="Form controls">
          <div className="grid gap-4 md:grid-cols-2">
            <Card className="space-y-3 p-4">
              <Field id="sg-input" label="Search documents" hint="Searches only what you can open.">
                <Input id="sg-input" placeholder="e.g. failover runbook" aria-describedby="sg-input-hint" />
              </Field>
              <SegmentedControl
                label="Time window"
                value={seg}
                onChange={setSeg}
                options={[
                  { value: 'day', label: '24 h' },
                  { value: 'week', label: '7 days' },
                  { value: 'month', label: '30 days' },
                ]}
              />
            </Card>
            <Card className="p-4">
              <SwitchField label="Include replay history" hint="Seeded demo questions, labelled as replay." checked={sw} onCheckedChange={setSw} />
              <SwitchField label="Disabled switch" checked={false} onCheckedChange={() => undefined} disabled />
            </Card>
          </div>
        </Section>

        <Section id="sg-tabs" title="Tabs">
          <Tabs defaultValue="overview">
            <TabsList aria-label="Node details">
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="passage">Passage</TabsTrigger>
              <TabsTrigger value="lineage">Lineage</TabsTrigger>
              <TabsTrigger value="why">Why I can see it</TabsTrigger>
            </TabsList>
            <TabsContent value="overview" className="text-sm text-muted">
              Overview panel.
            </TabsContent>
            <TabsContent value="passage" className="text-sm text-muted">
              Passage panel.
            </TabsContent>
            <TabsContent value="lineage" className="text-sm text-muted">
              Lineage panel.
            </TabsContent>
            <TabsContent value="why" className="text-sm text-muted">
              Why panel.
            </TabsContent>
          </Tabs>
          <Tabs defaultValue="a">
            <TabsList variant="segmented" aria-label="Chart range">
              <TabsTrigger value="a">Requests</TabsTrigger>
              <TabsTrigger value="b">Latency</TabsTrigger>
              <TabsTrigger value="c">Models</TabsTrigger>
            </TabsList>
            <TabsContent value="a" className="text-sm text-muted">
              Segmented tabs.
            </TabsContent>
            <TabsContent value="b" className="text-sm text-muted">
              Latency.
            </TabsContent>
            <TabsContent value="c" className="text-sm text-muted">
              Models.
            </TabsContent>
          </Tabs>
        </Section>

        <Section id="sg-overlays" title="Overlays" description="Radix: Esc closes, focus returns to the trigger, the page behind is inert for dialogs and sheets.">
          <div className="flex flex-wrap gap-2">
            <Popover>
              <PopoverTrigger asChild>
                <Button>Popover</Button>
              </PopoverTrigger>
              <PopoverContent>
                <p className="font-medium">Scope</p>
                <p className="mt-1 text-muted">Answers draw only on content you can access.</p>
              </PopoverContent>
            </Popover>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button iconRight={MoreHorizontal}>Menu</Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuLabel>Actions</DropdownMenuLabel>
                <DropdownMenuItem>
                  <FileText aria-hidden className="h-4 w-4 text-muted" />
                  Open journey
                </DropdownMenuItem>
                <DropdownMenuCheckboxItem checked={check} onCheckedChange={(v) => setCheck(!!v)}>
                  Show replay
                </DropdownMenuCheckboxItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem destructive>Remove</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <Sheet>
              <SheetTrigger asChild>
                <Button>Sheet</Button>
              </SheetTrigger>
              <SheetContent title="Payment service failover runbook" description="Confluence · SRE Runbooks · v7">
                <SkeletonText lines={4} label="Loading passage" />
              </SheetContent>
            </Sheet>
            <Dialog>
              <DialogTrigger asChild>
                <Button>Dialog</Button>
              </DialogTrigger>
              <DialogContent
                title="Reveal this answer?"
                description="Revealing is recorded in the audit ledger with your reason."
                footer={
                  <>
                    <DialogClose asChild>
                      <Button variant="ghost">Cancel</Button>
                    </DialogClose>
                    <Button variant="primary">Reveal</Button>
                  </>
                }
              >
                <Field id="sg-reason" label="Reason">
                  <Input id="sg-reason" />
                </Field>
              </DialogContent>
            </Dialog>
          </div>
        </Section>

        <Section id="sg-scroll" title="Scroll area, skeleton, empty state">
          <div className="grid gap-4 md:grid-cols-3">
            <ScrollArea className="h-40 rounded-lg border border-line bg-surface" viewportLabel="Sample scroll area">
              <ul className="p-3 text-sm">
                {Array.from({ length: 20 }, (_, i) => (
                  <li key={i} className="py-1 text-muted">
                    Ledger record {i + 1}
                  </li>
                ))}
              </ul>
            </ScrollArea>
            <Card className="space-y-3 p-4">
              <Skeleton className="h-4 w-1/2" />
              <SkeletonText lines={3} />
            </Card>
            <EmptyState compact icon={Inbox} title="No questions yet" description="Ask something to see it here." action={<Button size="sm" icon={Search}>Ask</Button>} />
          </div>
        </Section>
      </div>
    </Page>
  );
}
