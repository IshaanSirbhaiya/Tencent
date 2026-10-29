'use client';
import { Boxes } from 'lucide-react';
import { Placeholder } from '@/components/shell/Placeholder';

// Skeleton (Wave 0). The owning page agent replaces this file's body; keep the route.
export default function Page() {
  return (
    <Placeholder icon={Boxes} title="Walnut engine" description="The Walnut evidence engine: entity graph, time slider, provenance to the exact row, and the ontology." />
  );
}
