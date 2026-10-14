"""Assemble the demo video from still cards and recorded scene clips.

    python docs/tencent/video/assemble.py [--voice voice.m4a] [--out submission/05_video/walnut_internal_brain_demo.mp4]

Segments are listed in ``SEGMENTS`` in script order (VIDEO_SCRIPT.md), each with a target length
sized to its narration (about 2.6 spoken words a second, plus room to breathe). How the edit keeps it
feeling like a person demoing, not a slideshow:

- a recorded clip keeps its real speed when it is within -10%/+15% of its target, so the cursor and
  the typing are never visibly sped up or slowed down; a longer take is sped up at most x1.2, then its
  tail is trimmed; a shorter one holds its last frame only up to the -10% mark;
- still cards drift in very slowly (2.5% over the card) instead of sitting frozen;
- scenes join with a short cross-fade (0.35 s), not hard cuts or dips to black;
- each clip's ``<scene>.marks.json`` (written by record_scenes.cjs) becomes a small on-screen label
  ("Ken · outside contractor"), faded in and out at the moment the action happens;
- slow waits the recorder marked (a page loading, an answer being checked) are cut down to about a
  second, as a person editing a screen recording would. The answers themselves are never cut.

The real timeline is written to ``submission/05_video/timeline.json``; narration.py reads it, so the
voice-over cue sheet always matches the picture. Missing inputs are replaced by a labelled
placeholder and reported, never silently skipped.
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
WB = REPO / "submission" / "02_workbuddy"
W, H, FPS = 1920, 1080, 30
XFADE = 0.35

# (kind, file, target seconds, label) — the label's first word is the VIDEO_SCRIPT.md scene number
SEGMENTS = [
    ("card", "01_title.png", 9, "1 Hello"),
    ("clip", "01_login.webm", 13, "1 Hello: sign-in"),
    ("card", "02_why.png", 29, "2 Why this matters"),
    ("clip", "03_ask_jdoe.webm", 32, "3 One question, three platforms"),
    ("clip", "04_map.webm", 18, "4 The map of what you can see"),
    ("clip", "05_compare.webm", 18, "5 Same question, two people"),
    ("clip", "10_tour.webm", 15.5, "5b The guided tour"),
    ("clip", "06_restricted_vs_nonexistent.webm", 18, "6 Restricted looks like non-existent"),
    ("clip", "07_fintech.webm", 30, "7 The fintech part"),
    ("clip", "08_revocation_freshness.webm", 26, "8 Access changes, answers change"),
    ("clip", "09_audit.webm", 36, "9 The audit trail"),
    ("card", "09b_tamper.png", 9, "9 Tamper demo"),
    ("still", "workbuddy_mcp_panel.png", 5, "10 WorkBuddy: the connectors"),
    ("still", "workbuddy_compare.png", 11, "10 WorkBuddy: two people"),
    ("still", "workbuddy_compliance.png", 8, "10 WorkBuddy: compliance"),
    ("clip", "11_walnut_original.webm", 32, "11 What's underneath"),
    ("card", "12_architecture.png", 22, "12 How it fits together"),
    ("card", "13_numbers.png", 11, "13 The numbers"),
    ("card", "14_closing.png", 16, "14 Built with, thanks"),
]


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if r.returncode:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-1500:]}")


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


FIT = (f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:"
       f"color=0x070a14,fps={FPS},format=yuv420p")


def _font(size: int):
    from PIL import ImageFont
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def label_png(text: str, out: Path) -> None:
    """A small dark pill with white text, drawn at the lower left of the frame."""
    from PIL import Image, ImageDraw
    f = _font(34)
    tw = int(ImageDraw.Draw(Image.new("RGBA", (10, 10))).textlength(text, font=f))
    img = Image.new("RGBA", (tw + 64, 68), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, img.width - 1, img.height - 1), 16, fill=(8, 12, 26, 222),
                        outline=(125, 170, 255, 200), width=2)
    d.text((32, 12), text, font=f, fill=(240, 244, 252, 255))
    img.save(out)


def placeholder(label: str, name: str, out: Path) -> Path:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), (26, 31, 46))
    d = ImageDraw.Draw(img)
    msg = f"MISSING: {label} ({name})"
    f = _font(46)
    d.text(((W - d.textlength(msg, font=f)) / 2, H / 2 - 30), msg, fill=(240, 200, 120), font=f)
    img.save(out)
    return out


def still(src: Path, seconds: float, out: Path) -> None:
    """A card or screenshot that drifts in by 2.5% over its time on screen."""
    frames = int(round(seconds * FPS))
    drift = (f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=decrease:flags=lanczos,"
             f"pad={W * 2}:{H * 2}:(ow-iw)/2:(oh-ih)/2:color=0x070a14,"
             f"zoompan=z='1+0.025*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:"
             f"s={W}x{H}:fps={FPS},format=yuv420p")
    run(["ffmpeg", "-y", "-loop", "1", "-i", str(src), "-vf", drift, "-frames:v", str(frames),
         "-c:v", "libx264", "-preset", "medium", "-crf", "17", str(out)])


def _shift(at: float, cuts: list[list[float]]) -> float:
    """Where a moment of the raw recording lands once the cuts are taken out."""
    gone = 0.0
    for a, b in cuts:
        if at >= b:
            gone += b - a
        elif at > a:
            gone += at - a
    return at - gone


def clip(src: Path, target: float, out: Path, work: Path) -> tuple[float, str]:
    marks_file = src.with_suffix(".marks.json")
    raw = json.loads(marks_file.read_text(encoding="utf-8")) if marks_file.exists() else []
    cuts = sorted(m["cut"] for m in raw if "cut" in m and m["cut"][1] - m["cut"][0] > 0.2)
    marks = [{**m, "at": _shift(m["at"], cuts)} for m in raw if "text" in m]
    recorded = duration(src)
    have = recorded - sum(b - a for a, b in cuts)
    lo, hi = target * 0.9, target * 1.15
    speed, length = 1.0, have
    if have > hi:
        speed = min(have / hi, 1.2)
        length = min(have / speed, hi)
    elif have < lo:
        length = lo
    pad = max(0.0, length - have / speed)
    inputs = ["-i", str(src)]
    trim = ""
    if cuts:
        drop = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in cuts)
        trim = f"fps={FPS},select='not({drop})',setpts=N/{FPS}/TB,"
    chain = ("[0:v]" + trim + (f"setpts=PTS/{speed:.4f}," if speed > 1 else "") + FIT
             + (f",tpad=stop_mode=clone:stop_duration={pad:.2f}" if pad > 0 else "") + "[v0]")
    last, n = "v0", 0
    for i, m in enumerate(marks):
        at = m["at"] / speed
        if at > length - 1.0:
            continue
        dur = min(m.get("seconds", 3.5), length - at - 0.3)
        png = work / f"{src.stem}_m{i}.png"
        label_png(m["text"], png)
        inputs += ["-loop", "1", "-t", f"{length:.2f}", "-i", str(png)]
        n += 1
        chain += (f";[{n}:v]format=rgba,fade=t=in:st={at:.2f}:d=0.3:alpha=1,"
                  f"fade=t=out:st={at + dur - 0.3:.2f}:d=0.3:alpha=1[l{i}]"
                  f";[{last}][l{i}]overlay=x=56:y={H - 132}:enable='between(t,{at:.2f},{at + dur:.2f})'[o{i}]")
        last = f"o{i}"
    run(["ffmpeg", "-y", *inputs, "-filter_complex", chain, "-map", f"[{last}]", "-an", "-c:v", "libx264",
         "-preset", "medium", "-crf", "17", "-r", str(FPS), "-t", f"{length:.2f}", str(out)])
    note = (f"recorded {recorded:.1f}s" + (f", {recorded - have:.1f}s of waiting cut" if cuts else "")
            + (f", x{speed:.2f}" if speed > 1 else "") + (f", held {pad:.1f}s" if pad > 0.05 else "")
            + (f", {n} labels" if n else ""))
    return length, note


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", type=Path, help="voice-over audio to lay under the picture")
    ap.add_argument("--out", type=Path, default=V / "walnut_internal_brain_demo.mp4")
    args = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="ibvideo-"))
    parts: list[tuple[Path, float, str, str]] = []
    for i, (kind, name, secs, label) in enumerate(SEGMENTS):
        src = {"card": CARDS, "clip": CLIPS, "still": WB}[kind] / name
        out = work / f"{i:02d}.mp4"
        if not src.exists():
            still(placeholder(label, name, work / f"{i:02d}_missing.png"), secs, out)
            parts.append((out, float(secs), label, f"MISSING {name}"))
        elif kind == "clip":
            length, note = clip(src, secs, out, work)
            parts.append((out, length, label, note))
        else:
            still(src, secs, out)
            parts.append((out, float(secs), label, kind))

    # join the segments with short cross-fades
    inputs, chain, prev, offset, start, timeline = [], [], "0:v", 0.0, 0.0, []
    for i, (p, length, label, _) in enumerate(parts):
        inputs += ["-i", str(p)]
        timeline.append({"label": label, "start": round(start, 2), "end": round(start + length, 2)})
        if i:
            offset += parts[i - 1][1] - XFADE
            chain.append(f"[{prev}][{i}:v]xfade=transition=fade:duration={XFADE}:offset={offset:.3f}[x{i}]")
            prev = f"x{i}"
        start += length - XFADE
    total = sum(x[1] for x in parts) - XFADE * (len(parts) - 1)
    silent = work / "silent.mp4"
    run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(chain), "-map", f"[{prev}]", "-c:v", "libx264",
         "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(silent)])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.voice:
        run(["ffmpeg", "-y", "-i", str(silent), "-i", str(args.voice), "-c:v", "copy", "-c:a", "aac",
             "-b:a", "192k", "-shortest", str(args.out)])
    else:
        shutil.copyfile(silent, args.out)
    (V / "timeline.json").write_text(json.dumps(timeline, indent=1), encoding="utf-8")
    for (_, length, label, note), seg in zip(parts, timeline):
        s = seg["start"]
        print(f"{int(s // 60):02d}:{int(s % 60):02d}  {label:38} {length:5.1f}s  {note}")
    print(f"total {int(total // 60)}:{int(total % 60):02d} -> {args.out}")
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
