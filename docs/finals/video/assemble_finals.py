"""Assemble the finals demo video: camera intro, title card, recorded scene clips, cards and slides.

    python docs/finals/video/assemble_finals.py                      picture + cue sheet + subtitles
    python docs/finals/video/assemble_finals.py --voice voice.m4a    ... and lay the voice-over under it
    python docs/finals/video/assemble_finals.py --cues-only [--hy3]  only re-write the cue sheet / .srt
    python docs/finals/video/assemble_finals.py --self-test          check the take checks (no ffmpeg)
    python docs/finals/video/assemble_finals.py --cut short|main|full   which cut (default main; see below)
    python docs/finals/video/assemble_finals.py --cues-only --plan --cut full   cue sheet from the PLANNED
                                                                       slot lengths, before anything is recorded

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

Three cuts are built from the same takes (``--cut``): short (about 6:00), main (about 7:55, the default, kept
under the 8:00 of the 5 to 8 minute range in docs/tencent/SUBMISSION.md) and full (about 9:15). Each SEGMENTS row
gives its seconds for the three cuts; 0 leaves the scene out of that cut. Main writes the usual file names; the
others add ``_short`` / ``_full`` (``..._short_SILENT.mp4``, ``timeline_short.json``, ``narration_cues_short.md``).
Narration lines in VIDEO_SCRIPT_FINALS.md tagged ``**[MAIN]**`` are read in main and full, ``**[FULL]**`` only in
full; ``TODO`` lines (the teammates' slots) are never read. A segment marked optional is skipped, with a printed
line, when its clip or card does not exist yet.

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
EVAL_SUMMARY = REPO / "databridge" / "apps" / "internal_brain_web" / "public" / "eval_summary.json"
CONSTANTS = HERE / "video_constants.json"
CUTS = ("short", "main", "full")
CUT, SUF = "main", ""                       # set by set_cut(); main keeps the original file names
SILENT = V / "walnut_databridge_finals_SILENT.mp4"
FINAL = V / "walnut_databridge_finals.mp4"
INTRO_SECONDS = 31.8                        # the camera intro's length, used for planned timings only


def set_cut(cut: str) -> None:
    """Choose the cut: the output file names follow it (main = the original names)."""
    global CUT, SUF, SILENT, FINAL
    CUT, SUF = cut, ("" if cut == "main" else f"_{cut}")
    SILENT = V / f"walnut_databridge_finals{SUF}_SILENT.mp4"
    FINAL = V / f"walnut_databridge_finals{SUF}.mp4"


def secs_for(secs, cut: str | None = None) -> float:
    """A SEGMENTS length: one number for every cut, or (short, main, full). 0 = left out of that cut."""
    return float(secs[CUTS.index(cut or CUT)] if isinstance(secs, (tuple, list)) else secs)
W, H, FPS = 1920, 1080, 30
XFADE = 0.35
WPS = 2.5                                   # relaxed speaking pace: about 150 words a minute

def facts() -> dict:
    """The numbers the video says or shows, read from the evidence, never typed here: eval_summary.json
    (the app's own proof slide reads the same file) and video_constants.json (CodeBuddy session count)."""
    e = json.loads(EVAL_SUMMARY.read_text(encoding="utf-8"))
    c = json.loads(CONSTANTS.read_text(encoding="utf-8"))
    return {"requests": e["requests"], "leaks": e["leaks"], "pairs": e["differential"]["pairs"],
            "mismatches": e["differential"]["mismatches"], "injection": e["injection"]["requests"],
            "hard_failures": e["injection"]["hard_failures"], "backend": e["tests"]["backend"],
            "e2e": e["tests"]["e2e"], "stale": bool(e.get("stale")), "codebuddy": c["codebuddy_sessions"]}


_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
         "sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def words(n: int) -> str:
    """British spoken form: 1131 -> 'one thousand one hundred and thirty-one'."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else "")
    if n < 1000:
        return f"{_ONES[n // 100]} hundred" + (f" and {words(n % 100)}" if n % 100 else "")
    return f"{words(n // 1000)} thousand" + (f" and {words(n % 1000)}" if n % 1000 and n % 1000 < 100 else
                                              f" {words(n % 1000)}" if n % 1000 else "")


def norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower().replace("-", " ")).split())


def check_script_numbers() -> list[str]:
    """Problems between what scenes 11 and 14 SAY (VIDEO_SCRIPT_FINALS.md) and the evidence. [] = fine."""
    f, bad, text = facts(), [], norm(SCRIPT.read_text(encoding="utf-8"))
    if f["stale"]:
        bad.append("eval_summary.json says stale: true; re-run the evals before recording")
    if (f["leaks"], f["mismatches"], f["hard_failures"]) != (0, 0, 0):
        bad.append(f"the evidence is not 'zero': leaks {f['leaks']}, mismatches {f['mismatches']}, "
                   f"hard failures {f['hard_failures']}; the script says zero")
    for key, what in (("requests", "adversarial requests"), ("pairs", "paired questions"),
                      ("backend", "backend tests"), ("e2e", "browser tests")):
        w = norm(words(f[key]))
        if w not in text and not (w.startswith("one hundred") and "a" + w[3:] in text):   # "a hundred and ..."
            bad.append(f"the script never says '{words(f[key])}' ({f[key]:,} {what} in eval_summary.json)")
    if not any(norm(words(f["codebuddy"])) + w in text for w in (" codebuddy", " sessions")):
        bad.append(f"the script never says '{words(f['codebuddy'])}' CodeBuddy sessions")
    for stale in ("5 958", "5958", "754", "one hundred and twenty four", "nine hundred and seventy six", "six codebuddy"):
        if stale in text:
            bad.append(f"stale number or wording in the script: '{stale}'")
    return bad


# (kind, file, seconds, label, extra). seconds: one number, or (short, main, full) with 0 = left out of that cut.
# The label's first word is the VIDEO_SCRIPT_FINALS.md scene id. extra: {"optional": True} leaves a missing
# segment out (printed); {"slot": True} is a teammate's slot (optional, and left out of planned cue sheets until
# its clip exists); {"pills": [(at, seconds, text)]} puts label pills on a still. kind: camera (the intro, kept
# whole with its sound), card (cards/), clip (clips/), still (submission/02_workbuddy/).
# Order (founder's principle, 30 Oct evening; VIDEO_PLAN.md "The new timeline"): the 16 Oct video's spine, beat
# by beat, with each new feature next to the beat it strengthens. 16 Oct beats in brackets.
SEGMENTS = [
    ("camera", "intro_final.mp4", 0, "00 Camera intro", {}),
    ("card", "01_title.png", 9, "01 Title", {}),                   # [1] 9 s: Tencent is named in the first line
    # [3] one question, every sentence sourced, checked live (+ 简体中文)
    ("clip", "04_source_live.webm", (30, 34, 38), "04 One question, checked live", {}),
    # [4] the map: Jordan's everything, Ken's corner
    ("clip", "06_graph.webm", (20, 20, 26), "06 The map of what you can see", {}),
    # [5] + [7] same question, two people; the fintech part
    ("clip", "02_compare_amy_rachel.webm", (36, 38, 43), "02 Same question, two people", {}),
    # [6] restricted looks like non-existent
    ("clip", "03_ken_identical.webm", (23, 25, 28), "03 Restricted looks like nothing", {}),
    # enhancements to [7] and [3]: the answer becomes a reply, a ticket, a file
    ("clip", "17_act.webm", (18, 22, 26), "17 Act: a draft and a ticket", {"optional": True}),
    ("clip", "18_deliverables.webm", (10, 15, 20), "18 Deliverables: a table, a chart, a file", {"optional": True}),
    # [5b] the guided tour (+ dictation, the accessibility caption, the value calculator)
    ("clip", "21_extras.webm", (0, 12, 16), "21 New here: tour, dictation, value calculator", {"optional": True}),
    # [8] nothing stale: live growth and uploads
    ("clip", "06b_brain_grow.webm", (0, 22, 28), "06b Watch the brain grow", {"optional": True}),
    ("clip", "19_upload.webm", (0, 10, 15), "19 Add a file", {"optional": True}),
    # [9] the compliance side: the journey, the audit log and the tamper demo (+ analytics, Trust centre)
    ("clip", "05_journey.webm", (30, 35, 40), "05 Developer mode: the journey", {}),
    ("clip", "08_audit.webm", (40, 43, 50), "08 Audit, Verify and the tamper demo", {}),
    ("clip", "07_analytics.webm", (12, 12, 15), "07 Analytics", {}),
    ("clip", "09_trust.webm", 14, "09 The Trust centre", {}),
    # [10] WorkBuddy
    ("still", "workbuddy_mcp_panel.png", (5, 6, 6), "12 WorkBuddy: the connectors",
     {"pills": [(0.6, 4.0, "Tencent WorkBuddy · real window capture (15 Oct)")]}),
    ("still", "workbuddy_compare.png", (8, 9, 12), "12 WorkBuddy: two people",
     {"pills": [(1.0, 4.0, "One question, Jordan's and Ken's connectors: two answers")]}),
    # [11] what's underneath
    ("clip", "10_engine.webm", (20, 18, 25), "10 The Walnut engine", {}),
    # [12] how it fits together, and Hy3
    ("clip", "13_hy3_status.webm", (14, 14, 17), "13 Tencent Hy3", {}),
    # the teammates' slots: names, routes and lines come after the merge (TODO in the script)
    ("clip", "S1_voice.webm", (0, 8, 20), "S1 Voice", {"optional": True, "slot": True}),
    ("clip", "S2_jit_ui.webm", (0, 8, 20), "S2 Just-in-time UI", {"optional": True, "slot": True}),
    ("clip", "S3_dots.webm", (0, 6, 15), "S3 Dots", {"optional": True, "slot": True}),
    # [13] the numbers (+ the business case), [14] how we built it and thanks
    ("card", "14_proof.png", 20, "14 The proof",
     {"pills": [(10.5, 7.5, "PROOF_PILL")]}),
    ("card", "15_business.png", 21, "15 What it's worth",
     {"pills": [(12.0, 7.0, "Target host: Tencent Cloud. One-command deploy kit in the repo; no live instance yet")]}),
    ("card", "11_codebuddy.png", (10, 12, 14), "11 Built with CodeBuddy", {}),
    ("card", "20_miora.png", (0, 8, 10), "20 Miora: the cover art", {"optional": True}),
    ("card", "16_close.png", 13, "16 Close", {}),
]


def nominal_total(cut: str, slots: bool) -> float:
    """Length of a cut from the planned slot lengths, with or without the teammates' slots."""
    ns = [INTRO_SECONDS if k == "camera" else secs_for(sec, cut) for k, _, sec, _, e in SEGMENTS
          if (k == "camera" or secs_for(sec, cut) > 0) and (slots or not e.get("slot"))]
    return sum(ns) - XFADE * (len(ns) - 1)


def plan_timeline(cut: str) -> list[dict]:
    """The timeline a cut would have with the planned slot lengths (no clips needed): for cue sheets before
    anything is recorded. Teammate slots are left out until their clip exists."""
    out, start = [], 0.0
    for kind, name, secs, label, extra in SEGMENTS:
        n = INTRO_SECONDS if kind == "camera" else secs_for(secs, cut)
        if n <= 0 or (extra.get("slot") and not (CLIPS / f"{Path(name).stem}.marks.json").exists()):
            continue
        out.append({"label": label, "start": round(start, 2), "end": round(start + n, 2)})
        start += n - XFADE
    return out


def proof_pill() -> str:
    f = facts()
    return f"Plus {f['backend']:,} backend and {f['e2e']:,} browser tests passing, none failing"


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
    # the three cuts: ordered, main under the 8:00 of the 5 to 8 minute range even with every slot filled
    t = {c: nominal_total(c, True) for c in CUTS}
    assert t["short"] < t["main"] < t["full"], t
    assert 330 <= t["short"] <= 400 and t["main"] < 480, t
    order = [Path(n).stem for _, n, _, _, _ in SEGMENTS]
    # the 16 Oct spine, in its order: [3] 04, [4] 06, [5]+[7] 02, [6] 03, [9] 05 08, [10] 12, [11] 10, [12] 13, [13] 14, [14] 11 16
    spine = ["04_source_live", "06_graph", "02_compare_amy_rachel", "03_ken_identical", "05_journey", "08_audit",
             "workbuddy_mcp_panel", "10_engine", "13_hy3_status", "14_proof", "11_codebuddy", "16_close"]
    assert [order.index(s) for s in spine] == sorted(order.index(s) for s in spine), "the 16 Oct spine is out of order"
    # the script: every scene of the plan has a section; cuts add lines, never remove; TODO placeholders are never read
    TODO_SCENES.clear()
    lines = {c: scenes_from_script(False, c) for c in CUTS}
    for _, _, _, label, _ in SEGMENTS[1:]:
        sc = label.split()[0]
        assert sc in lines["main"], f"no '### {sc} ·' section in VIDEO_SCRIPT_FINALS.md"
        assert len(lines["short"][sc]) <= len(lines["main"][sc]) <= len(lines["full"][sc]), sc
        assert not any(x.startswith("TODO") for c in CUTS for x in lines[c][sc]), sc
    assert {"S1", "S2", "S3"} <= TODO_SCENES and not any(lines["full"][x] for x in ("S1", "S2", "S3"))
    assert lines["short"]["02"] != lines["full"]["02"]
    assert not any(secs_for(sec, "short") for _, _, sec, _, e in SEGMENTS if e.get("slot"))
    problems = check_script_numbers()                                  # the spoken numbers vs eval_summary.json
    assert not problems, "script vs evidence: " + "; ".join(problems)
    assert words(1131) == "one thousand one hundred and thirty-one" and words(7374) ==         "seven thousand three hundred and seventy-four" and words(166) == "one hundred and sixty-six"
    print(f"self-test ok: {len(cases)} bad takes refused, the good take accepted; "
          f"the script's numbers match eval_summary.json ({proof_pill()}); cuts "
          + ", ".join(f"{c} {int(t[c] // 60)}:{int(t[c] % 60):02d}" for c in CUTS))
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
TODO_SCENES: set[str] = set()


def scenes_from_script(hy3: bool, cut: str = "main") -> dict[str, list[str]]:
    """Narration per scene from VIDEO_SCRIPT_FINALS.md ('### NN · Title' then '> ' lines), one entry per
    sentence. '[ONLY IF HY3 LIVE]' (old spelling '[if Hy3 is on]') / '[otherwise]' lines are picked by --hy3;
    '[MAIN]' lines are read in the main and full cuts, '[FULL]' lines in the full cut only; a line that starts
    with TODO is a placeholder (the teammates' slots) and is never read (the scene is noted in TODO_SCENES)."""
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
            if text.startswith("TODO"):
                TODO_SCENES.add(cur)
                continue
            if "[MAIN]" in text and cut == "short":
                continue
            if "[FULL]" in text and cut != "full":
                continue
            if ("[ONLY IF HY3 LIVE]" in text or "[if Hy3 is on]" in text) and not hy3:
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


def write_cues(timeline: list[dict], hy3: bool, cut: str | None = None, planned: bool = False) -> list[dict]:
    """narration_cues.md and narration.srt from the script and the real timeline; returns the cues."""
    for problem in check_script_numbers():
        print("TRUTH CHECK:", problem)
    cut = cut or CUT
    lines, spans = scenes_from_script(hy3, cut), scene_spans(timeline)
    for sc in sorted(TODO_SCENES & set(spans)):
        print(f"TODO: scene {sc} is in this cut but its lines in VIDEO_SCRIPT_FINALS.md are still a placeholder")
    total = timeline[-1]["end"] if timeline else 0
    srt, cues, warn, n = [], [], [], 1
    md = [f"# Voice-over cue sheet (finals video, {cut} cut)", "",
          ("**PLANNED timings** from the nominal slot lengths in `assemble_finals.py`; the real ones are written when "
           "the picture is assembled from the new takes. " if planned else "")
          + f"Picture: `{SILENT.name}` ({stamp(total, False)}). Play it with "
          f"`narration{SUF}.srt` loaded as subtitles (VLC: Subtitle → Add Subtitle File) and read each line as it "
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
    (V / f"narration{SUF}.srt").write_text("\n".join(srt), encoding="utf-8")
    (V / f"narration_cues{SUF}.md").write_text("\n".join(md), encoding="utf-8")
    print(f"{n - 1} cues ({cut} cut) -> {V / f'narration_cues{SUF}.md'} and narration{SUF}.srt")
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
    ap.add_argument("--plan", action="store_true",
                    help="with --cues-only: use the planned slot lengths instead of timeline.json (nothing recorded yet)")
    ap.add_argument("--cut", choices=CUTS, default="main",
                    help="short (about 6:00), main (about 7:55, default) or full (about 9:15), from the same takes")
    ap.add_argument("--no-intro", action="store_true", help="leave the camera intro out")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--self-test", action="store_true", help="check the take checks on made-up takes, then stop")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    set_cut(args.cut)
    if args.cues_only:
        if args.plan:
            write_cues(plan_timeline(args.cut), args.hy3, planned=True)
        else:
            write_cues(json.loads((V / f"timeline{SUF}.json").read_text(encoding="utf-8")), args.hy3)
        return 0

    # every clip's current take is checked before anything is rendered (Codex round 2b #3)
    takes: dict[str, tuple[Path, list[dict]] | None] = {}
    refused, unusable = [], set()
    for kind, name, secs, label, extra in SEGMENTS:
        if kind != "clip" or secs_for(secs) <= 0:
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
        if kind != "camera" and secs_for(secs) <= 0:
            continue                                   # not in this cut
        secs = secs_for(secs)
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
            still(src, secs, out, work, [(a, d, proof_pill() if t == "PROOF_PILL" else t)
                                         for a, d, t in extra.get("pills", ())])
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
    (V / f"timeline{SUF}.json").write_text(json.dumps(timeline, indent=1), encoding="utf-8")
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
