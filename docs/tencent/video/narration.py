"""Build the voice-over cue sheet and subtitles from VIDEO_SCRIPT.md and the video timeline.

    python docs/tencent/video/narration.py [--hy3]

Writes submission/05_video/narration_cues.md (what to say, when) and narration.srt (load it in any
video player as subtitles and read along while recording). Scene timings come from assemble.py's
SEGMENTS, so the narration always matches the picture. ``--hy3`` picks the "[if Hy3 is on]" lines.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SCRIPT = REPO / "docs" / "tencent" / "VIDEO_SCRIPT.md"
OUT = REPO / "submission" / "05_video"
sys.path.insert(0, str(HERE))
from assemble import SEGMENTS  # noqa: E402

WPS = 2.6                                   # relaxed speaking pace, words per second


def scenes_from_script(hy3: bool) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    cur = None
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^###\s+(\w+)\s+·", line)
        if m:
            cur = m.group(1)
            out[cur] = []
            continue
        if cur and line.startswith(">"):
            text = line[1:].strip()
            if "[if Hy3 is on]" in text and not hy3:
                continue
            if "[otherwise]" in text and hy3:
                continue
            text = re.sub(r"\*\*\[[^\]]+\]\*\*\s*", "", text).replace("*", "")
            if out[cur] and not text:
                continue
            if out[cur] and not re.search(r"[.!?\"”]$", out[cur][-1]):
                out[cur][-1] += " " + text                     # join wrapped lines
            elif text:
                out[cur].append(text)
    split = {}
    for k, v in out.items():                     # one subtitle per sentence: easy to read along
        split[k] = [x.strip() for para in v for x in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", para)
                    if x.strip()]
    return split


def timeline() -> dict[str, tuple[float, float]]:
    spans: dict[str, tuple[float, float]] = {}
    t = 0.0
    for _, _, secs, label in SEGMENTS:
        scene = label.split()[0]
        a, _ = spans.get(scene, (t, t))
        spans[scene] = (a, t + secs)
        t += secs
    return spans


def stamp(sec: float, srt: bool = True) -> str:
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return (f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}" if srt
            else f"{int(m)}:{int(s):02d}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hy3", action="store_true")
    args = ap.parse_args()
    lines, spans = scenes_from_script(args.hy3), timeline()
    srt, cues, n, warn = [], ["# Voice-over cue sheet", "",
                              "Play `walnut_internal_brain_demo.mp4` with `narration.srt` loaded as "
                              "subtitles and read each line as it appears. Relaxed pace; pauses are fine.",
                              ""], 1, []
    for scene, (a, b) in spans.items():
        said = lines.get(scene, [])
        if not said:
            continue
        words = [len(x.split()) for x in said]
        need = sum(words) / WPS
        room = b - a - 1.0
        if need > room:
            warn.append(f"scene {scene}: {need:.0f}s of speech for {room:.0f}s of picture")
        scale = min(1.0, room / need) if need else 1.0
        t = a + 0.5
        cues.append(f"## {stamp(a, False)}–{stamp(b, False)} · scene {scene}")
        cues.append("")
        for text, w in zip(said, words):
            d = max(1.8, w / WPS * scale)
            srt += [str(n), f"{stamp(t)} --> {stamp(min(t + d, b - 0.2))}", text, ""]
            cues.append(f"- **{stamp(t, False)}** {text}")
            n, t = n + 1, t + d
        cues.append("")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "narration.srt").write_text("\n".join(srt), encoding="utf-8")
    (OUT / "narration_cues.md").write_text("\n".join(cues), encoding="utf-8")
    print(f"{n - 1} cues -> {OUT / 'narration.srt'}")
    for w in warn:
        print("TIGHT:", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
