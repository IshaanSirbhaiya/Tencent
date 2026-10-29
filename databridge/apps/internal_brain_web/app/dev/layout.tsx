import { AppShell } from '@/components/shell/AppShell';

// Developer mode: every /dev/* page renders inside the shell. Pages render <Page> (or <Placeholder>).
export default function DevLayout({ children }: { children: React.ReactNode }) {
  return <AppShell container={false}>{children}</AppShell>;
}
