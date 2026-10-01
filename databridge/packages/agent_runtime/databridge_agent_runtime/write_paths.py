"""Where a file-writing action writes, and why a file name the runtime assembled is grounded
(E2E run 3, P-04).

Run 3's follow-up drafts never landed. Two shapes of the same failure:

* ``larkspur-followups/INV-2026-0057.txt`` — the CONNECTION ID used as a folder inside the
  connection's own writable root; the writer refused ("the parent folder must already exist").
* ``run-<run id>/INV-2026-0057_follow_up.txt`` — a folder the proposal cannot prove exists, and a
  file name joined from cited values that the grounding gate read as one unknown token.

So, deterministically, BEFORE the action is checked or proposed:

1. **normalise** — a path-valued argument of a write (an argument named as a path by the tool's
   schema, or by its own key when no schema is published) is written DIRECTLY in the connection's
   root: separators are unified and only the last segment (the file name) is kept. A create-only
   write's reversal is "delete that file"; a write into a folder would presuppose a folder no
   proposal can show exists, and creating one would touch more than the file. The folder the
   planner asked for stays on the record (``requested_args``) and the change is reported.

2. **derive** — a file name whose stem is not itself a grounded token is offered to the SAME
   replayable-derivation gate a planner's own derivations pass (`answer.verify_derivations`): the
   runtime proposes ``concat`` over the stem's pieces (split on ONE separator, ``_`` ``-`` ``.``,
   a piece that is not grounded split again, two levels at most). The gate accepts it only when
   every piece is grounded (cited evidence or the run context / owner text), at least one comes
   from cited evidence, and the replay gives exactly the stem — then it is cited by a ``derived``
   pointer. Nothing is loosened: a piece found nowhere still refuses, and only PATH arguments of
   WRITE actions are ever decomposed.

Nothing here names a tool, a server, a connection or a domain.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence

#: Argument names that hold a file path (an MCP convention, not a tool's name).
PATH_ARG_NAMES = frozenset({"path", "file_path", "filepath", "filename", "file_name"})
#: Separators a stem may be split on — each one `derive.concat` can join with.
_SPLIT_SEPS = ("_", "-", ".")
_EXT = re.compile(r"^(.+)\.([A-Za-z0-9]{1,5})$")


def path_arguments(args: Mapping[str, Any], schema: Optional[Mapping[str, Any]]) -> list[str]:
    """The argument names that hold a path: string properties of the schema whose name is a path
    name; with no schema, the arguments themselves by name."""
    props = schema.get("properties") if isinstance(schema, Mapping) and \
        isinstance(schema.get("properties"), Mapping) else None
    names = list(props) if props else list(args)
    out = []
    for k in names:
        if str(k).lower() not in PATH_ARG_NAMES or not isinstance(args.get(k), str):
            continue
        if props:
            t = props[k].get("type") if isinstance(props.get(k), Mapping) else None
            if t not in (None, "string") and not (isinstance(t, list) and "string" in t):
                continue
        out.append(k)
    return out


def normalise_write_path(value: str) -> str:
    """The file name ``value`` names, written directly in the connection's root."""
    segs = [s for s in re.split(r"[/\\]+", value.strip()) if s not in ("", ".", "..")]
    return segs[-1].strip() if segs else value


def normalise_write_paths(args: Mapping[str, Any],
                          schema: Optional[Mapping[str, Any]]) -> tuple[dict, list[str]]:
    """``(args, notes)`` with every path argument normalised (`normalise_write_path`)."""
    out = dict(args)
    notes: list[str] = []
    for k in path_arguments(out, schema):
        before = out[k]
        after = normalise_write_path(before)
        if after and after != before:
            out[k] = after
            notes.append(f"'{k}' {before!r} was written as {after!r}: a write lands directly in "
                         f"its connection's root (no folder is assumed or created)")
    return out, notes


def _stem(name: str) -> str:
    m = _EXT.match(name)
    return m.group(1) if m else name


def path_derivations(args: Mapping[str, Any], schema: Optional[Mapping[str, Any]],
                     grounded: Any, *, depth: int = 2) -> list[dict]:
    """``concat`` derivations (as a planner would declare them) for every path argument's stem
    that ``grounded(token)`` does not already accept. Offered to `verify_derivations`, which
    decides; a stem that cannot be decomposed into grounded pieces gets none (and still refuses).
    """
    out: list[dict] = []

    def build(token: str, level: int, seps: Sequence[str]) -> Optional[list[dict]]:
        if grounded(token):
            return []
        if level <= 0:
            return None
        for sep in seps:
            pieces = token.split(sep)
            if len(pieces) < 2 or any(not p for p in pieces):
                continue
            subs: list[dict] = []
            for p in pieces:
                r = build(p, level - 1, [s for s in seps if s != sep])
                if r is None:
                    break
                subs.extend(r)
            else:
                return subs + [{"value": token, "rule": "concat", "inputs": pieces,
                                "args": {"sep": sep}}]
        return None

    for k in path_arguments(args, schema):
        stem = _stem(str(args[k]))
        for d in build(stem, depth, _SPLIT_SEPS) or []:
            if d not in out:
                out.append(d)
    return out


__all__ = ["PATH_ARG_NAMES", "path_arguments", "normalise_write_path", "normalise_write_paths",
           "path_derivations"]
