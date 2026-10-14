"""Build the Company A knowledge graph for the ORIGINAL Walnut screens (/graph, /brain, /evidence).

Two steps, both deterministic:

1. **Flatten** `fixtures/enterprise/world.json` (the Internal Brain's synthetic estate) into plain
   row files under `fixtures/enterprise/graph_rows/`, one `{"data": [...]}` page per shape. This is
   only reshaping: nested memberships become pair rows, Slack `ts` values become ISO dates, and
   issue keys mentioned in text become citation rows. No row is invented, and no body text is
   copied — labels are titles, names and (for chat threads) the first line of the first message,
   passed through the Internal Brain's own sensitive-value masking first.
2. **Load** those rows with the stock engine loader, `databridge_graph.estate_seed.load_pack_graph`,
   driven by `packs/enterprise/seed_map.yaml`, into `var/graph/enterprise.db`. Every node and edge
   cites the row cell it came from, exactly like the government and finance graphs.

    .venv/Scripts/python.exe scripts/build_enterprise_graph.py            # rows + graph
    .venv/Scripts/python.exe scripts/build_enterprise_graph.py --rows-only
    .venv/Scripts/python.exe scripts/build_enterprise_graph.py --out var/graph/other.db

The graph is permission-blind: it records who is GRANTED each container (`reads_space`) but shows
every item's title to whoever opens the screen. Use it as a local, admin-eye view of the estate.
Synthetic data only (`companya.example`).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent            # databridge/
for _pkg in sorted((REPO / "packages").iterdir()):
    if _pkg.is_dir():
        sys.path.insert(0, str(_pkg))
sys.path.insert(0, str(REPO / "apps" / "internal_brain"))

WORLD = REPO / "fixtures" / "enterprise" / "world.json"
ROWS_DIR = REPO / "fixtures" / "enterprise" / "graph_rows"
DEFAULT_OUT = REPO / "var" / "graph" / "enterprise.db"
PACK = "enterprise"

#: Company A runs on Singapore time; Slack `ts` values are epoch seconds and are shown in it.
SGT = dt.timezone(dt.timedelta(hours=8))
#: An issue key as it appears in prose ("DBMIG-14"). Only keys that exist in the estate are kept.
ISSUE_KEY = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
LABEL_MAX = 90


# ------------------------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------------------------
def _masker():
    """The Internal Brain's own masking (PAN, CVV, IBAN, NRIC) — applied to every label we copy."""
    try:
        from databridge_ib.sensitive import mask
    except Exception as exc:                                     # noqa: BLE001
        raise SystemExit(f"cannot import the Internal Brain's masking (databridge_ib.sensitive): {exc}")
    return lambda text: mask(text)[0]


def _one_line(text: str, limit: int = LABEL_MAX) -> str:
    line = " ".join((text or "").split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _ts_iso(ts: str) -> str:
    return dt.datetime.fromtimestamp(float(ts), tz=SGT).isoformat(timespec="seconds")


def _page(rows: list[dict], sort_keys: tuple[str, ...]) -> dict:
    rows = sorted(rows, key=lambda r: tuple(str(r.get(k) or "") for k in sort_keys))
    return {"data": rows}


def _space_id(platform: str, key: str) -> str:
    return f"{platform}-{key}"


# ------------------------------------------------------------------------------------------------
# step 1: flatten world.json into row files
# ------------------------------------------------------------------------------------------------
DEFAULT_PERSONA = "jdoe"


def _acl(world: dict):
    import importlib.util
    gen_path = REPO / "fixtures" / "enterprise" / "generate.py"
    spec = importlib.util.spec_from_file_location("_enterprise_world_generator", gen_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)                     # type: ignore[union-attr]
    return mod.Acl(world)


def _scope_to(world: dict, out: dict[str, list[dict]], persona: str) -> dict[str, list[dict]]:
    acl = _acl(world)
    keep: dict[str, list[dict]] = dict(out)
    visible_ids: set[str] = set()
    for kind in ("pages", "issues", "threads", "files"):
        rows = [r for r in out[kind] if persona in acl.readers(r["doc_id"])]
        keep[kind] = rows
        visible_ids |= {r["id"] for r in rows}
    spaces = {r["space"] for k in ("pages", "issues", "threads", "files") for r in keep[k]}
    keep["spaces"] = [r for r in out["spaces"] if r["id"] in spaces]
    keep["space_readers"] = [r for r in out["space_readers"] if r["space"] in spaces]
    keep["citations"] = [r for r in out["citations"]
                         if r["src"] in visible_ids and r["dst"] in visible_ids]
    return keep


def flatten(world: dict, persona: str | None = DEFAULT_PERSONA) -> dict[str, dict]:
    """Rows for the graph AS ``persona`` SEES IT: only items that person may open (the world
    generator's own permission rules), and only spaces where they can open at least one item.
    ``persona=None`` builds the unrestricted estate view (not used by the demo)."""
    mask = _masker()
    users = {u["id"]: u for u in world["directory"]["users"]}
    out: dict[str, list[dict]] = {k: [] for k in (
        "people", "teams", "team_members", "team_nesting", "spaces", "space_readers",
        "pages", "issues", "threads", "files", "citations")}

    # --- directory ---------------------------------------------------------------------------
    for u in world["directory"]["users"]:
        out["people"].append({
            "id": u["id"], "name": u["name"], "email": u["email"], "account_kind": u["kind"],
            "employment": u["employment"], "active": "active" if u["active"] else "deactivated",
            "roles": ", ".join(u.get("roles") or []) or None,
        })
    for g in world["directory"]["groups"]:
        out["teams"].append({"id": g["id"], "name": g["name"],
                             "direct_members": len(g.get("members") or [])})
        for member in g.get("members") or []:
            out["team_members"].append({"row": f"{g['id']}/{member}", "team": g["id"],
                                        "person": member})
        for child in g.get("member_groups") or []:
            out["team_nesting"].append({"row": f"{g['id']}/{child}", "parent": g["id"],
                                        "child": child})

    def readers(space: str, groups, people, via: str) -> None:
        for g in groups or []:
            out["space_readers"].append({"row": f"{space}/team/{g}", "space": space,
                                         "principal_type": "team", "principal": g, "via": via})
        for p in people or []:
            out["space_readers"].append({"row": f"{space}/colleague/{p}", "space": space,
                                         "principal_type": "colleague", "principal": p, "via": via})

    # --- containers --------------------------------------------------------------------------
    for s in world["confluence"]["spaces"]:
        sid = _space_id("confluence", s["key"])
        out["spaces"].append({"id": sid, "platform": "Confluence", "container": "Confluence space",
                              "key": s["key"], "name": s["name"],
                              "label": f"{s['name']} (Confluence space)",
                              "visibility": "anonymous" if s.get("anonymous") else "restricted",
                              "created": None})
        readers(sid, s["read"].get("groups"), s["read"].get("users"), "Confluence space read")
    for p in world["jira"]["projects"]:
        sid = _space_id("jira", p["key"])
        out["spaces"].append({"id": sid, "platform": "Jira", "container": "Jira project",
                              "key": p["key"], "name": p["name"],
                              "label": f"{p['name']} (Jira {p['key']})",
                              "visibility": "project roles", "created": None})
        browse = p.get("browse") or {}
        for role in browse.get("roles") or []:
            actors = (p.get("roles") or {}).get(role) or {}
            readers(sid, actors.get("groups"), actors.get("users"), f"Jira browse via role {role}")
        readers(sid, browse.get("groups"), browse.get("users"), "Jira browse")
    for c in world["slack"]["channels"]:
        sid = _space_id("slack", c["id"])
        out["spaces"].append({"id": sid, "platform": "Slack", "container": "Slack channel",
                              "key": c["id"], "name": f"#{c['name']}", "label": f"#{c['name']}",
                              "visibility": c["type"] + (" (archived)" if c.get("archived") else ""),
                              "created": c.get("created")})
        readers(sid, None, c.get("members"), f"Slack {c['type']} channel member")
    for d in world["drive"]["drives"]:
        sid = _space_id("drive", d["id"])
        out["spaces"].append({"id": sid, "platform": "Google Drive", "container": "Shared drive",
                              "key": d["id"], "name": d["name"],
                              "label": f"{d['name']} (shared drive)",
                              "visibility": "drive members", "created": None})
        members = d.get("members") or {}
        readers(sid, members.get("groups"), members.get("users"), "Shared drive member")

    # --- items -------------------------------------------------------------------------------
    issue_keys = {i["key"] for i in world["jira"]["issues"]}

    def cite(src_type: str, src: str, text: str, via: str) -> None:
        seen: set[str] = set()
        for key in ISSUE_KEY.findall(text or ""):
            if key in issue_keys and key not in seen and not (src_type == "issue" and key == src):
                seen.add(key)
                out["citations"].append({"row": f"{src_type}/{src}/{key}", "src_type": src_type,
                                         "src": src, "dst": key, "via": via})

    for pg in world["confluence"]["pages"]:
        out["pages"].append({
            "id": pg["id"], "doc_id": f"confluence:{pg['id']}", "title": mask(pg["title"]),
            "space": _space_id("confluence", pg["space"]), "author": pg.get("author"),
            "parent": pg.get("parent"), "status": pg.get("status"), "version": pg.get("version"),
            "created": pg.get("created"), "updated": pg.get("updated"),
            "page_restriction": "restricted" if pg.get("read_restriction") else "space default",
        })
        cite("page", pg["id"], pg.get("body", ""), "issue key mentioned in the page")

    levels = {lv["id"]: lv["name"] for lv in world["jira"]["security_levels"]}
    for iss in world["jira"]["issues"]:
        out["issues"].append({
            "id": iss["key"], "doc_id": f"jira:{iss['key']}",
            "title": mask(_one_line(f"{iss['key']}: {iss['summary']}")),
            "space": _space_id("jira", iss["project"]), "reporter": iss.get("reporter"),
            "assignee": iss.get("assignee"), "status": iss.get("status"),
            "security_level": levels.get(iss.get("security_level")) if iss.get("security_level")
            else None,
            "comments": len(iss.get("comments") or []),
            "created": iss.get("created"), "updated": iss.get("updated"),
        })
        text = " ".join([iss.get("description") or ""] +
                        [c.get("body") or "" for c in iss.get("comments") or []])
        cite("issue", iss["key"], text, "issue key mentioned in the issue")
        for linked in iss.get("links") or []:
            if linked in issue_keys and linked != iss["key"]:
                out["citations"].append({"row": f"issue/{iss['key']}/{linked}/link",
                                         "src_type": "issue", "src": iss["key"], "dst": linked,
                                         "via": "Jira issue link"})

    channels = {c["id"]: c["name"] for c in world["slack"]["channels"]}
    for th in world["slack"]["threads"]:
        msgs = th.get("messages") or []
        first = msgs[0] if msgs else {}
        tid = f"{th['channel']}-{th['ts']}"
        out["threads"].append({
            "id": tid, "doc_id": f"slack:{th['channel']}:{th['ts']}",
            "title": f"#{channels.get(th['channel'], th['channel'])}: "
                     f"{mask(_one_line(first.get('text', ''), 70))}",
            "space": _space_id("slack", th["channel"]), "starter": first.get("user"),
            "messages": len(msgs),
            "participants": len({m.get("user") for m in msgs if m.get("user")}),
            "created": _ts_iso(th["ts"]),
            "last_reply": _ts_iso(msgs[-1]["ts"]) if msgs else None,
        })
        cite("thread", tid, " ".join(m.get("text") or "" for m in msgs),
             "issue key mentioned in the thread")

    folders = {f["id"]: f["name"] for f in world["drive"]["folders"]}
    for f in world["drive"]["files"]:
        mime = (f.get("mimeType") or "").rsplit(".", 1)[-1].rsplit("/", 1)[-1]
        out["files"].append({
            "id": f["id"], "doc_id": f"drive:{f['id']}", "title": mask(f["name"]),
            "space": _space_id("drive", f["drive"]), "folder": folders.get(f.get("folder")),
            "file_type": mime or None, "version": f.get("version"),
            "own_permissions": len(f.get("permissions") or []),
            "created": f.get("created"), "modified": f.get("modified"),
        })
        cite("file", f["id"], f.get("content", ""), "issue key mentioned in the file")

    if persona:
        out = _scope_to(world, out, persona)
    # Dangling people (an author who is not in the directory) would become dangling edges; the
    # loader counts and drops those itself, so nothing is filtered here.
    del users
    sort = {
        "people": ("id",), "teams": ("id",), "team_members": ("row",), "team_nesting": ("row",),
        "spaces": ("id",), "space_readers": ("row",), "pages": ("id",), "issues": ("id",),
        "threads": ("id",), "files": ("id",), "citations": ("row",),
    }
    return {name: _page(rows, sort[name]) for name, rows in out.items()}


def write_rows(pages: dict[str, dict]) -> None:
    ROWS_DIR.mkdir(parents=True, exist_ok=True)
    for name, page in pages.items():
        text = json.dumps(page, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
        (ROWS_DIR / f"{name}.json").write_bytes(text.encode("utf-8"))   # LF, byte-stable
    print(f"rows -> {ROWS_DIR}  " + ", ".join(f"{k}={len(v['data'])}" for k, v in pages.items()))


# ------------------------------------------------------------------------------------------------
# step 2: build the graph with the stock loader
# ------------------------------------------------------------------------------------------------
def build_graph(out: Path) -> dict:
    from databridge_graph.estate_seed import load_pack_graph
    from databridge_graph.packs import deployment
    from databridge_graph.store import GraphStore

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".building.db")
    for p in (tmp, Path(str(tmp) + "-wal"), Path(str(tmp) + "-shm")):
        if p.exists():
            p.unlink()
    with deployment(PACK):
        store = GraphStore(str(tmp))
        result = load_pack_graph(store, PACK)
        counts = store.counts()
        close = getattr(store, "close", None)
        if callable(close):
            close()
    for p in (out, Path(str(out) + "-wal"), Path(str(out) + "-shm")):
        if p.exists():
            p.unlink()
    tmp.replace(out)
    return {"counts": counts, **result}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="graph SQLite file to (re)build")
    ap.add_argument("--rows-only", action="store_true", help="only rewrite graph_rows/*.json")
    ap.add_argument("--as", dest="persona", default=DEFAULT_PERSONA,
                    help="build the graph as this person sees it (default jdoe); 'all' = unrestricted")
    args = ap.parse_args()

    t0 = time.perf_counter()
    world = json.loads(WORLD.read_text(encoding="utf-8"))
    persona = None if args.persona == "all" else args.persona
    write_rows(flatten(world, persona))
    print(f"view: {persona or 'unrestricted'}")
    if args.rows_only:
        return 0
    out = args.out if args.out.is_absolute() else (REPO / args.out)
    result = build_graph(out)
    report = result.pop("report")
    print("graph ->", out)
    print("counts:", result["counts"])
    print("nodes_by_kind:", report["nodes_by_kind"])
    print("edges_by_kind:", report["edges_by_kind"])
    noisy = {k: v for k, v in report.items()
             if k.startswith(("skipped", "dangling", "slug")) and v}
    if noisy:
        print("skipped / dropped (counted by the loader):", noisy)
    print(f"elapsed: {time.perf_counter() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
