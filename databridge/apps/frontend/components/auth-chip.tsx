"use client";
/**
 * Who this page acts as (FIN-16).
 *
 * Every approval this app sends is recorded against the principal the gateway authenticated the
 * page's credential as. When that credential is the public development token, the record says
 * `dev_only: true`, and the page says so too, plainly, so a demo sign-in is never mistaken for a
 * real person's. Read from `GET /auth/whoami`; nothing here is inferred from the token string.
 */
import { useQuery } from "@tanstack/react-query";
import { sourcesApi, unwrap, type WhoAmI } from "@/lib/sources-api";
import { cn } from "@/lib/utils";

export function useWhoAmI() {
  return useQuery<WhoAmI>({
    queryKey: ["auth-whoami"],
    queryFn: () => unwrap(sourcesApi.whoami()),
    retry: false,
    staleTime: 60_000,
  });
}

/** The plain marker shown beside anything recorded under the development token. */
export function DevTokenMark({ testId }: { testId?: string }) {
  return (
    <span
      data-testid={testId ?? "dev-token-mark"}
      title="Made with the public development token (WALNUT_DEV_AUTH). It is not a real sign-in."
      className="ml-1 inline-flex items-center rounded border border-band-spot/50 px-1 text-2xs font-medium text-band-spot"
    >
      dev token, not a real sign-in
    </span>
  );
}

export function AuthChip({ compact = false }: { compact?: boolean }) {
  const q = useWhoAmI();
  if (q.isLoading) return <span className="text-2xs text-subtle">checking sign-in…</span>;
  if (q.isError || !q.data) {
    return (
      <span data-testid="auth-chip" data-state="none" className="text-2xs text-band-review">
        not signed in
      </span>
    );
  }
  const me = q.data;
  return (
    <span
      data-testid="auth-chip"
      data-state={me.dev_only ? "dev" : "signed-in"}
      className={cn("inline-flex items-center gap-1 text-2xs", compact ? "" : "rounded border border-border px-1.5 py-0.5")}
    >
      <span className="font-mono">{me.principal_id}</span>
      {me.dev_only ? <DevTokenMark testId="auth-chip-dev" /> : !compact && <span className="text-subtle">signed in</span>}
    </span>
  );
}
