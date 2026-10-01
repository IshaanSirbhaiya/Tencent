"use client";
/**
 * `/materialise` moved to `/data#agent` (2026-10-01): one Data page replaces both `/materialise` and
 * `/convert` (CLAUDE.md rule 9 — replace, never shadow). The route stays so old links still land;
 * any query string is carried across.
 */
import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

export default function Moved() {
  const router = useRouter();
  const [target, setTarget] = React.useState("/data#agent");
  React.useEffect(() => {
    const to = `/data${window.location.search}#agent`;
    setTarget(to);
    router.replace(to);
  }, [router]);
  return (
    <p className="p-6 text-xs text-subtle" data-testid="moved-to-data">
      This page moved to <Link href={target} className="text-accent hover:underline">Data</Link>.
    </p>
  );
}
