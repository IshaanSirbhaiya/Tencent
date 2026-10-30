"""Read-only pre-merge check for a teammate's branch (docs/finals/MERGE_PLAN.md §2).

    .venv/Scripts/python scripts/merge_precheck.py <branch>                       # from databridge/
    .venv/Scripts/python scripts/merge_precheck.py <branch> --base origin/final_v1.3
    .venv/Scripts/python scripts/merge_precheck.py final_v1.2 --base final_v1.1   # the self-test
    .venv/Scripts/python scripts/merge_precheck.py <branch> --names-file names.txt --add-name "Acme Bank"

What it prints, for the commits on <branch> that are not on --base (``git diff <merge-base>..<branch>``):

1. the branch base: the merge-base, ahead/behind counts, and whether the verified pointer
   (``--verified``, default ``origin/final_v1.3``) is inside the branch;
2. the diff stat, grouped by area (IB backend, web, fixtures, engine, ...);
3. secret-pattern hits on ADDED lines (values masked), plus any added secret-shaped file
   (``.env*``, ``*.pem``, ``*.key``, ``*.tok`` ...; their content is never read or printed) and any
   removed ``.gitignore`` line that protected one;
4. real-company-name hits on added lines (rule 7, synthetic data only). The list is short and
   configurable: ``--names-file`` (one name per line, ``#`` comments) and/or ``--add-name``, or
   ``MERGE_PRECHECK_NAMES`` (comma-separated). ``--names-file`` replaces the default list;
5. touches under ``databridge/packages/**`` (the Walnut engine is reused by import, never modified);
6. a per-domain-branching heuristic (rule 6) and feature hints (client-side keys, code execution, a
   second speech stack, new env flags, dependency changes). Heuristics: read each hit by hand;
7. the conflict-hotspot overlap: files the branch changed that our side also changed since
   ``--ours-since`` (default ``8fff33c``) on ``--ours`` (default ``HEAD``), plus our uncommitted work
   when ``--ours`` is ``HEAD``, with the named hot spots marked.

Read-only and silent: only ``git diff/log/rev-parse/merge-base/rev-list/grep/status`` with
``--no-optional-locks``; no fetch, no prompts, no network, no writes. It never opens a working-tree
file (in particular never ``databridge/.env.ib.local``).

Exit code: 0 no blocking finding; 1 a blocking finding (secret, secret-shaped file, real company
name, packages/** touch); 2 a usage or git error.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

REPO = Path(__file__).resolve().parents[2]          # the git repo root (Tencent/)
DB = "databridge/"

# --- configurable lists ---------------------------------------------------------------------------

# Rule 7: a SHORT, generic default of well-known real companies that must never appear in the
# synthetic world or the UI. Platform names the product legitimately integrates with (Slack,
# Confluence, Jira, Google Drive), the event and sponsor (Tencent, Aspire) and the model vendors are
# deliberately NOT here. Extend per merge with --add-name / --names-file / MERGE_PRECHECK_NAMES.
DEFAULT_NAMES = (
    "DBS", "OCBC", "UOB", "HSBC", "Standard Chartered", "Citibank", "JPMorgan", "J.P. Morgan",
    "Goldman Sachs", "Morgan Stanley", "Barclays", "Maybank", "Grab", "Shopee", "Sea Limited",
    "Revolut", "Stripe", "PayPal", "Mastercard", "Ant Group", "Alipay", "Airwallex", "Nium",
    "Temasek", "GIC", "Singtel", "Deloitte", "KPMG", "PwC", "Ernst & Young", "Accenture",
)

# Named hot spots (MERGE_PLAN §4): files our side changed heavily or that every feature touches.
HOTSPOTS = (
    "databridge/apps/internal_brain_web/lib/types.ts",
    "databridge/apps/internal_brain_web/lib/api.ts",
    "databridge/apps/internal_brain_web/app/api/ib/[...path]/route.ts",
    "databridge/apps/internal_brain_web/components/user/AnswerCard.tsx",
    "databridge/apps/internal_brain_web/components/user/Composer.tsx",
    "databridge/apps/internal_brain_web/components/user/useDictation.ts",
    "databridge/apps/internal_brain_web/components/user/DeliverSheet.tsx",
    "databridge/apps/internal_brain_web/components/presenter/script.ts",
    "databridge/apps/internal_brain_web/components/presenter/controller.ts",
    "databridge/apps/internal_brain_web/components/shell/Providers.tsx",
    "databridge/apps/internal_brain_web/lib/mock/server.ts",
    "databridge/apps/internal_brain_web/lib/personas.ts",
    "databridge/apps/internal_brain_web/package.json",
    "databridge/apps/internal_brain_web/package-lock.json",
    "databridge/apps/internal_brain_web/tsconfig.json",
    "databridge/apps/internal_brain/databridge_ib/app.py",
    "databridge/apps/internal_brain/databridge_ib/config.py",
    "databridge/apps/internal_brain/databridge_ib/models.py",
    "databridge/apps/internal_brain/databridge_ib/pipeline.py",
    "databridge/apps/internal_brain/databridge_ib/actions.py",
    "databridge/requirements-lock.txt",
    "databridge/scripts/demo_up.sh",
    "databridge/scripts/demo_day.ps1",
    "databridge/scripts/demo_preflight.py",
)
HOTSPOT_PREFIXES = (
    "databridge/apps/internal_brain_web/lib/i18n/",
    "databridge/apps/internal_brain/databridge_ib/deliverables/",
)

# Area grouping for the diff stat: first match wins.
AREAS = (
    ("Walnut engine (MUST be empty)", lambda p: p.startswith(DB + "packages/")),
    ("Dependencies", lambda p: p.endswith(("requirements-lock.txt", "package.json", "package-lock.json",
                                            "pyproject.toml"))),
    ("IB deliverables", lambda p: p.startswith(DB + "apps/internal_brain/databridge_ib/deliverables/")),
    ("IB backend", lambda p: p.startswith(DB + "apps/internal_brain/databridge_ib/")),
    ("IB tests", lambda p: p.startswith(DB + "apps/internal_brain/tests/")),
    ("Web e2e", lambda p: p.startswith(DB + "apps/internal_brain_web/tests/")),
    ("Web i18n", lambda p: p.startswith(DB + "apps/internal_brain_web/lib/i18n/")),
    ("Web proxy route", lambda p: p.startswith(DB + "apps/internal_brain_web/app/api/")),
    ("Web mock", lambda p: p.startswith(DB + "apps/internal_brain_web/lib/mock/")),
    ("Web app", lambda p: p.startswith(DB + "apps/internal_brain_web/")),
    ("Mock platforms", lambda p: p.startswith(DB + "apps/mock_platforms/")),
    ("Synthetic world (fixtures)", lambda p: p.startswith(DB + "fixtures/")),
    ("Eval", lambda p: p.startswith(DB + "evals/")),
    ("Demo kit / scripts", lambda p: p.startswith((DB + "scripts/", "deploy/")) or p.endswith(".cmd")),
    ("Other databridge", lambda p: p.startswith(DB)),
    ("Docs", lambda p: p.startswith("docs/") or p.endswith(".md")),
    ("Submission", lambda p: p.startswith("submission/")),
    ("Other", lambda p: True),
)

# --- patterns -------------------------------------------------------------------------------------

SECRET_PATTERNS = (
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Tencent Cloud SecretId", re.compile(r"\bAKID[0-9A-Za-z]{13,}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("bearer literal", re.compile(r"[Bb]earer\s+[A-Za-z0-9._~+/=-]{24,}")),
    ("key/secret/token assignment", re.compile(
        r"(?i)\b[\w.-]*(?:api[_-]?key|secret|token|passw(?:or)?d|passcode|access[_-]?key)[\w.-]*['\"]?\s*[:=]\s*"
        r"['\"]([^'\"\s]{12,})['\"]")),
)
# Values that are visibly placeholders or dev defaults: reported, but not blocking.
PLACEHOLDER = re.compile(r"(?i)(dev|test|example|sample|placeholder|dummy|fake|not[-_ ]?real|mock|changeme|x{4,}|\*{3,}|<[^>]+>|\$\{|"
                         r"process\.env|os\.environ|getenv|_env\()")
SECRET_FILE = re.compile(r"(?i)(^|/)(\.env(\..*)?|.*\.(pem|key|p12|pfx|tok|keystore|jks)|id_rsa.*|id_ed25519.*|"
                         r"credentials\.json|service[-_]account.*\.json|\.npmrc|\.pypirc)$")
SECRET_FILE_OK = re.compile(r"(?i)(^|/)\.env\.example$")
GITIGNORE_GUARD = re.compile(r"(?i)\.env|\.tok|\.pem|\.key|secret|credential")

DOMAIN_WORDS = ("finance", "fintech", "healthcare", "health", "hr", "government", "compliance",
                "fincrime", "aml", "kyc", "banking", "bank", "insurance", "legal", "payroll", "treasury")
_DW = "|".join(DOMAIN_WORDS)
DOMAIN_BRANCH = re.compile(
    rf"""(?ix)(
        (==|!=|===|!==|\bin\b|\bcase\b)\s*\(?\s*['"]({_DW})['"]
      | ['"]({_DW})['"]\s*(==|!=|===|!==)
      | \b(domain|vertical|industry|sector|department)\s*(==|!=|===|!==)
    )""")
# Platform semantics belong in connectors/ (rule 6); elsewhere a platform test is shown for review only.
PLATFORM_BRANCH = re.compile(r"""\b(platform|source)\s*(==|===|!=|!==)\s*['"](slack|confluence|jira|drive)['"]""")
DOMAIN_SCOPE = (DB + "apps/internal_brain/databridge_ib/", DB + "apps/internal_brain_web/lib/",
                DB + "apps/internal_brain_web/components/", DB + "apps/internal_brain_web/app/")
DOMAIN_EXEMPT = (DB + "apps/internal_brain/databridge_ib/connectors/",)   # platform semantics live here

FEATURE_HINTS = (
    ("key exposed to the browser (NEXT_PUBLIC_*KEY/TOKEN/SECRET)",
     re.compile(r"\bNEXT_PUBLIC_[A-Z0-9_]*(KEY|TOKEN|SECRET)[A-Z0-9_]*\b"), (".ts", ".tsx", ".js", ".mjs", ".sh", ".ps1", ".cmd")),
    ("code execution (eval / new Function / dangerouslySetInnerHTML)",
     re.compile(r"\beval\s*\(|\bnew\s+Function\s*\(|dangerouslySetInnerHTML"), (".ts", ".tsx", ".js", ".mjs")),
    ("code execution (Python exec/eval/compile)",
     re.compile(r"(?<![.\w])(exec|eval|compile)\s*\("), (".py",)),
    ("a second speech stack (reuse components/user/useDictation.ts)",
     re.compile(r"SpeechRecognition|getUserMedia|MediaRecorder|AudioContext"), (".ts", ".tsx", ".js")),
    ("direct external fetch (should go through /api/ib or the server)",
     re.compile(r"""fetch\(\s*['"`]https?://(?!127\.0\.0\.1|localhost)"""), (".ts", ".tsx", ".js")),
    ("reads the local key file (keys stay server-side, loaded by the existing kit)", re.compile(r"env\.ib\.local"),
     (".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".sh", ".ps1", ".cmd")),
)
ENV_NAME = re.compile(r"\b(?:IB|NEXT_PUBLIC|WALNUT|MOCK|TOKENHUB)_[A-Z0-9_]{2,}\b")
ENV_NAME_POSIX = "(IB|NEXT_PUBLIC|WALNUT|MOCK|TOKENHUB)_[A-Z0-9_]{2,}"      # the same, for git grep -E

# --- git ------------------------------------------------------------------------------------------

_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat", PAGER="cat", LC_ALL="C.UTF-8",
            GIT_OPTIONAL_LOCKS="0")
_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class GitError(RuntimeError):
    pass


def git(*args: str, ok: tuple[int, ...] = (0,)) -> str:
    proc = subprocess.run(["git", "--no-pager", "--no-optional-locks", "-c", "core.quotepath=off",
                           "-C", str(REPO), *args], capture_output=True, env=_ENV,
                          stdin=subprocess.DEVNULL, creationflags=_FLAGS)
    if proc.returncode not in ok:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout.decode("utf-8", "replace")


def resolve(ref: str) -> Optional[str]:
    for cand in (ref, f"origin/{ref}"):
        out = git("rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}", ok=(0, 1)).strip()
        if out:
            return cand
    return None


def is_ancestor(a: str, b: str) -> bool:
    return subprocess.run(["git", "-C", str(REPO), "merge-base", "--is-ancestor", a, b],
                          capture_output=True, env=_ENV, stdin=subprocess.DEVNULL,
                          creationflags=_FLAGS).returncode == 0


def numstat(*range_args: str) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for line in git("diff", "--numstat", "-M", *range_args).splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add, rem, path = parts[0], parts[1], parts[-1]
        if " => " in path:                       # rename shown inline: keep the new path
            path = re.sub(r"\{[^{}]* => ([^{}]*)\}", r"\1", path).replace("//", "/")
            path = path.split(" => ")[-1]
        out[path] = (int(add) if add.isdigit() else 0, int(rem) if rem.isdigit() else 0)
    return out


@dataclass
class Line:
    path: str
    no: int
    text: str


@dataclass
class Diff:
    added: list[Line] = field(default_factory=list)
    removed: list[Line] = field(default_factory=list)


_SKIP_CONTENT = [":(exclude,glob)**/.env*", ":(exclude,glob).env*", ":(exclude,glob)**/*.tok",
                 ":(exclude,glob)**/*.pem", ":(exclude,glob)**/*.key", ":(exclude,glob)**/package-lock.json"]


def added_lines(mb: str, tip: str) -> Diff:
    """Added/removed lines of mb..tip; secret-shaped files are excluded, so their content is never read."""
    d, path, new_no, old_no = Diff(), "", 0, 0
    raw = git("diff", "-U0", "--no-color", "--no-ext-diff", "-M", mb, tip, "--", ".", *_SKIP_CONTENT)
    for line in raw.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("--- "):
            continue
        elif line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if m:
                old_no, new_no = int(m.group(1)), int(m.group(2))
        elif line.startswith("+") and path:
            d.added.append(Line(path, new_no, line[1:]))
            new_no += 1
        elif line.startswith("-") and path:
            d.removed.append(Line(path, old_no, line[1:]))
            old_no += 1
    return d


# --- checks ---------------------------------------------------------------------------------------

def mask(value: str) -> str:
    v = value.strip()
    return (v[:4] + "…" + f"[{len(v)} chars]") if len(v) > 6 else "***"


def clip(text: str, n: int = 110) -> str:
    t = text.strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def area_of(path: str) -> str:
    return next(name for name, test in AREAS if test(path))


def load_names(args: argparse.Namespace) -> list[str]:
    names: list[str] = list(DEFAULT_NAMES)
    if args.names_file:
        names = []
        for raw in Path(args.names_file).read_text(encoding="utf-8").splitlines():
            raw = raw.split("#", 1)[0].strip()
            if raw:
                names.append(raw)
    env = os.environ.get("MERGE_PRECHECK_NAMES", "")
    names += [n.strip() for n in env.split(",") if n.strip()]
    names += [n.strip() for n in args.add_name if n.strip()]
    return sorted(set(names), key=str.lower)


def name_regex(name: str) -> re.Pattern:
    # Case-sensitive, as written or in capitals ("Grab" and "GRAB", never "grab the row"; "DBS", never
    # "dbs/"), so common words that are also company names do not drown the real hits.
    alts = {re.escape(v).replace(r"\ ", r"\s+") for v in (name, name.upper())}
    return re.compile(r"(?<![A-Za-z0-9_])(?:" + "|".join(sorted(alts)) + r")(?![A-Za-z0-9_])")


def section(title: str) -> None:
    print(f"\n== {title} " + "=" * max(0, 92 - len(title)))


def show(hits: list[str], limit: int) -> None:
    for h in hits[:limit]:
        print("  " + h)
    if len(hits) > limit:
        print(f"  ... and {len(hits) - limit} more (raise --max-hits)")


# --- main -----------------------------------------------------------------------------------------

def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only pre-merge check (docs/finals/MERGE_PLAN.md).")
    ap.add_argument("branch", help="the branch to check (local, or origin/<name>)")
    ap.add_argument("--base", default="origin/final_v1.3",
                    help="what it will be merged into (default origin/final_v1.3; use the then-latest final_v1.x)")
    ap.add_argument("--verified", default="origin/final_v1.3", help="the verified pointer the branch should contain")
    ap.add_argument("--ours", default="HEAD", help="our side for the hot-spot overlap (default HEAD)")
    ap.add_argument("--ours-since", default="8fff33c", help="our heavy changes are counted from here (default 8fff33c)")
    ap.add_argument("--no-worktree", action="store_true", help="ignore our uncommitted work in the overlap")
    ap.add_argument("--names-file", help="replace the default company-name list (one per line, # comments)")
    ap.add_argument("--add-name", action="append", default=[], help="add a company name (repeatable)")
    ap.add_argument("--max-hits", type=int, default=40, help="lines shown per section (default 40)")
    args = ap.parse_args(list(argv) if argv is not None else None)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        return run(args)
    except GitError as e:
        print(f"merge_precheck: {e}", file=sys.stderr)
        return 2


def run(args: argparse.Namespace) -> int:
    tip, base = resolve(args.branch), resolve(args.base)
    if not tip or not base:
        missing = args.branch if not tip else args.base
        print(f"merge_precheck: cannot resolve '{missing}' (fetch it first: see MERGE_PLAN §2.1)", file=sys.stderr)
        return 2
    mb = git("merge-base", base, tip).strip()
    if not mb:
        print(f"merge_precheck: '{tip}' and '{base}' share no history", file=sys.stderr)
        return 2
    blocking: list[str] = []
    lim = args.max_hits

    print(f"merge_precheck  branch={tip}  base={base}  repo={REPO}")
    print("Read-only: git diff/log/rev-parse/merge-base/grep/status only. No fetch, no writes.")

    # 1. base -------------------------------------------------------------------------------------
    section("1. Branch base")
    ahead, behind = git("rev-list", "--left-right", "--count", f"{base}...{tip}").split()
    print(f"  merge-base      {git('log', '-1', '--format=%h %ci %s', mb).strip()}")
    print(f"  branch tip      {git('log', '-1', '--format=%h %ci %an: %s', tip).strip()}")
    print(f"  ahead of base   {behind} commit(s) to merge;  base has {ahead} commit(s) the branch lacks")
    ver = resolve(args.verified)
    if ver:
        inside = is_ancestor(ver, tip)
        print(f"  {args.verified} inside the branch: {'yes' if inside else 'NO'}"
              + ("" if inside else "  -> branched from an older final_v1.x: expect more conflicts; ask for a rebase"
                                  " or merge carefully (MERGE_PLAN §2.2)"))
    else:
        print(f"  {args.verified}: not found locally (fetch origin)")
    on_origin = bool(git("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{args.branch}", ok=(0, 1)).strip())
    print(f"  on origin       {'yes' if on_origin or tip.startswith('origin/') else 'no (local only)'}")
    merges = git("rev-list", "--merges", "--count", f"{mb}..{tip}").strip()
    print(f"  merge commits on the branch: {merges}")

    # 2. diff stat ----------------------------------------------------------------------------------
    section("2. Diff summary by area (merge-base..branch)")
    theirs = numstat(mb, tip)
    status = {}
    for line in git("diff", "--name-status", "-M", mb, tip).splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            status[parts[-1]] = parts[0][0]
    by_area: dict[str, list[str]] = defaultdict(list)
    for p in theirs:
        by_area[area_of(p)].append(p)
    tot_a = sum(a for a, _ in theirs.values())
    tot_r = sum(r for _, r in theirs.values())
    print(f"  {len(theirs)} files, +{tot_a} -{tot_r}")
    for name, _ in AREAS:
        files = by_area.get(name)
        if not files:
            continue
        a = sum(theirs[f][0] for f in files)
        r = sum(theirs[f][1] for f in files)
        print(f"\n  {name}: {len(files)} files, +{a} -{r}")
        for f in sorted(files, key=lambda f: -(theirs[f][0] + theirs[f][1]))[:lim]:
            print(f"    {status.get(f, '?')} {f}  (+{theirs[f][0]} -{theirs[f][1]})")
        if len(files) > lim:
            print(f"    ... and {len(files) - lim} more")
    world = any(p.startswith(DB + "fixtures/enterprise/") for p in theirs)
    backend = any(p.startswith(DB + "apps/internal_brain/databridge_ib/") for p in theirs)
    print(f"\n  backend changed: {'yes' if backend else 'no'};  world changed: {'yes' if world else 'no'}"
          + ("  -> the leak eval MUST be re-run (MERGE_PLAN §5.6)" if backend or world else ""))

    diff = added_lines(mb, tip)

    # 3. secrets ------------------------------------------------------------------------------------
    section("3. Secrets (added lines; values masked)")
    hard: list[str] = []
    soft: list[str] = []
    for p, st in status.items():
        if SECRET_FILE.search(p) and not SECRET_FILE_OK.search(p) and st != "D":
            hard.append(f"FILE {p}  (secret-shaped file committed; content not read)")
    for ln in diff.removed:
        if ln.path.endswith(".gitignore") and GITIGNORE_GUARD.search(ln.text):
            hard.append(f"{ln.path}: removed ignore rule '{clip(ln.text, 60)}'")
    for ln in diff.added:
        for label, rx in SECRET_PATTERNS:
            m = rx.search(ln.text)
            if not m:
                continue
            val = m.group(m.lastindex) if m.lastindex else m.group(0)
            entry = f"{ln.path}:{ln.no}  {label}  value={mask(val)}"
            (soft if PLACEHOLDER.search(ln.text) else hard).append(entry)
            break
    if hard:
        blocking.append(f"{len(hard)} secret hit(s)")
    print(f"  blocking: {len(hard)}")
    show(hard, lim)
    print(f"  placeholder / dev-default / env lookup (check by eye, not blocking): {len(soft)}")
    show(soft, lim)

    # 4. company names ------------------------------------------------------------------------------
    names = load_names(args)
    section(f"4. Real company names (rule 7; {len(names)} names configured)")
    regs = [(n, name_regex(n)) for n in names]
    name_hits: list[str] = []
    for ln in diff.added:
        for n, rx in regs:
            if rx.search(ln.text):
                name_hits.append(f"{ln.path}:{ln.no}  [{n}]  {clip(ln.text)}")
    for p in theirs:
        for n, rx in regs:
            if rx.search(p):
                name_hits.append(f"{p}  [{n}] in the file name")
    if name_hits:
        blocking.append(f"{len(name_hits)} company-name hit(s)")
    print(f"  hits: {len(name_hits)}  (a hit in a doc that names a sponsor or a real platform may be fine; "
          "any hit in fixtures/, mock data or UI copy is not)")
    show(name_hits, lim)

    # 5. packages/** -------------------------------------------------------------------------------
    section("5. Walnut engine: databridge/packages/** (must be empty)")
    pk = sorted(p for p in theirs if p.startswith(DB + "packages/") or p.startswith("packages/"))
    if pk:
        blocking.append(f"{len(pk)} packages/** file(s)")
    print(f"  files touched: {len(pk)}")
    show([f"{status.get(p, '?')} {p}" for p in pk], lim)

    # 6. heuristics ---------------------------------------------------------------------------------
    section("6. Per-domain branching (rule 6; heuristic, read each hit)")
    scoped = [ln for ln in diff.added
              if ln.path.startswith(DOMAIN_SCOPE) and not ln.path.startswith(DOMAIN_EXEMPT)
              and ln.path.endswith((".py", ".ts", ".tsx")) and "/tests/" not in ln.path]
    dom = [f"{ln.path}:{ln.no}  {clip(ln.text)}" for ln in scoped if DOMAIN_BRANCH.search(ln.text)]
    plat = [f"{ln.path}:{ln.no}  {clip(ln.text)}" for ln in scoped if PLATFORM_BRANCH.search(ln.text)]
    print(f"  domain branching: {len(dom)}  (also run packages/graph/tests/test_no_domain_branching.py, "
          "MERGE_PLAN §5.1)")
    show(dom, lim)
    print(f"  platform tests outside connectors/ (display or simulator code is usually fine; "
          f"retrieval or gate logic is not): {len(plat)}")
    show(plat, min(lim, 10))

    section("6b. Feature-rule hints (heuristic)")
    for label, rx, exts in FEATURE_HINTS:
        hits = [f"{ln.path}:{ln.no}  {clip(ln.text)}" for ln in diff.added
                if (exts is None or ln.path.endswith(exts)) and "/tests/" not in ln.path
                and not ln.path.endswith("useDictation.ts") and rx.search(ln.text)]
        if hits:
            print(f"  {label}: {len(hits)}")
            show(hits, min(lim, 10))
    new_env = sorted({m for ln in diff.added for m in ENV_NAME.findall(ln.text)}
                     - set(ENV_NAME.findall(git("grep", "-h", "-o", "-I", "-E", ENV_NAME_POSIX,
                                                base, "--", "databridge", ok=(0, 1)))))
    print(f"  new env names (each must default OFF; document it in the run sheet): {', '.join(new_env) or 'none'}")
    deps = [f"{ln.path}:{ln.no}  {clip(ln.text, 80)}" for ln in diff.added
            if ln.path.endswith(("requirements-lock.txt", "package.json", "pyproject.toml"))]
    print(f"  dependency lines added (need a Windows fresh-install check): {len(deps)}")
    show(deps, min(lim, 15))

    # 7. hotspot overlap ----------------------------------------------------------------------------
    section("7. Conflict hot spots: overlap with our changes")
    ours_ref = resolve(args.ours) or args.ours
    ours = numstat(f"{args.ours_since}..{ours_ref}")
    wt_note = ""
    if args.ours == "HEAD" and not args.no_worktree:
        for p, (a, r) in numstat("HEAD").items():
            oa, orr = ours.get(p, (0, 0))
            ours[p] = (oa + a, orr + r)
        untracked = [l[3:] for l in git("status", "--porcelain", "-uall").splitlines() if l.startswith("?? ")]
        for p in untracked:
            ours.setdefault(p, (0, 0))
        wt_note = f" + uncommitted work ({len(untracked)} untracked)"
    print(f"  our side: {args.ours_since}..{args.ours}{wt_note}: {len(ours)} files")
    overlap = sorted(set(theirs) & set(ours),
                     key=lambda p: -(sum(theirs[p]) + sum(ours[p])))
    hot = set(HOTSPOTS)
    is_hot = lambda p: p in hot or p.startswith(HOTSPOT_PREFIXES)   # noqa: E731
    rows = [f"{'HOT ' if is_hot(p) else '    '}{p}  theirs +{theirs[p][0]} -{theirs[p][1]} | "
            f"ours +{ours[p][0]} -{ours[p][1]}" for p in overlap]
    print(f"  files both sides changed: {len(overlap)}  (HOT = a named hot spot, MERGE_PLAN §4)")
    show(rows, lim)
    lone = sorted(p for p in theirs if is_hot(p) and p not in ours)
    if lone:
        print(f"  named hot spots only the branch touched (review the shape, low conflict risk): {len(lone)}")
        show(lone, lim)

    # verdict ---------------------------------------------------------------------------------------
    section("Verdict")
    if blocking:
        print("  BLOCKING: " + "; ".join(blocking) + ".  Do not merge until each is fixed on the branch.")
        return 1
    print("  No blocking finding. Read sections 6/6b and 7 by hand, then follow MERGE_PLAN §3.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
