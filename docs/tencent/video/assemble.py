"""Assemble the demo video from still cards and recorded scene clips.

    python docs/tencent/video/assemble.py [--voice voice.m4a] [--out submission/05_video/walnut_internal_brain_demo.mp4]

Segments are listed in ``SEGMENTS`` in script order (VIDEO_SCRIPT.md). A card is shown for its
``seconds``; a clip is fitted to its slot: trimmed if longer, its last frame held if shorter, so the
timing always matches the script and the voice-over can be recorded against the finished picture.
Missing inputs are reported and replaced by a labelled placeholder, never silently skipped.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
V = REPO / "submission" / "05_video"
CARDS, CLIPS = V / "cards", V / "clips"
W, H, FPS = 1920, 1080, 30

# (kind, file, seconds, label)  — seconds follow VIDEO_SCRIPT.md
SEGMENTS = [
    ("card", "01_title.png", 8, "1 Hello"),
    ("clip", "01_login.webm", 12, "1 Hello: sign-in"),
    ("card", "02_why.png", 35, "2 Why this matters"),
    ("clip", "03_ask_jdoe.webm", 45, "3 One question, three platforms"),
    ("clip", "04_map.webm", 35, "4 The map of what you can see"),
    ("clip", "05_compare.webm", 35, "5 Same question, two people"),
    ("clip", "06_restricted_vs_nonexistent.webm", 25, "6 Restricted looks like non-existent"),
    ("clip", "07_fintech.webm", 30, "7 The fintech part"),
    ("clip", "08_revocation_freshness.webm", 35, "8 Access changes, answers change"),
    ("clip", "09_audit.webm", 45, "9 The audit trail"),
    ("card", "09b_tamper.png", 15, "9 Tamper demo"),
    ("still", "../../02_workbuddy/workbuddy_compare.png", 18, "10 WorkBuddy: two people"),
    ("still", "../../02_workbuddy/workbuddy_compliance.png", 12, "10 WorkBuddy: compliance"),
    ("clip", "11_walnut_original.webm", 35, "11 What's underneath"),
    ("card", "12_architecture.png", 25, "12 How it fits together"),
    ("card", "13_numbers.png", 20, "13 The numbers"),
    ("card", "14_closing.png", 20, "14 Built with, thanks"),
]


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


FIT = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x070a14,"
       f"fps={FPS},format=yuv420p")


def segment(kind: str, src: Path, seconds: float, label: str, out: Path) -> str:
    if not src.exists():
        txt = f"MISSING: {label} ({src.name})".replace(":", r"\:").replace("'", "")
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x1a1f2e:s={W}x{H}:d={seconds}:r={FPS}",
             "-vf", f"drawtext=text='{txt}':fontcolor=white:fontsize=42:x=(w-tw)/2:y=(h-th)/2",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)])
        return f"MISSING {src}"
    if kind in ("card", "still"):
        zoom = "" if kind == "card" else (",zoompan=z='min(zoom+0.0006,1.06)':d=1:s="
                                          f"{W}x{H}:fps={FPS}")
        run(["ffmpeg", "-y", "-loop", "1", "-t", str(seconds), "-i", str(src),
             "-vf", FIT + zoom, "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-t", str(seconds),
             str(out)])
        return f"{kind:5} {seconds:>4}s  {src.name}"
    have = duration(src)
    pad = max(0.0, seconds - have)
    vf = FIT + (f",tpad=stop_mode=clone:stop_duration={pad:.2f}" if pad > 0 else "")
    run(["ffmpeg", "-y", "-i", str(src), "-vf", vf, "-an", "-c:v", "libx264", "-preset", "medium",
         "-crf", "18", "-t", str(seconds), str(out)])
    return f"clip  {seconds:>4}s  {src.name} (recorded {have:.1f}s{', held ' + format(pad, '.1f') + 's' if pad else ''})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", type=Path, help="voice-over audio to lay under the picture")
    ap.add_argument("--out", type=Path, default=V / "walnut_internal_brain_demo.mp4")
    args = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="ibvideo-"))
    parts, report, total = [], [], 0
    for i, (kind, name, secs, label) in enumerate(SEGMENTS):
        src = (CARDS if kind == "card" else CLIPS if kind == "clip" else CARDS) / name
        out = work / f"{i:02d}.mp4"
        report.append(f"{total // 60:02d}:{total % 60:02d}  {label:38}  " + segment(kind, src.resolve(), secs, label, out))
        parts.append(out)
        total += secs
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    silent = work / "silent.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(silent)])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.voice:
        run(["ffmpeg", "-y", "-i", str(silent), "-i", str(args.voice), "-c:v", "copy", "-c:a", "aac",
             "-b:a", "192k", "-shortest", str(args.out)])
    else:
        shutil.copyfile(silent, args.out)
    print("\n".join(report))
    print(f"total {total // 60}:{total % 60:02d} -> {args.out}")
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
