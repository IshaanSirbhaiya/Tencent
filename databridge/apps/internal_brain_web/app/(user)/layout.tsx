import { AppShell } from '@/components/shell/AppShell';

// User mode pages that live in this route group render inside the shell. Pages render <Page>.
// (The pre-finals pages /, /map, /compare still wrap themselves; moving them here is the User agent's job.)
export default function UserLayout({ children }: { children: React.ReactNode }) {
  return <AppShell container={false}>{children}</AppShell>;
}
