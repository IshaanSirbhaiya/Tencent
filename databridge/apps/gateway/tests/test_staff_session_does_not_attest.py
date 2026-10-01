"""The shift session identifies the operator. It must never attest to a patient's identity.

`CLAUDE.md` constraint 1 forbids automating identity verification; constraint 4 requires a human
confirmation gate. A staff session is exactly the feature that erodes both if it is built carelessly:
once the terminal knows who is standing at it, the tempting next step is to tick the attestation on
their behalf. That converts a per-patient act of looking at an NRIC into an ambient property of being
logged in, and the ledger's `identity_checked_in_person_by` column then records nothing.

The gateway half of this is already covered (`attested_in_person` is a required boolean on
`SelectPatientRequest`/`ConfirmRequest`). These tests cover the half a backend test cannot see: that
the *frontend* does not quietly supply it.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from fastapi.testclient import TestClient

from conftest import AUTH_HEADERS

from databridge_gateway import create_app

# Reuse the comment-blanking scanner rather than re-deriving "is this line code?".
# The line-skipping version it replaced is exactly what let a hardcoded attestation
# share a line with a comment and go unseen.
from test_no_acme_branding import code_only_ts


# The two shapes a fabricated attestation takes in this frontend. Defined ONCE: the scan below and
# the test that proves the scan works both use these, so a weakened pattern fails its own proof.
# Two copies of a regex is how a guard and its proof drift apart until the proof proves nothing.
#
# The inner alternation matters. A flat `[^)]*` cannot cross the `)` in `staffId.trim()`, so
# `intakeApi.confirm(intakeId, staffId.trim(), true)` — the exact call this frontend makes —
# slipped straight past the first version of this pattern. The proof test below caught it.
ATTESTATION_OBJECT_LITERAL = re.compile(r"attested_in_person\s*:\s*true")
# `re.S` so `.`-free whitespace classes still span the newlines Prettier inserts when it wraps a
# long call. A trailing comma before `)` is Prettier's default for multi-line argument lists, so it
# is matched too — without it the wrapped form of the exact call this guards would slip through.
ATTESTATION_POSITIONAL = re.compile(
    r"\b(?:confirm|selectPatient)\s*\((?:[^()]|\([^()]*\))*,\s*true\s*,?\s*\)", re.S)
_ATTESTATION_PATTERNS_ANY = re.compile(
    f"(?:{ATTESTATION_OBJECT_LITERAL.pattern})|(?:{ATTESTATION_POSITIONAL.pattern})", re.S)


def _hardcodes_attestation(text: str) -> bool:
    return bool(_ATTESTATION_PATTERNS_ANY.search(text))


def _frontend() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[3] / "apps" / "frontend"


def _src(rel: str) -> str:
    return (_frontend() / rel).read_text(encoding="utf-8")


# --- the frontend must not fabricate the attestation ------------------------------------------


def test_the_staff_session_stores_no_attestation_field():
    """If the session object cannot carry an attestation, no component can read one off it.

    This is a shape argument, not a style one: the session is persisted to `sessionStorage` and
    survives navigation, so an `attested` field on it would outlive the patient it referred to.
    """
    src = _src("lib/staff-session.tsx")
    body = src[src.index("export type StaffSession"):src.index("type Ctx")].lower()
    # NOTE the `.lower()` on BOTH sides. This read `"identityChecked" not in body.lower()`, which
    # can never match a lowercased haystack — the check for the most dangerous of the three field
    # names was structurally incapable of failing.
    for forbidden in ("attest", "verified", "identitychecked"):
        assert forbidden not in body, (
            f"StaffSession carries {forbidden!r} — the session says WHO is at the terminal, never "
            f"that an identity was checked")


def test_the_session_guard_would_notice_an_attestation_field():
    """The test above is a string scan, so it is worth proving it can fail.

    `test_no_acme_branding.py` records the same lesson: a guard whose failure mode is silence gets
    reported as coverage.
    """
    body = "export type StaffSession = { staffId: string; identityChecked: boolean };".lower()
    assert any(f in body for f in ("attest", "verified", "identitychecked"))


def test_the_identity_panel_never_stores_a_bare_attested_boolean():
    """The attestation must be keyed to the PATIENT it was made for, not held as a bare flag.

    A bare boolean is what made both halves of this bug possible: it survives a change of selected
    candidate, because picking a different candidate does not change the intake id. `attestedFor`
    holds the patient id, and `attested` is derived from it.
    """
    src = _src("components/frontdesk/identity-panel.tsx")
    code = "\n".join(code_only_ts(src.replace("\r\n", "\n")))

    assert re.search(r"const\s*\[\s*attestedFor\s*,\s*setAttestedFor\s*\]", code), (
        "identity-panel.tsx no longer keys the attestation to a patient id")
    # This line used to read `assert not re.search(...) or "attestedFor" in code`. Because the
    # assertion above already guarantees `attestedFor` is present, the disjunction was always true
    # — a tautology sitting in the middle of the guard file whose entire subject is assertions that
    # cannot fail. Review caught it. What it MEANT to say is that the bare boolean must not come
    # back alongside the keyed one.
    assert not re.search(r"\[\s*attested\s*,\s*setAttested\s*\]", code), (
        "identity-panel.tsx has reintroduced a bare `attested` boolean; an attestation must be "
        "keyed to the patient it was made about")
    # Derived, so the rendered checkbox cannot disagree with what will be submitted.
    assert re.search(r"attested\s*=\s*picked\s*!==\s*null\s*&&\s*attestedFor\s*===\s*picked", code)
    assert "useStaffSession" not in code, (
        "identity-panel.tsx reads the staff session — the attestation must come from the checkbox "
        "the person ticks, not from who is signed in")
    # And it still resets wholesale when the intake changes.
    assert re.search(r"setAttestedFor\(null\)", code)


def test_the_confirm_button_keys_its_attestation_to_the_selected_patient():
    """The half review already caught, pinned so it cannot regress: the confirm request's
    attestation is checked against the patient id CURRENTLY selected on the server."""
    src = _src("app/frontdesk/[id]/page.tsx")
    code = "\n".join(code_only_ts(src.replace("\r\n", "\n")))
    assert re.search(r"const\s*\[\s*confirmAttestedFor\s*,", code)
    assert re.search(r"confirmAttestedFor\s*===\s*\(?\s*gate\?\.selected_patient_id", code), (
        "the confirm attestation is no longer compared against the selected patient id")


def test_no_component_sends_a_hardcoded_true_attestation():
    """A literal true attestation is how this was broken once before (Phase 4b: the confirm button
    sent it unconditionally, unconnected to any checkbox).

    Scans for BOTH shapes. The original only caught the object-literal form
    (`attested_in_person: true`) while this frontend passes the attestation **positionally** —
    `intakeApi.confirm(id, staffId, true)` and `intakeApi.selectPatient(id, patientId, staffId,
    true)` both sailed straight through the check that existed to catch them.
    """
    offenders: list[str] = []
    for path in list(_frontend().rglob("*.tsx")) + list(_frontend().rglob("*.ts")):
        rel = path.relative_to(_frontend()).as_posix()
        if any(part in {"node_modules", ".next"} for part in rel.split("/")):
            continue
        text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        # Comments that DESCRIBE the old bug are the reason it stays fixed — `page.tsx` carries a
        # note about the Phase 4b hardcoded attestation. Only real code counts, and "only real
        # code" is computed by blanking comment spans rather than by skipping whole lines.
        #
        # Scanned as ONE string, not line by line. Prettier wraps a long call across lines, so
        # `intakeApi.confirm(\n  intakeId,\n  staffId,\n  true,\n)` is invisible to a per-line
        # scan — and that is the formatting this repo's own frontend uses.
        blanked = "\n".join(code_only_ts(text))
        for match in _ATTESTATION_PATTERNS_ANY.finditer(blanked):
            line_no = blanked.count("\n", 0, match.start()) + 1
            offenders.append(f"{rel}:{line_no}: {match.group(0).strip()[:120]}")
    assert offenders == [], (
        "the attestation is hardcoded true:\n" + "\n".join(offenders))


@pytest.mark.parametrize("snippet", [
    "intakeApi.confirm(intakeId, staffId.trim(), true)",
    "intakeApi.selectPatient(intakeId, patientId, staffId, true)",
    "body: JSON.stringify({ attested_in_person: true })",
    # Prettier's wrapped form of the first case. A per-line scan cannot see this, and this repo's
    # frontend is Prettier-formatted — so the guard was blind to the shape most likely to occur.
    "intakeApi.confirm(\n  intakeId,\n  staffId.trim(),\n  true,\n)",
])
def test_the_hardcoded_attestation_scan_would_actually_catch_it(snippet):
    """The finding that mattered most in review was that these scans could not detect the bug they
    existed for. Each shape is asserted to be caught."""
    assert _hardcodes_attestation(snippet)


def test_the_sign_in_screen_says_it_is_not_authentication():
    """There is no auth layer behind `staff_id`. STATUS.md records this as an accepted limitation on
    condition that we SAY it rather than imply the gate authenticates anyone."""
    src = _src("components/staff-gate.tsx")
    assert "not authentication" in src.lower()


# --- and the gateway still refuses, whoever is signed in ---------------------------------------


@pytest.fixture()
def client():
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:                  # `with` runs the lifespan
        yield c


def _first_intake(client: TestClient) -> str:
    fixtures = client.get("/intake/fixtures").json()["fixtures"]
    name = next(f for f in fixtures if "chit_01" in f)
    text = client.get(f"/intake/fixtures/{name}").json()["text"]
    res = client.post("/intake/upload", json={"document_id": name, "text": text})
    return res.json()["intake_id"]


def test_a_named_staff_member_alone_cannot_confirm(client):
    """The staff id is present and well-formed. That is not consent, and the ledger stays empty."""
    intake_id = _first_intake(client)
    res = client.post(f"/intake/{intake_id}/confirm",
                      json={"staff_id": "PS-FD-014", "attested_in_person": False})
    assert res.status_code == 422
    assert res.json()["refusal"]["code"] == "IDENTITY_NOT_VERIFIED"
    assert client.get(f"/intake/{intake_id}/ledger").json()["rows"] == []


def test_a_named_staff_member_alone_cannot_select_a_candidate(client):
    intake_id = _first_intake(client)
    candidates = client.get(f"/intake/{intake_id}").json()["identity"]["candidates"]
    res = client.post(f"/intake/{intake_id}/select-patient",
                      json={"patient_id": candidates[0]["patient_id"],
                            "staff_id": "PS-FD-014", "attested_in_person": False})
    assert res.status_code == 422
    assert res.json()["refusal"]["code"] == "IDENTITY_NOT_VERIFIED"
