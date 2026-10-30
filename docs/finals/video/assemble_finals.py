"""Assemble the finals demo video: camera intro, title card, recorded scene clips, cards and slides.

    python docs/finals/video/assemble_finals.py                      picture + cue sheet + subtitles
    python docs/finals/video/assemble_finals.py --voice voice.m4a    ... and lay the voice-over under it
    python docs/finals/video/assemble_finals.py --cues-only [--hy3]  only re-write the cue sheet / .srt
    python docs/finals/video/assemble_finals.py --self-test          check the take checks (no ffmpeg)

(Use a Python with Pillow, e.g. ``databridge\\.venv\\Scripts\\python``; ffmpeg and ffprobe on PATH.)

Outputs, all in ``submission/finals_video/``:

- ``walnut_databridge_finals_SILENT.mp4``: the full picture. Silent except the camera intro, which keeps
  its own sound. Ishaan records the voice-over against it.
- ``walnut_databridge_finals.mp4``: with ``--voice``, the same picture with the voice-over mixed in.
- ``timeline.json``: where every segment really starts and ends.
- ``narration_cues.md`` and ``narration.srt``: what to say, when, from VIDEO_SCRIPT_FINALS.md and the
  real timeline.

Adapted from docs/tencent/video/assemble.py (the first video); the edit follows the same rules:

- the camera intro (``submission/05_video/voice/intro_final.mp4``: Ishaan to camera, letterboxed with the
  navy bars and the "Ishaan · Team Walnut" label) opens the video, exactly as before;
- a recorded clip keeps its real speed when it is within -10%/+15% of its target, so the cursor and the
  typing are never visibly sped up; a longer take is sped up at most x1.2, then its tail is trimmed; a
  shorter one holds its last frame only up to the -10% mark;
- still cards and slides drift in very slowly (2.5% over the card) instead of sitting frozen;
- segments join with a short cross-fade (0.35 s);
- each clip comes from its current good take: ``clips/<scene>.marks.json`` (written by record_finals.cjs
  only after a clean take) names the take's video under ``clips/takes/`` and carries its marks. Each
  label becomes a small pill at the lower left, faded in and out when the action happens; slow waits it
  marked are cut to about a second, and the blank frames before the first page are dropped. Answers
  themselves are never cut;
- ``--voice`` lines the voice-over up with the cue sheet (its first spoken word lands on the first cue),
  cleans it (high-pass, loudness-normalised to -16 LUFS) and mixes it under the picture after the intro.

Missing inputs are replaced by a labelled placeholder and reported, never silently skipped; a segment
marked optional is left out instead. A clip whose current take fails ``check_take`` (a failed or
unfinished take, a missing label, a zero duration, a video that is not the one the marks were made with,
or an old-style marks list with no take) stops the whole assembly before anything is rendered: re-record
that scene. An optional scene with such a take is left out, with the reason printed.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
V = REPO / "submission" / "finals_video"
CARDS, CLIPS = V / "cards", V / "clips"
WB = REPO / "submission" / "02_workbuddy"
INTRO = REPO / "submission" / "05_video" / "voice" / "intro_final.mp4"
SCRIPT = REPO / "docs" / "finals" / "video" / "VIDEO_SCRIPT_FINALS.md"
SILENT = V / "walnut_databridge_finals_SILENT.mp4"
FINAL = V / "walnut_databridge_finals.mp4"
W, H, FPS = 1920, 1080, 30
XFADE = 0.35
WPS = 2.5                                   # relaxed speaking pace: about 150 words a minute

# (kind, file, target seconds, label, extra). The label's first word is the VIDEO_SCRIPT_FINALS.md scene
# number. extra: {"optional": True} leaves a missing segment out; {"pills": [(at, seconds, text)]} puts
# label pills on a still. kind: camera (the intro, kept whole with its sound), card (cards/), clip
# (clips/), still (submission/02_workbuddy/).
SEGMENTS = [
    ("camera", "intro_final.mp4", 0, "00 Camera intro", {}),
    ("card", "01_title.png", 6, "01 Title", {}),
    ("clip", "02_compare_amy_rachel.webm", 40, "02 Same question, two people", {}),
    ("clip", "03_ken_identical.webm", 23, "03 Restricted looks like nothing", {}),
    ("clip", "04_source_live.webm", 25, "04 A source, checked live", {}),
    ("clip", "05_journey.webm", 30, "05 Developer mode: the journey", {}),
    ("clip", "06_graph.webm", 25, "06 The knowledge graph", {}),
    ("clip", "06b_brain_grow.webm", 28, "06b Watch the brain grow", {"optional": True}),
    ("clip", "07_analytics.webm", 17, "07 Analytics", {}),
    ("clip", "08_audit.webm", 40, "08 Audit, Verify and the tamper demo", {}),
    ("clip", "09_trust.webm", 15, "09 The Trust centre", {}),
    ("clip", "10_engine.webm", 26, "10 The Walnut engine", {}),
    ("card", "11_codebuddy.png", 13, "11 Built with CodeBuddy", {}),
    ("still", "workbuddy_mcp_panel.png", 6, "12 WorkBuddy: the connectors",
     {"pills": [(0.6, 5.0, "Tencent WorkBuddy · real window capture (15 Oct)")]}),
    ("still", "workbuddy_compare.png", 11, "12 WorkBuddy: two people",
     {"pills": [(1.0, 5.0, "One question, Jordan's and Ken's connectors: two answers")]}),
    ("clip", "13_hy3_status.webm", 15, "13 Tencent Hy3", {}),
    ("card", "14_proof.png", 18, "14 The proof",
     {"pills": [(9.5, 7.0, "Plus 1,131 backend tests and 166 browser tests, all passing")]}),
    ("card", "15_business.png", 19, "15 What it's worth", {}),
    ("card", "16_close.png", 11, "16 Close", {}),
]


def run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-1500:]}")
    return r.stderr


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


FIT = (f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:"
       f"color=0x0a0b0d,fps={FPS},format=yuv420p")


def _font(size: int):
    from PIL import ImageFont
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _cjk_font(size: int):
    from PIL import ImageFont
    for name in ("msyhbd.ttc", "msyh.ttc", "simhei.ttf", "NotoSansCJK-Bold.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return _font(size)


def label_png(text: str, out: Path) -> None:
    """A small dark pill with white text, drawn at the lower left of the frame (as in the first video)."""
    from PIL import Image, ImageDraw
    f = _cjk_font(34) if re.search(r"[一-鿿]", text) else _font(34)
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


def _pills(marks: list[dict], length: float, first_input: int, work: Path, stem: str, src_label: str):
    """Inputs and filter chain that overlay label pills on stream [src_label]."""
    inputs, chain, last, n = [], "", src_label, 0
    for i, m in enumerate(marks):
        at = m["at"]
        if at > length - 1.0:
            continue
        dur = min(m.get("seconds", 3.5), length - at - 0.3)
        png = work / f"{stem}_m{i}.png"
        label_png(m["text"], png)
        inputs += ["-loop", "1", "-t", f"{length:.2f}", "-i", str(png)]
        idx = first_input + n
        n += 1
        chain += (f";[{idx}:v]format=rgba,fade=t=in:st={at:.2f}:d=0.3:alpha=1,"
                  f"fade=t=out:st={at + dur - 0.3:.2f}:d=0.3:alpha=1[l{i}]"
                  f";[{last}][l{i}]overlay=x=56:y={H - 132}:enable='between(t,{at:.2f},{at + dur:.2f})'[o{i}]")
        last = f"o{i}"
    return inputs, chain, last, n


def still(src: Path, seconds: float, out: Path, work: Path, pills=()) -> None:
    """A card, slide or screenshot that drifts in by 2.5% over its time on screen."""
    frames = int(round(seconds * FPS))
    drift = (f"[0:v]scale={W * 2}:{H * 2}:force_original_aspect_ratio=decrease:flags=lanczos,"
             f"pad={W * 2}:{H * 2}:(ow-iw)/2:(oh-ih)/2:color=0x0a0b0d,"
             f"zoompan=z='1+0.025*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:"
             f"s={W}x{H}:fps={FPS},format=yuv420p[v0]")
    marks = [{"at": a, "seconds": s, "text": t} for a, s, t in pills]
    extra, chain, last, _ = _pills(marks, seconds, 1, work, src.stem, "v0")
    run(["ffmpeg", "-y", "-loop", "1", "-i", str(src), *extra, "-filter_complex", drift + chain,
         "-map", f"[{last}]", "-frames:v", str(frames), "-c:v", "libx264", "-preset", "medium", "-crf", "17",
         "-r", str(FPS), str(out)])


def camera(src: Path, out: Path) -> float:
    """The camera intro, kept whole (picture and sound), normalised to the video's format."""
    run(["ffmpeg", "-y", "-i", str(src), "-vf", FIT, "-c:v", "libx264", "-preset", "medium", "-crf", "17",
         "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(out)])
    return duration(out)


def _shift(at: float, cuts: list[list[float]]) -> float:
    """Where a moment of the raw recording lands once the cuts are taken out."""
    gone = 0.0
    for a, b in cuts:
        if at >= b:
            gone += b - a
        elif at > a:
            gone += at - a
    return at - gone


# ---- takes: only the current good take of a scene is ever used ---------------------------------
class TakeError(Exception):
    """The scene's current take cannot be trusted; the message says why."""


def check_take(t: object, clips: Path, scene: str) -> list[str]:
    """Problems with a take record (empty: it is fine). The same checks as record_finals.cjs checkTake()."""
    if isinstance(t, list):
        return ["an old-style marks list with no take id (written before takes were kept): re-record it"]
    if not isinstance(t, dict):
        return ["not a take record"]
    bad: list[str] = []
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
    dur = t.get("duration")
    if t.get("scene") != scene:
        bad.append(f"it is a take of {t.get('scene')!r}")
    if t.get("ok") is not True or t.get("rejected"):
        bad.append(f"the take failed: {t.get('error') or t.get('rejected')}")
    if not num(dur) or dur <= 0:
        bad.append(f"duration {dur}")
        dur = 0.0
    video = clips / str(t.get("video") or "")
    if not t.get("video") or not video.is_file():
        bad.append(f"video {t.get('video')} missing")
    elif not num(t.get("bytes")) or t["bytes"] <= 0 or video.stat().st_size != t["bytes"]:
        bad.append(f"video {t.get('video')} is not the {t.get('bytes')} bytes the marks were made with")
    marks = t.get("marks") if isinstance(t.get("marks"), list) else []
    starts = [m["start"] for m in marks if isinstance(m, dict) and "start" in m]
    if len(starts) != 1 or not num(starts[0]) or not 0 <= starts[0] < dur:
        bad.append("no single start mark inside the clip")
    labels = [m for m in marks if isinstance(m, dict) and "text" in m]
    want = t.get("expected_labels")
    if not isinstance(want, int) or want <= 0 or len(labels) != want:
        bad.append(f"{len(labels)} of {want} labels")
    for m in labels:
        if not (num(m.get("at")) and 0 <= m["at"] <= dur and num(m.get("seconds")) and m["seconds"] > 0):
            bad.append(f"label {m.get('text')!r} at {m.get('at')}s for {m.get('seconds')}s")
    for m in marks:
        if isinstance(m, dict) and "cut" in m:
            c = m["cut"]
            if not (isinstance(c, list) and len(c) == 2 and all(num(x) for x in c) and 0 <= c[0] < c[1] <= dur + 0.5):
                bad.append(f"cut {c}")
    return bad


def current_take(clips: Path, scene: str) -> tuple[Path, list[dict]] | None:
    """The video and marks of the scene's current good take; None if it was never recorded."""
    pointer = clips / f"{scene}.marks.json"
    if not pointer.exists():
        return None
    try:
        t = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise TakeError(f"{pointer.name}: unreadable ({e})") from e
    problems = check_take(t, clips, scene)
    if problems:
        raise TakeError(f"{pointer.name}: " + "; ".join(problems))
    return clips / t["video"], t["marks"]


def self_test() -> int:
    """Simulate good, failed and incomplete takes in a temp folder and check each is judged right."""
    d = Path(tempfile.mkdtemp(prefix="finals-takes-"))
    try:
        (d / "takes" / "s1" / "A").mkdir(parents=True)
        (d / "takes" / "s1" / "A" / "s1.webm").write_bytes(b"\1" * 500)
        good = {"scene": "s1", "take": "A", "ok": True, "duration": 20.0, "expected_labels": 2,
                "video": "takes/s1/A/s1.webm", "bytes": 500,
                "marks": [{"start": 1.2}, {"at": 2, "seconds": 4, "text": "one"}, {"cut": [3, 6]},
                          {"at": 7, "seconds": 3.5, "text": "two"}]}
        pointer = d / "s1.marks.json"

        def judge(t: object) -> str:
            pointer.write_text(json.dumps(t), encoding="utf-8")
            try:
                current_take(d, "s1")
                return ""
            except TakeError as e:
                return str(e)

        assert current_take(d, "nothing") is None
        assert judge(good) == "", judge(good)
        video, marks = current_take(d, "s1")
        assert video == d / "takes/s1/A/s1.webm" and len(marks) == 4
        cases = {
            "the take failed": {**good, "ok": False, "error": "timeout"},
            "1 of 2 labels": {**good, "marks": good["marks"][:3]},
            "duration 0": {**good, "duration": 0},
            "no single start mark": {**good, "marks": good["marks"][1:]},
            "is not the 400 bytes": {**good, "bytes": 400},           # marks of another take's video
            "takes/s1/B/s1.webm missing": {**good, "video": "takes/s1/B/s1.webm"},
            "old-style marks list": good["marks"],                      # what the old recorder wrote
            "label 'two' at 7s for 0s": {**good, "marks": good["marks"][:3] + [{"at": 7, "seconds": 0, "text": "two"}]},
            "a take of 's2'": {**good, "scene": "s2"},
        }
        for want, t in cases.items():
            got = judge(t)
            assert want in got, f"{want!r} not in {got!r}"
        pointer.write_text("{", encoding="utf-8")                      # cut off mid-write
        try:
            current_take(d, "s1")
            raise AssertionError("a truncated marks file was accepted")
        except TakeError as e:
            assert "unreadable" in str(e), e
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print(f"self-test ok: {len(cases)} bad takes refused, the good take accepted")
    return 0


def clip(src: Path, target: float, out: Path, work: Path, raw: list[dict]) -> tuple[float, str]:
    cuts = [m["cut"] for m in raw if "cut" in m and m["cut"][1] - m["cut"][0] > 0.2]
    start = max([m["start"] for m in raw if "start" in m] or [0.0])
    if start > 0.05:
        cuts.append([0.0, start])                    # blank frames before the first page is drawn
    cuts = sorted(cuts)
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
    trim = ""
    if cuts:
        drop = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in cuts)
        trim = f"fps={FPS},select='not({drop})',setpts=N/{FPS}/TB,"
    chain = ("[0:v]" + trim + (f"setpts=PTS/{speed:.4f}," if speed > 1 else "") + FIT
             + (f",tpad=stop_mode=clone:stop_duration={pad:.2f}" if pad > 0 else "") + "[v0]")
    for m in marks:
        m["at"] = m["at"] / speed
    extra, pills, last, n = _pills(marks, length, 1, work, src.stem, "v0")
    run(["ffmpeg", "-y", "-i", str(src), *extra, "-filter_complex", chain + pills, "-map", f"[{last}]", "-an",
         "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-r", str(FPS), "-t", f"{length:.2f}", str(out)])
    note = (f"recorded {recorded:.1f}s" + (f", {recorded - have:.1f}s of waiting cut" if cuts else "")
            + (f", x{speed:.2f}" if speed > 1 else "") + (f", held {pad:.1f}s" if pad > 0.05 else "")
            + (f", {n} labels" if n else ""))
    return length, note


# ---- narration: cue sheet and subtitles -------------------------------------------------------
def scenes_from_script(hy3: bool) -> dict[str, list[str]]:
    """Narration per scene from VIDEO_SCRIPT_FINALS.md ('### NN · Title' then '> ' lines), one entry per
    sentence. '[if Hy3 is on]' / '[otherwise]' lines are picked by --hy3."""
    out: dict[str, list[str]] = {}
    cur = None
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^###\s+(\w+)\s+·", line)
        if m:
            cur = m.group(1)
            out[cur] = []
            continue
        if line.startswith("## "):
            cur = None
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
    return {k: [x.strip() for para in v for x in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", para) if x.strip()]
            for k, v in out.items()}


def scene_spans(timeline: list[dict]) -> dict[str, tuple[float, float]]:
    spans: dict[str, tuple[float, float]] = {}
    for seg in timeline:
        scene = seg["label"].split()[0]
        a, _ = spans.get(scene, (seg["start"], seg["start"]))
        spans[scene] = (a, seg["end"])
    return spans


def stamp(sec: float, srt: bool = True) -> str:
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return (f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)) % 1000:03d}" if srt
            else f"{int(m)}:{int(s):02d}")


def write_cues(timeline: list[dict], hy3: bool) -> list[dict]:
    """narration_cues.md and narration.srt from the script and the real timeline; returns the cues."""
    lines, spans = scenes_from_script(hy3), scene_spans(timeline)
    total = timeline[-1]["end"] if timeline else 0
    srt, cues, warn, n = [], [], [], 1
    md = ["# Voice-over cue sheet (finals video)", "",
          f"Picture: `walnut_databridge_finals_SILENT.mp4` ({stamp(total, False)}). Play it with "
          "`narration.srt` loaded as subtitles (VLC: Subtitle → Add Subtitle File) and read each line as it "
          "appears. Relaxed pace, about 150 words a minute; pauses are fine. "
          + ("These are the **Hy3 is on** lines." if hy3 else
             "These are the default lines (Hy3 not switched on yet)."), "",
          "The camera intro (0:00 to the first cue) already has your voice: stay quiet until the title card.", ""]
    for scene, (a, b) in spans.items():
        said = lines.get(scene, [])
        if not said:
            continue
        words = [len(x.split()) for x in said]
        need = sum(words) / WPS
        room = b - a - 0.9
        if need > room:
            warn.append(f"scene {scene}: {need:.0f}s of speech for {room:.0f}s of picture")
        scale = min(1.0, room / need) if need else 1.0
        t = a + 0.5
        title = next((s["label"] for s in timeline if s["label"].split()[0] == scene), scene)
        md += [f"## {stamp(a, False)}–{stamp(b, False)} · {title}", ""]
        for text, w in zip(said, words):
            d = max(1.8, w / WPS * scale)
            end = min(t + d, b - 0.2)
            srt += [str(n), f"{stamp(t)} --> {stamp(end)}", text, ""]
            md.append(f"- **{stamp(t, False)}** {text}")
            cues.append({"scene": scene, "start": round(t, 2), "end": round(end, 2), "text": text})
            n, t = n + 1, t + d
        md.append("")
    V.mkdir(parents=True, exist_ok=True)
    (V / "narration.srt").write_text("\n".join(srt), encoding="utf-8")
    (V / "narration_cues.md").write_text("\n".join(md), encoding="utf-8")
    print(f"{n - 1} cues -> {V / 'narration_cues.md'} and narration.srt")
    for w in warn:
        print("TIGHT:", w)
    return cues


# ---- voice-over -------------------------------------------------------------------------------
def speech_onset(path: Path, skip: float) -> float:
    """Seconds into the file (after `skip`) where speech first starts."""
    err = run(["ffmpeg", "-hide_banner", "-ss", f"{skip:.3f}", "-i", str(path), "-af",
               "highpass=f=100,silencedetect=noise=-38dB:d=0.35", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    if starts and starts[0] < 0.05 and ends:
        return ends[0]
    return 0.0


def mix_voice(silent: Path, voice: Path, at: str, skip: float, intro_end: float, first_cue: float,
              out: Path) -> None:
    """The silent picture + the camera intro's own sound + the voice-over, both loudness-normalised."""
    if at == "auto":
        onset = speech_onset(voice, skip)
        place = first_cue - onset                          # where the file's t=skip lands in the video
        print(f"voice: speech starts {onset:.2f}s into the file; first cue at {first_cue:.2f}s "
              f"-> file placed at {place:.2f}s")
    else:
        place = float(at)
    if place < 0:                                          # the file starts earlier than the video
        skip, place = skip - place, 0.0
    # never let the voice track play under the camera intro
    if place < intro_end:
        skip, place = skip + (intro_end - place), intro_end
    total = duration(silent)
    # the silent master already carries the intro's normalised sound (or nothing, with --no-intro)
    base = "[0:a]anull" if intro_end > 0 else f"anullsrc=r=48000:cl=stereo,atrim=duration={total:.2f}"
    afilter = (f"{base},apad=whole_dur={total:.2f}[ia];"
               f"[1:a]atrim=start={skip:.3f},asetpts=PTS-STARTPTS,highpass=f=80,"
               f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,adelay={int(place * 1000)}:all=1[va];"
               f"[ia][va]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]")
    run(["ffmpeg", "-y", "-i", str(silent), "-i", str(voice), "-filter_complex", afilter, "-map", "0:v",
         "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags",
         "+faststart", "-t", f"{total:.2f}", str(out)])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--voice", type=Path, help="the voice-over recording to mix in (any audio or video file)")
    ap.add_argument("--voice-at", default="auto",
                    help="'auto' (default): the first spoken word lands on the first cue; or the second of "
                         "the video where the voice file's start (after --voice-skip) should land")
    ap.add_argument("--voice-skip", type=float, default=0.0,
                    help="seconds to drop from the start of the voice file (a count-in, a cough)")
    ap.add_argument("--hy3", action="store_true", help="use the [if Hy3 is on] lines in the cue sheet")
    ap.add_argument("--cues-only", action="store_true", help="only re-write the cue sheet from timeline.json")
    ap.add_argument("--no-intro", action="store_true", help="leave the camera intro out")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--self-test", action="store_true", help="check the take checks on made-up takes, then stop")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if args.cues_only:
        timeline = json.loads((V / "timeline.json").read_text(encoding="utf-8"))
        write_cues(timeline, args.hy3)
        return 0

    # every clip's current take is checked before anything is rendered (Codex round 2b #3)
    takes: dict[str, tuple[Path, list[dict]] | None] = {}
    refused, unusable = [], set()
    for kind, name, _, label, extra in SEGMENTS:
        if kind != "clip":
            continue
        try:
            takes[name] = current_take(CLIPS, Path(name).stem)
        except TakeError as e:
            if extra.get("optional"):
                unusable.add(name)
                print(f"skip  {label} (optional; its take is not usable: {e})")
            else:
                refused.append(f"{label}: {e}")
    if refused:
        print("REFUSED: these scenes have no complete good take; re-record them with record_finals.cjs:")
        for r in refused:
            print("  -", r)
        return 1

    work = Path(tempfile.mkdtemp(prefix="finalsvideo-"))
    parts: list[tuple[Path, float, str, str]] = []
    intro_end = 0.0
    for i, (kind, name, secs, label, extra) in enumerate(SEGMENTS):
        if kind == "camera" and args.no_intro:
            continue
        if name in unusable:
            continue                                   # optional, reported above
        src = {"camera": INTRO, "card": CARDS, "clip": CLIPS, "still": WB}[kind]
        src = src if kind == "camera" else src / name
        take = takes.get(name)
        if take:
            src = take[0]                              # the current good take's own video
        out = work / f"{i:02d}.mp4"
        if (kind == "clip" and not take) or not src.exists():
            if extra.get("optional"):
                print(f"skip  {label} (optional; {name} not recorded)")
                continue
            secs = secs or 5
            still(placeholder(label, name, work / f"{i:02d}_missing.png"), secs, out, work)
            parts.append((out, float(secs), label, f"MISSING {name}"))
        elif kind == "camera":
            length = camera(src, out)
            parts.append((out, length, label, "camera intro, own sound"))
        elif kind == "clip":
            length, note = clip(src, secs, out, work, take[1])
            parts.append((out, length, label, note))
        else:
            still(src, secs, out, work, extra.get("pills", ()))
            parts.append((out, float(secs), label, kind))

    # join the segments with short cross-fades
    inputs, chain, prev, offset, start, timeline = [], [], "0:v", 0.0, 0.0, []
    for i, (p, length, label, _) in enumerate(parts):
        inputs += ["-i", str(p)]
        timeline.append({"label": label, "start": round(start, 2), "end": round(start + length, 2)})
        if label.startswith("00 "):
            intro_end = start + length
        if i:
            offset += parts[i - 1][1] - XFADE
            chain.append(f"[{prev}][{i}:v]xfade=transition=fade:duration={XFADE}:offset={offset:.3f}[x{i}]")
            prev = f"x{i}"
        start += length - XFADE
    total = sum(x[1] for x in parts) - XFADE * (len(parts) - 1)
    joined = work / "joined.mp4"
    run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(chain), "-map", f"[{prev}]", "-c:v", "libx264",
         "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", str(joined)])
    # the silent master: the picture, plus the camera intro's own sound (silence after it)
    V.mkdir(parents=True, exist_ok=True)
    if intro_end > 0:
        run(["ffmpeg", "-y", "-i", str(joined), "-i", str(parts[0][0]), "-filter_complex",
             f"[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=out:st={max(0, intro_end - 0.4):.2f}:d=0.4,"
             f"apad=whole_dur={total:.2f}[a]", "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
             "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", "-t", f"{total:.2f}", str(SILENT)])
    else:
        run(["ffmpeg", "-y", "-i", str(joined), "-c", "copy", "-movflags", "+faststart", str(SILENT)])
    (V / "timeline.json").write_text(json.dumps(timeline, indent=1), encoding="utf-8")
    cues = write_cues(timeline, args.hy3)
    for (_, length, label, note), seg in zip(parts, timeline):
        s = seg["start"]
        print(f"{int(s // 60):02d}:{s % 60:05.2f}  {label:40} {length:5.1f}s  {note}")
    print(f"total {int(total // 60)}:{total % 60:05.2f} -> {SILENT}")
    if args.voice:
        out = args.out or FINAL
        mix_voice(SILENT, args.voice, args.voice_at, args.voice_skip, intro_end, cues[0]["start"] if cues else intro_end, out)
        print(f"with voice -> {out}")
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
