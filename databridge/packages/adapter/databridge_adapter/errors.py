"""The adapter error hierarchy — the named refusals (`ADAPTER_AGENT_SPEC` §10, Appendix A).

**Why these are not `ValueError` subclasses, and why that is deliberate.**

Pydantic v2 catches `ValueError`/`AssertionError` raised inside a validator and re-raises it as a
`ValidationError`, which erases the exception TYPE. A caller that must distinguish "you asked for a
past instant this tier cannot serve" (`CannotPinPast` — recoverable: resolve against the Capture
Store instead) from "that field is not a string" cannot do so through a `ValidationError`.

So the split is:

* **structural / typing violations** → `pydantic.ValidationError`, as usual;
* **contract-invariant violations** → an `AdapterError` subclass, raised *through* the validator
  and reaching the caller with its type intact.

`ADAPTER_AGENT_SPEC` §10's table is a table of *named* refusals precisely because each one has a
different operator response. Collapsing them loses the response.

Every one of these is a **fail-closed** signal. None of them has a "degrade and continue" branch:
a partial result that looks complete is the only outcome worse than an error.
"""
from __future__ import annotations


class AdapterError(Exception):
    """Base of every refusal the adapter port can raise.

    Deliberately NOT a `ValueError` — see the module docstring. Deliberately not carrying a
    machine-readable code either: the class IS the code, and a string code beside it would
    immediately drift from it.
    """


class CapabilityNotAdvertised(AdapterError):
    """The request names a capability, or the adapter emitted a verdict, that `describe()` does
    not declare (`ADAPTER_AGENT_SPEC` §10, §5).

    Both directions matter. Inbound: a Shell that proposes an unadvertised capability is rejected
    and logged, and the rejection is *never* escalated to a model to reconsider. Outbound: an
    adapter that cannot compute benign drift may not emit `DRIFT_BENIGN`, because a verdict nobody
    can substantiate is worse than `UNVERIFIABLE`.
    """


class CannotPinPast(AdapterError):
    """`as_of` names an instant this source's pinning tier cannot serve (`ADAPTER_AGENT_SPEC` §5).

    T1 cursors replay FORWARD only; T2 is current-addressable; T3 has no history at all. The
    honest response is to refuse and point at the Capture Store — never to substitute "now" and
    let the caller believe it received the past.
    """


class UnorderedPointeredRead(AdapterError):
    """A pointered read was requested without `order_by` (`ADAPTER_AGENT_SPEC` §7).

    `row_ordinal` over an unordered result is not a stable citation: it moves under pagination,
    plan changes and ties. Every read this port performs is pointered, so the requirement is on
    the contract rather than on each adapter remembering it.
    """


class UntrustedInControlFlow(AdapterError):
    """An untrusted value reached a position where it decides what happens next (spine D3).

    Untrusted values are **quotable, not actionable**. This is a bug in the caller, not an
    operational condition — it should fail the build, and a human should never see it.
    """


class BudgetExceeded(AdapterError):
    """Admission control refused the read BEFORE it ran (`ADAPTER_AGENT_SPEC` §10).

    After the fact is too late: the cost has already been incurred and, for an egress-classed
    source, the rows have already left.
    """


class UngroundedValue(AdapterError):
    """A cell's value is not what its pointer cites (`ADAPTER_AGENT_SPEC` §4a).

    The pointer may be perfectly well-formed. A pointer proves *citation*; only a comparison
    against the bytes the read returned proves *grounding*. Raising this refuses the whole
    result — a fabricated cell does not get dropped quietly beside honest ones.
    """


class PurposeNotDeclared(AdapterError):
    """The principal's purpose is not in any enabled pack's declared vocabulary (spine D5).

    Fails closed: a purpose the deployment never declared cannot be audited against a lawful
    basis eighteen months later, so it is refused now rather than recorded meaninglessly.
    """


class PredicateGrammarNotDefined(AdapterError):
    """A `predicate` was supplied, and the portable predicate grammar does not exist yet.

    `ADAPTER_AGENT_SPEC` §6 and Appendix A name this as the highest-risk undefined component:
    a closed grammar, an identifier allowlist bound to the projection, literal-free expressions
    with bound params, a type check against pack-declared types, a canonical AST re-serialisation
    that `read_hash` can cover, and per-dialect conformance tests. None of that is built.

    Until it is, §6's own instruction stands — "an adapter accepts `predicate=None` and a fixed
    set of pack-declared named queries" — and the contract enforces it here rather than letting a
    string reach a compiler that would have to guess what it meant.
    """


# ---------------------------------------------------------------------------------------
# Workstreams 3C (Policy) and 3D (Shell). Same rule as above: each refusal is a distinct
# operator response, so each gets a distinct type rather than a string code on one class.
# ---------------------------------------------------------------------------------------


class PurposeNotPermitted(AdapterError):
    """The purpose is DECLARED (a pack vocabulary has it) and is not GRANTED here (spine D5).

    Distinct from `PurposeNotDeclared`, and the distinction is the whole point of the purpose
    check. "We have never heard of this purpose" is a deployment error an engineer fixes; "you
    hold this data for one purpose and asked to use it for another" is a policy refusal an
    auditor must be able to find. `fixtures/packs/finance/roles.yaml`'s `purpose_mismatch`
    demonstration case is the second one: a relationship manager holding a portfolio is not
    thereby permitted to screen it for financial crime.
    """


class PurposeNotBound(AdapterError):
    """A declared pack purpose maps to no purpose in the estate's access model.

    Fails closed and names both files. The alternative — picking the nearest-looking estate
    purpose — silently attaches a lawful basis nobody wrote down, which is exactly what a closed
    vocabulary exists to prevent.
    """


class EgressCeilingExceeded(AdapterError):
    """The source's egress class is above what this principal's roles may receive (§10).

    Refused at admission, before the read. After the read there is nothing to refuse: for an
    egress-classed source the rows have already left.
    """


class PolicyNotApplied(AdapterError):
    """A Core was reached without an admission, or with a decision table that is not the one the
    admission names (`ADAPTER_AGENT_SPEC` Appendix A).

    > "Policy→Core authorisation protocol — needed before the first adapter — a caller must not
    > be able to invoke Core directly and skip Policy; today this is stated, not enforced."

    This is the in-process half of that: a Core refuses construction without a `PolicyEngine`,
    obtains its own admission inside every verb, and re-checks the compiled table's digest before
    it emits a cell. It is **not** cross-process authorisation — that still needs the IPC
    protocol Appendix A lists — but it removes the documented path that skips Policy.
    """


class RowRuleNotCompilable(AdapterError):
    """A row-level rule in the estate's role model cannot be compiled into a decision table.

    Fails **closed**: the read is refused rather than served with the rule quietly dropped. The
    rules that land here are the ones that need the portable predicate grammar (§6, Appendix A) —
    multi-table joins, and filters over mailboxes and document drops that are not relational at
    all.
    """


class UntrustedColumnInPolicy(AdapterError):
    """A compiled policy rule scopes access through a column the source contract does not
    declare TRUSTED (spine D3; five independent Codex reviews of Phase 3A converged on this as
    one root cause — see `wiki/40-reviews/raw/phase3-adapters-*.md`).

    Trust is stamped per field when a pointer is EMITTED (`SourceDescription.trust_of`), but
    nothing checked trust when a value ENTERED policy evaluation. A row-scope predicate's
    comparison column, or a subquery's `select_column`/`where_column`, could name a
    contract-declared UNTRUSTED column — OCR text, an email header, a claimed case reference,
    whatever a third-party MCP server returned — and that attacker-writable value would then
    decide row inclusion, ACL membership, or field disclosure. That is the data-plane twin of
    `UntrustedInControlFlow`, which screens the ORCHESTRATOR's request; this one screens the
    ESTATE's own policy declaration, and both stand on the same rule: untrusted values are
    quotable, never actionable.

    Refused at POLICY COMPILE TIME (`PolicyEngine._compile`), not at row-evaluation time. A rule
    that scopes through an untrusted column is a declaration error in `roles.yaml` — the
    deployment has written a rule that cannot be safely evaluated — so it fails loudly, naming the
    rule and the column, rather than being silently evaluated per row or silently dropped. A
    column the source contract does not classify at all is treated the same as one it classifies
    UNTRUSTED: fail closed, because "never considered" is not evidence of safety.
    """


class EscalationRequired(AdapterError):
    """The Shell could not produce a request inside the advertised capability set (§6, §9).

    > "Where a genuinely novel query is needed, the Shell escalates to a human who adds it to the
    > pack. That is a slower path, and it is the correct one."

    Raised INSTEAD of retrying. A rejection fed back to a model until it guesses something the
    screen accepts is a capability screen that eventually passes anything — which is worse than
    no screen, because it looks like one.
    """
