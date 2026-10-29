// Display helpers shared by the canvas, the panel and the table. Pure formatting of API values.
import { PLATFORM_LABEL } from '@/lib/format';
import type { NodeAttrs } from './model';

export const platformName = (p: string | null | undefined) => (p ? PLATFORM_LABEL[p] ?? p : '');

const PLACE_NOUN: Record<string, string> = { confluence: 'space', jira: 'project', slack: 'channel', drive: 'shared drive' };
const DOC_NOUN: Record<string, string> = { page: 'page', issue: 'issue', thread: 'thread', file: 'file' };

/** A place in its platform's own words: "Confluence space", "Slack channel". */
export const placeNoun = (p: string | null | undefined) => (p ? PLACE_NOUN[p] ?? 'place' : 'place');

export function displayLabel(a: Pick<NodeAttrs, 'kind' | 'platform' | 'label'>): string {
  if (a.kind === 'platform') return platformName(a.platform ?? a.label);
  if (a.kind === 'container' && a.platform === 'slack' && !a.label.startsWith('#')) return `#${a.label}`;
  return a.label;
}

export function placeLabel(name: string | null | undefined, platform: string | null | undefined): string {
  if (!name) return '';
  return platform === 'slack' && !name.startsWith('#') ? `#${name}` : name;
}

/** "Jira issue · Payments", "Slack channel · 84 items", "Person · 58 items". */
export function kindLine(a: NodeAttrs): string {
  const n = a.count;
  const items = n === null || n === undefined ? '' : ` · ${n.toLocaleString('en-SG')} ${n === 1 ? 'item' : 'items'}`;
  if (a.kind === 'doc') return `${platformName(a.platform)} ${DOC_NOUN[a.docKind ?? ''] ?? 'document'}${a.containerName ? ` · ${placeLabel(a.containerName, a.platform)}` : ''}`;
  if (a.kind === 'container') return `${platformName(a.platform)} ${placeNoun(a.platform)}${items}`;
  if (a.kind === 'person') return `Person${items}`;
  const places = n === null || n === undefined ? '' : ` · ${n} ${n === 1 ? 'place' : 'places'}`;
  return `Platform${places}`;
}

export function typeName(a: Pick<NodeAttrs, 'kind' | 'platform' | 'docKind'>): string {
  if (a.kind === 'doc') return DOC_NOUN[a.docKind ?? ''] ? capital(DOC_NOUN[a.docKind ?? '']) : 'Document';
  if (a.kind === 'container') return capital(placeNoun(a.platform));
  if (a.kind === 'person') return 'Person';
  return 'Platform';
}

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function fmtCount(n: number): string {
  return n.toLocaleString('en-SG');
}
