import { AppShell } from '@/components/shell/AppShell';

// /value is open to every signed-in person in either mode (it keeps the current mode).
export default function ValueLayout({ children }: { children: React.ReactNode }) {
  return <AppShell container={false}>{children}</AppShell>;
}
