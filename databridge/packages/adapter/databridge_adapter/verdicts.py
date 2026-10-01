"""The verdict lattice — `verify()` never returns a boolean (spine D6, `ADAPTER_AGENT_SPEC` §5).

Collapsing these eight states to `True`/`False` throws away the only information a human can act
on. "This citation is no longer verified" is not a task; "the value this determination cites has
changed" and "the mailbox this was captured from no longer has the message" are two different
tasks with two different owners.

**The split that decides whether verification survives contact with production:**
`DRIFT_BENIGN` must not page anyone and `DRIFT_MATERIAL` must always page someone. Get that wrong
and verification is switched off in month three, at which point every pointer in the ledger is
decorative.

**`DRIFT_BENIGN` is an OPTIONAL ADVERTISED CAPABILITY, not a universal verdict.** If verification
only ever hashes the read projection (D6, and it must - hashing the whole schema produces
false-drift storms), then a change to a NON-projected column is invisible and the honest verdict
is `VERIFIED`, not `DRIFT_BENIGN`. Benign drift is only *detectable* where the adapter already
gets a cheap entity-level change signal for free - a row version, `xmin`, an ETag, `historyId`, an
mtime. Adapters that have no such signal must never emit it, and `assert_verdict_advertised`
enforces that rather than trusting each adapter to remember.

What holds everywhere, regardless of tier or capability: **a changed cited value is always
`DRIFT_MATERIAL`**, and no adapter may downgrade to `DRIFT_BENIGN` on the strength of a signal
that does not cover the projection.
"""
from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Iterable

from .errors import CapabilityNotAdvertised

if TYPE_CHECKING:                                                    # pragma: no cover - typing
    from .describe import SourceDescription


class Verdict(str, Enum):
    """The eight states `verify()` may return. Closed."""

    VERIFIED = "verified"                               # re-read matches, digests equal
    VERIFIED_AS_OF_CAPTURE = "verified_as_of_capture"   # matched the capture; live source moved on
    DRIFT_BENIGN = "drift_benign"                       # something moved; nothing we cited
    DRIFT_MATERIAL = "drift_material"                   # a CITED value changed - ESCALATE
    EXPIRED = "expired"                                 # cursor window closed (e.g. Gmail 404)
    VANISHED = "vanished"                               # row / message / file is gone
    FORBIDDEN = "forbidden"                             # policy now denies what it once allowed
    UNVERIFIABLE = "unverifiable"                       # tier cannot answer; NOT a failure

    @property
    def must_escalate(self) -> bool:
        """Does a human have to be told? Exactly one verdict says yes, and always.

        `EXPIRED` and `VANISHED` schedule work (resync, mark unverifiable); `UNVERIFIABLE` is an
        honest property of the tier and pages nobody. Only a changed *cited* value means a
        determination in the ledger now rests on something the source no longer says.
        """
        return self is Verdict.DRIFT_MATERIAL

    @property
    def is_positive(self) -> bool:
        """Did the citation hold? ``VERIFIED_AS_OF_CAPTURE`` counts - it is the DESIGNED outcome
        for a captured T1/T3 artefact, not a degraded one. Demanding `VERIFIED` everywhere would
        assert something the design does not claim (`ADAPTER_AGENT_SPEC` §11.2)."""
        return self in (Verdict.VERIFIED, Verdict.VERIFIED_AS_OF_CAPTURE)


#: **Severity order, worst first** — the join that makes the enum an actual lattice.
#:
#: R-3A finding 10: the eight states had no ordering, so a result covering several pointers -
#: `FORBIDDEN` on one and `DRIFT_MATERIAL` on another - had no defined combined verdict, and every
#: caller was free to pick a different one. The order below is derived from what a human must do,
#: not from how bad each state sounds:
#:
#: * `DRIFT_MATERIAL` is first and dominates everything, including `FORBIDDEN`. A determination in
#:   the ledger now rests on something the source no longer says; the fact that another cited
#:   value has *also* become unreadable does not make that less urgent, and `must_escalate` has to
#:   survive the join or the escalation rule is decorative.
#: * `FORBIDDEN` next: a live policy denial is actionable now (appeal, or re-scope the read).
#: * `VANISHED` then `EXPIRED`: both schedule work - resync, mark unverifiable - and a gone row is
#:   a stronger statement than a closed cursor window.
#: * `UNVERIFIABLE` outranks the positive verdicts because "we could not check this one" must not
#:   be swallowed by "the other seven checked out".
#: * `DRIFT_BENIGN` then `VERIFIED_AS_OF_CAPTURE` then `VERIFIED` - all fine, decreasingly
#:   informative.
_SEVERITY: tuple["Verdict", ...] = (
    Verdict.DRIFT_MATERIAL,
    Verdict.FORBIDDEN,
    Verdict.VANISHED,
    Verdict.EXPIRED,
    Verdict.UNVERIFIABLE,
    Verdict.DRIFT_BENIGN,
    Verdict.VERIFIED_AS_OF_CAPTURE,
    Verdict.VERIFIED,
)


def _severity_index(verdict: "Verdict") -> int:
    return _SEVERITY.index(verdict)


def combine_verdicts(verdicts: "Iterable[Verdict]") -> "Verdict":
    """The join over a set of per-pointer verdicts: **worst news wins**.

    Raises on an empty input, for the same reason `trust_lub` does: the join of the empty set is
    the lattice bottom (`VERIFIED`), and returning it would report "everything checks out" for a
    verification that examined nothing.
    """
    items = list(verdicts)
    if not items:
        raise ValueError(
            "combine_verdicts() over an empty set is not a verdict - a verification that examined "
            "no pointers has not verified anything, and returning VERIFIED would say it had")
    return min(items, key=_severity_index)


#: Verdicts any adapter may emit without advertising anything extra.
UNIVERSAL_VERDICTS = frozenset(v for v in Verdict if v is not Verdict.DRIFT_BENIGN)

#: Verdicts that require an explicit capability declaration in `describe()`.
ADVERTISED_ONLY_VERDICTS = frozenset({Verdict.DRIFT_BENIGN})


def assert_verdict_advertised(verdict: Verdict, description: "SourceDescription") -> Verdict:
    """Refuse a verdict this source's `describe()` does not support it making.

    Two checks, and the second is the one that matters. A source declares
    ``reachable_verdicts`` - the subset of the lattice it can actually produce - and separately
    ``detects_benign_drift``. Emitting `DRIFT_BENIGN` without the change signal that substantiates
    it is worse than `UNVERIFIABLE`: it tells an operator "we looked and it was fine" on the
    strength of a hash that covers only the columns we read.
    """
    if verdict is Verdict.DRIFT_BENIGN and not description.detects_benign_drift:
        raise CapabilityNotAdvertised(
            f"source {description.source_id!r} emitted DRIFT_BENIGN but does not advertise "
            f"detects_benign_drift. Benign drift needs a cheap entity-level change signal (row "
            f"version, xmin, ETag, historyId, mtime) stored beside the projection hash; without "
            f"one the honest verdict is VERIFIED, because a projection-only hash cannot see a "
            f"non-projected column move (§5)")
    if verdict not in description.reachable_verdicts:
        raise CapabilityNotAdvertised(
            f"source {description.source_id!r} emitted {verdict.value!r}, which is not in its "
            f"declared reachable_verdicts "
            f"{sorted(v.value for v in description.reachable_verdicts)}. A verdict the capability "
            f"matrix says is unreachable is a bug in the adapter, not a discovery about the "
            f"source")
    return verdict
