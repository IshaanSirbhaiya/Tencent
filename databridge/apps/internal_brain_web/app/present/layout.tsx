import type { Metadata } from 'next';
import { PresenterRoot } from '@/components/presenter/PresenterRoot';

export const metadata: Metadata = { title: 'Walnut DataBridge · Demo Day' };

// Full-bleed slides for the Demo Day script (no app shell, no session needed). The presenter bar
// is mounted here so Space keeps driving the script on the slides.
export default function PresentLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <main id="main" tabIndex={-1} className="focus:outline-none">
        {children}
      </main>
      <PresenterRoot />
    </>
  );
}
