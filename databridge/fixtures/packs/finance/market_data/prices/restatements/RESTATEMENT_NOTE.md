# Vendor restatement 2026-07-31

SYNTHETIC. The vendor reissued closing prices for June 2026 after a corporate
action adjustment. The reissued file replaces the same address on the vendor's
endpoint; there is **no version field and no effective-date column** in the
vendor schema.

Rows restated: 39.

Why this matters: a pointer captured against the pre-restatement bytes must
verify to `DRIFT_MATERIAL`, not `VERIFIED`, and a rebuild that silently picks
up the new closes is the failure mode this fixture exists to catch.
