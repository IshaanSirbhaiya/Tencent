"""WORKAROUND: drop workflow tools matching a substring from <agent>.workflow.yaml (e2e dir only),
keeping the original beside it, then the caller regenerates the declaration (fix_declaration.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import e2e_dir  # noqa: E402
import yaml  # noqa: E402

agent, pats = sys.argv[1], sys.argv[2:]
p = e2e_dir() / "agents" / f"{agent}.workflow.yaml"
d = yaml.safe_load(p.read_text(encoding="utf-8"))
orig = p.with_name(p.name + ".orig")
if not orig.exists():
    orig.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
before = list(d["tools"])
d["tools"] = [t for t in before if not any(x in t for x in pats)]
p.write_text(yaml.safe_dump(d, sort_keys=False, allow_unicode=True), encoding="utf-8")
print("dropped", [t for t in before if t not in d["tools"]])
