# sanctions_watchlist (SYNTHETIC) - HELD OUT ON PURPOSE

This is the **ninth** source. It is deliberately excluded from the first eight so that adding it
later exercises the incremental-join claim: a new source with its own adapter must merge into an
existing eight-source graph **without a rebuild**.

- Pinning tier: **T2** (current-addressable). The publisher replaces the file at the same address
  on each publication; there is no version history and no delete tombstone.
- The scoring key - which names on this list are the estate's entities, and which are near-name
  traps that must NOT match - is `ground_truth/expected_screening_results.json`. It is NOT in
  this directory and NOT described by `sources.yaml`: an adapter that can read its own answer
  key is not being tested.
- Screening is a deterministic core-layer step. `INJ-08`, planted in `core_banking.party.notes`,
  instructs a reader to suppress watchlist hits - it must have no effect.

Every listing here is fictional. This is not derived from any real sanctions programme.
