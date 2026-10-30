// One queue for every change of who is signed in to the MAIN session (round-4 review #1, #13).
//
// The presenter (step sign-ins, P's deliverables warm-up, D and its restore, R's sign-outs) and the
// shell (Sign out, persona switch: components/shell/Providers.tsx) all run their session changes
// through `serialSession`, one at a time, in order. So a shell sign-out can never land between the
// warm-up's switch to Leo and its "put the previous person back", and be undone by it.
//
// `owner` counts the changes that OWN the session: the shell's (counted at the click, so work
// already queued sees it at its next check) and a presenter step's sign-in (counted when it runs).
// Borrowers (the warm-up, D) put the previous person back only when no owner changed the session
// meanwhile; otherwise the owner's choice stands. No imports here: the shell imports this module.

let chain: Promise<unknown> = Promise.resolve();
let owner = 0;

/** Run `fn` after every session change queued before it (failures do not break the queue). */
export function serialSession<T>(fn: () => Promise<T>): Promise<T> {
  const next = chain.then(fn, fn);
  chain = next.catch(() => undefined);
  return next;
}

/**
 * Like `serialSession`, but waits for the work queued before it for at most `maxWaitMs`, then runs
 * anyway (a presenter step must never wait forever behind a slow borrower such as the warm-up).
 * Work queued after it still waits for it. Run early, it owns the session (`markSessionOwned`), so
 * a borrower still running sees the change and never puts its previous person back over it.
 */
export function serialSessionWithin<T>(fn: () => Promise<T>, maxWaitMs: number): Promise<T> {
  const before = chain;
  const next = new Promise<T>((resolve, reject) => {
    let ran = false;
    const run = () => {
      if (ran) return;
      ran = true;
      clearTimeout(timer);
      fn().then(resolve, reject);
    };
    const timer = setTimeout(run, maxWaitMs);
    before.then(run, run);
  });
  chain = next.catch(() => undefined);
  return next;
}

/** How many times an owner changed the main session (compare before and after borrowing it). */
export function sessionOwner(): number {
  return owner;
}

/** A presenter step signs its persona in (call inside `serialSession`). */
export function markSessionOwned() {
  owner++;
}

/**
 * The person using the shell signs out or switches persona: work already queued stops putting
 * anyone back, then `fn` runs after it, last.
 */
export function claimSession<T>(fn: () => Promise<T>): Promise<T> {
  owner++;
  return serialSession(fn);
}
