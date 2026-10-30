"""The extractive answer is the same in every process (eval P2 control, 30 Oct 2026).

``narrate.extractive_answer`` narrows to the question's most specific anchor. The anchors are a set,
so with two anchors matched by the same number of sentences the choice used to follow set iteration
order, which changes with PYTHONHASHSEED: two processes over the same world could answer the same
question differently (the eval's A-vs-A control rebuild flagged it once the everyday-company layer
added near-ties). The tie is now broken by the anchor itself."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]          # databridge/

SCRIPT = r"""
from databridge_ib.narrate import Passage, extractive_answer
def mk(tag, text, platform):
    return Passage(tag=tag, doc_id=f"{platform}:{tag}", platform=platform, container_name="c",
                   title="t", version="1", updated="2026-10-01", text=text)
ps = [mk("C1", "Alpha Bravo note: the Alpha budget is reviewed each quarter by the team.", "confluence"),
      mk("C2", "Alpha Bravo memo: the Bravo budget is reviewed each quarter by the team.", "jira"),
      mk("C3", "The Alpha plan covers the budget for the team this quarter.", "slack"),
      mk("C4", "The Bravo plan covers the budget for the team this quarter.", "drive"),
      mk("C5", "Unrelated budget notes for the team this quarter only.", "slack")]
print(extractive_answer(ps, "What is the budget for Alpha and Bravo this quarter?"))
"""


def _run(seed: str) -> str:
    env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=os.pathsep.join(
        str(ROOT / p) for p in ("apps/internal_brain", "packages/core", "packages/harness",
                                "packages/adapter", "packages/retrieval")))
    out = subprocess.run([sys.executable, "-c", SCRIPT], env=env, capture_output=True, text=True,
                         timeout=120, cwd=str(ROOT))
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout


def test_extractive_answer_does_not_depend_on_the_hash_seed():
    answers = {_run(str(seed)) for seed in range(8)}
    assert len(answers) == 1, answers
    assert next(iter(answers)).strip()
