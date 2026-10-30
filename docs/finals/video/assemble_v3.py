"""Assemble the finals video, plan v4 (VIDEO_PLAN_V4_FINALS.md; the narration is NARRATION_SCRIPT_V5.md, final).

    python docs/finals/video/assemble_v3.py --self-test      everything that needs no media (no ffmpeg run)
    python docs/finals/video/assemble_v3.py --cues           the speakers' cue sheet (planned timings) -> <v3>/
    python docs/finals/video/assemble_v3.py --cues --to-docs ... and docs/finals/video/CUE_SHEET_V4.md
    python docs/finals/video/assemble_v3.py --preview        a full PREVIEW cut with the placeholder narration
                                                             (<v3>/narration_placeholder); missing takes are cards
    python docs/finals/video/assemble_v3.py                  the main cut: real narration (<v3>/narration), every
                                                             take checked; refuses anything missing or unchecked

(Use a Python with Pillow, e.g. ``databridge\\.venv\\Scripts\\python``; ffmpeg and ffprobe on PATH.)

Everything it reads and writes lives in ``<v3>`` = ``$WALNUT_V3_DIR`` or ``<repo>/submission/finals_video/v3``
(git-ignored): no media, take, answer or render is ever committed.

Running order (plan v4) and inputs, all in <v3> unless given on the command line:

| part | what | input | sound |
|---|---|---|---|
| C1 | intro, Aaruni on camera | ``C1_intro.mov`` (848x464: centred over a blurred, scaled copy of itself) | own, -16 LUFS |
| C2 | the AI film (optional: left out, no placeholder, if missing) | ``C2_ai_film.mp4``/``.mov`` | own, -16 LUFS |
| C3 | the feature slides | ``--slides`` (1918x1080: padded; ``--mask-topbar`` crops the top bar) | own, -16 LUFS |
| T1 | card "Company A · a fictional Singapore fintech · every record is synthetic" | drawn here | none |
| C4 | Atlas: Dev's film, label blurred | ``work/atlas_clean.mp4`` from atlas_film.py (verified) | its own voice-over |
| T2 | hinge card "It knows everything about the project. Does it know who's asking?" | drawn here | none |
| C5 | The Call, recorded by the team | ``work/call_clean.mp4`` from atlas_film.py --film call_film.mp4 | its own (A_C5 only if silent) |
| C6a | the company brain | takes C6_1 ... C6_5b (record_v3.cjs) | narration B_C6a |
| C6b | built with Tencent | CodeBuddy card, the live WorkBuddy capture (``C6_workbuddy.*``), Miora, deploy | A_C6b |
| C6c | the proof | take C6_7_proof | narration B_C6c |
| C7 | close card "Walnut DataBridge · Answers you can prove." | drawn here | A_C7 |

The team's films (C4, C5) are used exactly as recorded, sound and timing untouched (founder's order, 30 Oct).

Narration: one file per block in ``<v3>/narration/`` (``B_C6a``, ``A_C6b``, ``B_C6c``, ``A_C7``; ``A_C5`` only if
the Call film has no sound;
``.wav``, ``.m4a``, ``.mp3``, ``.aac``, ``.flac`` or ``.ogg``), cleaned (high-pass, -16 LUFS). ``--preview`` uses
``<v3>/narration_placeholder/`` instead (and B_C5 for A_C5). Each block's paragraphs are found in the audio
(the pauses nearest to where the words say they should be), and the picture is fitted to them: each
paragraph's screen (a take's ``seg`` mark, or an anchor in the films) starts as its paragraph is read, the
footage between two anchors sped up or slowed down within limits, then held or trimmed. Waits a take
marked are cut first, keeping about a second of "thinking". Answers are never cut.

Takes: only a scene's current good take is used (``clips/<scene>.marks.json``, written by record_v3.cjs only
after a clean take), and it is checked again here: assemble_finals.check_take() plus every expected-state check
of the take passed. Captions: each take's labels (lower left), and paragraph captions on the films.
Outputs, in ``<v3>/out/``: the video (1080p30, H.264/AAC), ``timeline.json``, ``subtitles.srt`` (the narration,
plus the slides' own words) and ``cue_sheet.md`` (real timings). The main cut must be <= 8:00.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
from assemble_finals import (EVAL_SUMMARY, FIT, FPS, H, W, TakeError, _pills, check_take, duration,  # noqa: E402
                             norm, placeholder, run, still, words)

V3 = Path(os.environ.get("WALNUT_V3_DIR") or REPO / "submission" / "finals_video" / "v3")
SCRIPT = HERE / "NARRATION_SCRIPT_V5.md"
SLIDES_TEXT = HERE / "VIDEO_PLAN_V3_FINALS.md"            # the slides' own words (C3), for the subtitles
CONSTANTS = HERE / "video_constants.json"
DESKTOP = Path.home() / "OneDrive" / "Desktop"
DEFAULT_SLIDES = DESKTOP / "Screen Recording 2026-10-30 210819.mp4"
DEFAULT_INTRO = DESKTOP / "IMG_3819.MOV"
AUDIO_EXT = (".wav", ".m4a", ".mp3", ".aac", ".flac", ".ogg")
FILM_EXT = (".mp4", ".mov", ".mkv", ".webm", ".m4v")
MAX_MAIN = 480.0                                           # 8:00
LEAD, TAIL = 0.6, 0.9                                      # silence before / after a narration block
VIDEO_LEADS = 0.25                                         # a paragraph's screen starts this much before its words
HUMAN = 0.88                                               # a person reads ~12% faster than the placeholder voice
WPS = 2.4

CARD_COMPANY = ("Company A", "a fictional Singapore fintech · every record is synthetic")
CARD_HINGE = ("It found the project context.", "Can it keep a secret?")
CARD_CLOSE = ("Walnut DataBridge", "Answers you can prove")
PILL_TENCENT = "Hunyuan via TokenHub: wired, not live yet · Tencent Cloud deploy kit: no live instance yet"

# ---- the parts --------------------------------------------------------------------------------------
# narrated parts: block = the NARRATION_SCRIPT_V5.md block, audio = its file name. clips = (scene, optional):
# takes carry their own seg marks (paragraph numbers). lo / hi: how far a take may be slowed / sped up
# between two anchors (beyond: hold / trim). The team's films are never re-timed (team_film).
PARTS = [
    {"id": "C1", "kind": "camera"},
    {"id": "C2", "kind": "film", "optional": True},
    {"id": "C3", "kind": "slides"},
    {"id": "T1", "kind": "card", "card": CARD_COMPANY, "png": "T1_company", "seconds": 3.5},
    # C4 and C5: the team's films, with their OWN voice-over, exactly as recorded (founder's order, 30 Oct): not
    # cut, re-timed or re-voiced; only the model label blurred and the frame scaled (atlas_film.py). A_C5 is laid
    # under the Call film only if that film has no sound at all.
    {"id": "C4", "kind": "team_film", "film": "atlas", "block": "C4", "plan_seconds": 89.2},
    {"id": "T2", "kind": "card", "card": CARD_HINGE, "png": "T2_hinge", "seconds": 4.0},
    # The Call arrived as two clips, Amy then Rachel, each with its own voice (founder, 22:31)
    {"id": "C5a", "kind": "team_film", "film": "call_amy", "block": "C5", "plan_seconds": 52.3},
    {"id": "C5b", "kind": "team_film", "film": "call_rachel", "block": "C5", "plan_seconds": 31.7},
    {"id": "C6a", "kind": "narrated", "block": "C6a", "audio": "B_C6a", "lo": 0.8, "hi": 1.8,
     "clips": [("C6_1_graph", False), ("C6_2_ken", False), ("C6_3_journey", False), ("C6_4_audit", False),
               ("C6_5_engine", False), ("C6_5b_platform", True)]},
    {"id": "C6b", "kind": "stills", "block": "C6b", "audio": "A_C6b",
     # (first sentence of the block it covers, what is on screen, its caption): the live WorkBuddy session
     # (sentences 1-2; the 15 Oct still if there is no capture yet), CodeBuddy, Miora, Hunyuan + Tencent Cloud
     "stills": [(0, "film:C6_workbuddy", "Tencent WorkBuddy · calling Walnut through MCP"),
                (2, "card:11_codebuddy.png", ""), (3, "card:20_miora.png", ""), (4, "card:13b_deploy.png", PILL_TENCENT)]},
    {"id": "C6c", "kind": "narrated", "block": "C6c", "audio": "B_C6c", "lo": 0.7, "hi": 1.6, "clips": [("C6_7_proof", False)]},
    {"id": "C7", "kind": "card", "card": CARD_CLOSE, "png": "C7_close", "block": "C7", "audio": "A_C7"},
]
PREVIEW_ALIAS = {"A_C5": ["B_C5"]}                         # the placeholder voice has The Call as B_C5
BANNED_SPOKEN = ("str has been filed", "11 of 37", "by phone", "runs on tencent hunyuan", "hy3 answered",
                 "saved", "achieved", "native email", "native whatsapp", "pandan")


# ---- the script --------------------------------------------------------------------------------------
def blocks(path: Path = SCRIPT) -> dict[str, list[str]]:
    """'### B_C4: ...' (v5; v4: '### Block C4: ...') sections -> their paragraphs (the '> ' lines, joined; '·' pauses and [n] tags out)."""
    out: dict[str, list[str]] = {}
    cur, para = None, []
    for line in path.read_text(encoding="utf-8").splitlines() + ["## end"]:
        m = re.match(r"^###\s+(?:Block\s+)?(?:[AB]_)?(\w+):", line)
        if m or line.startswith("## ") or (line.startswith("### ") and not m):
            if cur and para:
                out.setdefault(cur, []).append(" ".join(para))
            cur, para = (m.group(1) if m and "moved" not in line else None), []
            if cur:
                out[cur] = []
            continue
        if not cur:
            continue
        if line.startswith(">"):
            t = line[1:].strip()
            if not t:
                if para:
                    out[cur].append(" ".join(para))
                para = []
            else:
                para.append(t)
    return {k: [clean(p) for p in v if clean(p)] for k, v in out.items() if v}


def clean(p: str) -> str:
    p = re.sub(r"\*\*\[\d+\]\*\*\s*", "", p).replace("·", "").replace("*", "")
    return re.sub(r"\s+", " ", re.sub(r"\s+([,.:;?!])", r"\1", p)).strip()


def sentences(p: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", p) if s]


def slide_lines() -> list[str]:
    """The slides' own words (C3, from VIDEO_PLAN_V3_FINALS.md), for the subtitles."""
    text = SLIDES_TEXT.read_text(encoding="utf-8")
    sec = text[text.index("### C3"):text.index("### C4")]
    out, cur = [], []
    for line in sec.splitlines():
        if line.startswith(">"):
            cur.append(line[1:].strip())
        elif cur:
            out.append(" ".join(cur))
            cur = []
    return [s for p in out for s in sentences(clean(p))]


def nwords(s: str) -> int:
    return len(s.split())


# ---- narration files and paragraph timing ------------------------------------------------------------
def narration_file(name: str, preview: bool, root: Path | None = None) -> Path | None:
    root = root or V3
    dirs = [root / "narration"] + ([root / "narration_placeholder"] if preview else [])
    for d in dirs:
        for n in [name] + (PREVIEW_ALIAS.get(name, []) if preview else []):
            for ext in AUDIO_EXT:
                if (d / f"{n}{ext}").exists():
                    return d / f"{n}{ext}"
    return None


def silences(path: Path) -> list[tuple[float, float]]:
    err = run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "highpass=f=100,silencedetect=noise=-38dB:d=0.3",
               "-f", "null", "-"])
    s = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    e = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    return list(zip(s, e))


def para_times(total: float, paras: list[str], sil: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Each paragraph's (start, end) in the narration file: speech is spread by word count, then each boundary
    snaps to the middle of the nearest pause within 2.5 s. Leading/trailing silence is speech-free."""
    a = sil[0][1] if sil and sil[0][0] < 0.05 else 0.0
    b = sil[-1][0] if sil and sil[-1][1] >= total - 0.05 and sil[-1][0] > a else total
    w = [nwords(p) for p in paras]
    cum, bounds = 0, [a]
    mids = [(x + y) / 2 for x, y in sil if x > a + 0.2 and y < b - 0.2]
    for k in w[:-1]:
        cum += k
        guess = a + (b - a) * cum / sum(w)
        near = min(mids, key=lambda m: abs(m - guess), default=None)
        bounds.append(near if near is not None and abs(near - guess) <= 2.5 and near > bounds[-1] + 0.3 else guess)
    bounds.append(b)
    return [(round(bounds[i], 2), round(bounds[i + 1], 2)) for i in range(len(paras))]


# ---- fitting footage to the narration ------------------------------------------------------------------
def fit_segments(anchors: list[tuple[float, float]], kept_total: float, targets: dict[float, float], total: float,
                 lo: float, hi: float) -> list[dict]:
    """anchors: (paragraph, second of the joined footage); targets: paragraph -> second of the part.
    Returns the pieces [g0, g1) of footage -> [t0, t1) of the part with their speed, hold and trim."""
    pts = [(0.0, 0.0)]
    for p, g in sorted(anchors):
        t = targets.get(p)
        if t is None or g <= pts[-1][0] + 0.05 or t <= pts[-1][1] + 0.3 or g >= kept_total - 0.05 or t >= total - 0.3:
            continue                                         # out of order or too close: the neighbours cover it
        pts.append((g, t))
    pts.append((kept_total, total))
    out = []
    for (g0, t0), (g1, t1) in zip(pts, pts[1:]):
        need, have = t1 - t0, g1 - g0
        speed = min(hi, max(lo, have / need)) if need > 0 else hi
        shown = min(have, need * speed)
        out.append({"g0": round(g0, 3), "g1": round(g0 + shown, 3), "t0": round(t0, 3), "t1": round(t1, 3),
                    "speed": round(speed, 4), "hold": round(max(0.0, need - shown / speed), 3),
                    "trim": round(have - shown, 3)})
    return out


def map_time(g: float, segs: list[dict]) -> float | None:
    """A second of the joined footage -> the second of the part it lands on (None: trimmed away)."""
    for s in segs:
        if s["g0"] <= g < s["g1"] + 1e-6:
            return s["t0"] + (g - s["g0"]) / s["speed"]
    return None


def shift_cuts(at: float, cuts: list[tuple[float, float]]) -> float:
    gone = 0.0
    for a, b in sorted(cuts):
        if at >= b:
            gone += b - a
        elif at > a:
            gone += at - a
    return at - gone


# ---- takes -------------------------------------------------------------------------------------------
def v3_take(clips: Path, scene: str) -> tuple[Path, dict] | None:
    """The scene's current good take, checked again: assemble_finals.check_take + every expected check passed."""
    pointer = clips / f"{scene}.marks.json"
    if not pointer.exists():
        return None
    try:
        t = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise TakeError(f"{pointer.name}: unreadable ({e})") from e
    bad = check_take(t, clips, scene) + check_problems(t.get("marks") or [])
    if bad:
        raise TakeError(f"{pointer.name}: " + "; ".join(bad))
    return clips / t["video"], t


def check_problems(marks: list) -> list[str]:
    want = next((m["expect_checks"] for m in marks if isinstance(m, dict) and "expect_checks" in m), None)
    if not want:
        return ["the take lists no expected checks (not a record_v3.cjs take)"]
    got: dict[str, bool] = {}
    for m in marks:
        if isinstance(m, dict) and "check" in m:
            got[m["check"]] = got.get(m["check"], True) and m.get("ok") is True
    return [f"check {c} never ran" for c in want if c not in got] + [f"check {c} failed" for c, ok in got.items() if not ok]


# ---- drawing: cards --------------------------------------------------------------------------------
def _font(size: int, bold=True):
    from PIL import ImageFont
    for name in (("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def card_png(lines: tuple[str, str], out: Path, name: str | None = None) -> Path:
    """The team's card <v3>/work/cards/<name>.png when it exists (the main session draws them); else a plain dark
    card drawn here: a big first line, a quieter second line, the Walnut DataBridge mark at the top."""
    if name and (V3 / "work" / "cards" / f"{name}.png").exists():
        return V3 / "work" / "cards" / f"{name}.png"
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), (10, 11, 13))
    d = ImageDraw.Draw(img)
    d.text((150, 110), "Walnut DataBridge", font=_font(34), fill=(237, 237, 239))
    big, small = _font(92), _font(46, bold=False)
    y = H // 2 - 90
    for text, f, col in ((lines[0], big, (237, 237, 239)), (lines[1], small, (161, 161, 170))):
        tw = d.textlength(text, font=f)
        if tw > W - 300:                                    # never wider than the frame
            f = _font(int(f.size * (W - 300) / tw), bold=f is big)
            tw = d.textlength(text, font=f)
        d.text(((W - tw) / 2, y), text, font=f, fill=col)
        y += int(f.size * 1.6)
    img.save(out)
    return out


# ---- filters -----------------------------------------------------------------------------------------
def blurfill(fg_h: int) -> str:
    """A small frame centred on 1920x1080 over a blurred, scaled-up copy of itself (never stretched)."""
    return (f"[0:v]split[bg][fg];[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=30,"
            f"eq=brightness=-0.08[b];[fg]scale=-2:{fg_h}:flags=lanczos[f];[b][f]overlay=(W-w)/2:(H-h)/2,fps={FPS},"
            "format=yuv420p[v]")


def slides_vf(mask_topbar: bool) -> str:
    crop = "crop=iw-108:ih-60:54:60," if mask_topbar else ""          # the top bar out, the aspect kept
    return f"[0:v]{crop}scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0a0b0d,fps={FPS},format=yuv420p[v]"


AUDIO_NORM = "highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000"
ENC = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS),
       "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def has_audio(p: Path) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of",
                          "csv=p=0", str(p)], capture_output=True, text=True).stdout
    return bool(out.strip())


def own_sound(src: Path, vf: str, out: Path, length: float | None = None) -> float:
    a = (f";[0:a]{AUDIO_NORM}[a]" if has_audio(src) else f";anullsrc=r=48000:cl=stereo[a]")
    t = ["-t", f"{length:.2f}"] if length else ["-shortest"] if not has_audio(src) else []
    run(["ffmpeg", "-y", "-i", str(src), "-filter_complex", vf + a, "-map", "[v]", "-map", "[a]", *ENC, *t, str(out)])
    return duration(out)


def silent_card(lines, seconds: float, out: Path, work: Path, name: str | None = None) -> float:
    png = card_png(lines, work / f"{out.stem}.png", name)
    v = work / f"{out.stem}_v.mp4"
    still(png, seconds, v, work)
    run(["ffmpeg", "-y", "-i", str(v), "-f", "lavfi", "-t", f"{seconds:.2f}", "-i", "anullsrc=r=48000:cl=stereo",
         "-map", "0:v", "-map", "1:a", *ENC, "-t", f"{seconds:.2f}", str(out)])
    return seconds


def with_narration(video: Path, audio: Path | None, total: float, out: Path) -> None:
    if audio is None:
        run(["ffmpeg", "-y", "-i", str(video), "-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=48000:cl=stereo",
             "-map", "0:v", "-map", "1:a", *ENC, "-t", f"{total:.2f}", str(out)])
        return
    run(["ffmpeg", "-y", "-i", str(video), "-i", str(audio), "-filter_complex",
         f"[1:a]{AUDIO_NORM},adelay={int(LEAD * 1000)}:all=1,apad[a]", "-map", "0:v", "-map", "[a]", *ENC,
         "-t", f"{total:.2f}", str(out)])


def team_film(part: dict, clean: Path, out: Path, work: Path, preview: bool) -> tuple[float, str]:
    """A cleaned team film, its own sound as it is (atlas_film.py already levelled it): re-encoded to the video's
    format only (48 kHz stereo AAC, the same H.264 settings), never filtered, cut or re-timed. Its length must
    stay the clean file's (+-0.05 s)."""
    src = duration(clean)
    if has_audio(clean):
        run(["ffmpeg", "-y", "-i", str(clean), "-map", "0:v", "-map", "0:a", *ENC, str(out)])
        note = "own voice, as recorded"
    else:
        audio = narration_file(part.get("fallback_audio", ""), preview) if part.get("fallback_audio") else None
        if audio is None and not preview:
            raise SystemExit(f"REFUSED {part['id']}: the film has no sound and there is no {part.get('fallback_audio')}")
        total = max(src, (LEAD + duration(audio) + TAIL) if audio else src)
        v = work / f"{part['id']}_held.mp4"
        run(["ffmpeg", "-y", "-i", str(clean), "-vf", f"tpad=stop_mode=clone:stop_duration={total - src + 0.1:.2f}",
             "-an", "-c:v", "libx264", "-crf", "17", "-t", f"{total:.2f}", str(v)])
        with_narration(v, audio, total, out)
        note = f"no sound in the film: {audio.name if audio else 'silent'} laid under it"
    got = duration(out)
    if has_audio(clean) and abs(got - src) > 0.05:
        raise SystemExit(f"REFUSED {part['id']}: re-encoding changed the length ({got:.3f}s vs {src:.3f}s)")
    return got, note


# ---- a narrated part -----------------------------------------------------------------------------
def film_source(part: dict, args) -> tuple[Path | None, list[tuple[float, float]], list]:
    """(clean film, anchors in clean seconds, problems) for C4 / C5."""
    name = part["film"]
    clean = V3 / "work" / f"{name}_clean.mp4"
    report = V3 / "work" / f"{name}_film.json"
    if not clean.exists():
        raw = [V3 / f"{n}{e}" for n in (f"{name}_film", name) for e in FILM_EXT if (V3 / f"{n}{e}").exists()]
        why = (f"{raw[0].name} is not cleaned yet: python atlas_film.py --film {raw[0]} --out {clean}" if raw
               else f"no {name} film in {V3}")
        return None, [], [why]
    info = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
    if not (info.get("verify") or {}).get("ok"):
        return None, [], [f"{report.name}: the label blur is not verified (run atlas_film.py again)"]
    if info.get("cuts"):
        return None, [], [f"{report.name}: the film was cut; the founder's order is no cuts (run atlas_film.py again)"]
    return clean, [], []


def narrated(part: dict, args, work: Path, out: Path, preview: bool) -> tuple[float, str, list[dict]]:
    paras = blocks()[part["block"]]
    audio = narration_file(part["audio"], preview)
    if audio is None and not preview:
        raise SystemExit(f"REFUSED: no narration {part['audio']} in {V3 / 'narration'}")
    speech = duration(audio) if audio else sum(map(nwords, paras)) / WPS
    ptimes = para_times(speech, paras, silences(audio) if audio else [])
    total = LEAD + speech + TAIL
    targets = {float(i + 1): max(0.0, LEAD + a - VIDEO_LEADS) for i, (a, _) in enumerate(ptimes)}
    for i, (a, b) in enumerate(ptimes):                      # a fractional anchor (5.5): the middle of its paragraph
        targets[i + 1.5] = LEAD + (a + b) / 2
    sources: list[tuple[Path, float, list, list]] = []      # (kept video, kept length, anchors, pills)
    notes = []
    if part.get("film"):
        clean, anchors, bad = film_source(part, args)
        if bad:
            if not preview:
                raise SystemExit(f"REFUSED {part['id']}: {bad[0]}")
            notes.append(bad[0])
        else:
            sources.append((clean, duration(clean), anchors, []))
    for scene, optional in part.get("clips", []):
        try:
            take = v3_take(V3 / "clips", scene)
        except TakeError as e:
            if optional:
                notes.append(f"{scene} left out: {e}")
                continue
            if not preview:
                raise SystemExit(f"REFUSED {part['id']}: {e}")
            notes.append(str(e))
            take = None
        if take is None:
            if optional:
                continue
            if not preview:
                raise SystemExit(f"REFUSED {part['id']}: {scene} has no good take; record it with record_v3.cjs")
            notes.append(f"{scene} MISSING")
            png = placeholder(part["id"], scene, work / f"{scene}_missing.png")
            ph = work / f"{scene}_missing.mp4"
            still(png, 8, ph, work)
            seg = next(float(p) for p in ([s for s, o in part["clips"] if s == scene] and [1]))
            sources.append((ph, 8.0, [(seg, 0.0)], []))
            continue
        src, t = take
        marks = t["marks"]
        cuts = sorted([tuple(m["cut"]) for m in marks if "cut" in m and m["cut"][1] - m["cut"][0] > 0.1]
                      + [(0.0, next(m["start"] for m in marks if "start" in m))])
        kept = work / f"{scene}_kept.mp4"
        drop = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in cuts)
        run(["ffmpeg", "-y", "-i", str(src), "-filter_complex",
             f"[0:v]fps={FPS},select='not({drop})',setpts=N/{FPS}/TB,{FIT}[v]", "-map", "[v]", "-an",
             "-c:v", "libx264", "-preset", "medium", "-crf", "16", str(kept)])
        anchors = [(float(m["seg"]), shift_cuts(m["at"], cuts)) for m in marks if "seg" in m]
        pills = [{**m, "at": shift_cuts(m["at"], cuts)} for m in marks if "text" in m]
        sources.append((kept, duration(kept), anchors, pills))
    if not sources:
        raise SystemExit(f"REFUSED {part['id']}: no footage")
    # join the footage, then fit it to the paragraphs
    offs, g, anchors, pills = [], 0.0, [], []
    for src, length, an, pl in sources:
        offs.append(g)
        anchors += [(p, g + a) for p, a in an]
        pills += [{**m, "at": g + m["at"]} for m in pl]
        g += length
    segs = fit_segments(anchors, g, targets, total, part["lo"], part["hi"])
    # one trim per (segment x source) overlap, each at its segment's speed; the hold is the last frame
    inputs, chain, labels = [], [], []
    for i, (src, *_rest) in enumerate(sources):
        inputs += ["-i", str(src)]
    k = 0
    for s in segs:
        parts_ = []
        for i, (src, length, *_r) in enumerate(sources):
            a, b = max(s["g0"], offs[i]), min(s["g1"], offs[i] + length)
            if b - a > 1 / FPS:
                parts_.append((i, a - offs[i], b - offs[i]))
        for j, (i, a, b) in enumerate(parts_):
            hold = s["hold"] if j == len(parts_) - 1 else 0
            chain.append(f"[{i}:v]trim=start={a:.3f}:end={b:.3f},setpts=(PTS-STARTPTS)/{s['speed']:.4f},fps={FPS}"
                         + (f",tpad=stop_mode=clone:stop_duration={hold:.3f}" if hold > 0.01 else "") + f"[p{k}]")
            labels.append(f"[p{k}]")
            k += 1
        if not parts_ and s["t1"] - s["t0"] > 0.01:          # nothing to show: hold the previous frame
            chain.append(f"color=c=0x0a0b0d:s={W}x{H}:r={FPS}:d={s['t1'] - s['t0']:.3f}[p{k}]")
            labels.append(f"[p{k}]")
            k += 1
    chain.append("".join(labels) + f"concat=n={k}:v=1:a=0,format=yuv420p[v0]")
    # captions: the takes' labels, and the part's paragraph captions (the films)
    marks = [{"at": t, "seconds": m.get("seconds", 3.5), "text": m["text"]} for m in pills
             if (t := map_time(m["at"], segs)) is not None]
    marks += [{"at": targets[float(p)] + 0.4, "seconds": 4.0, "text": txt} for p, txt in part.get("captions", [])
              if float(p) in targets]
    extra, pill_chain, last, n = _pills(sorted(marks, key=lambda m: m["at"]), total, len(sources), work, part["id"], "v0")
    video = work / f"{part['id']}_video.mp4"
    run(["ffmpeg", "-y", *inputs, *extra, "-filter_complex", ";".join(chain) + pill_chain, "-map", f"[{last}]",
         "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-r", str(FPS), "-t", f"{total:.2f}", str(video)])
    with_narration(video, audio, total, out)
    note = (f"narration {audio.name if audio else 'MISSING'} {speech:.1f}s; " + ", ".join(
        f"x{s['speed']:.2f}" + (f"+hold {s['hold']:.1f}" if s["hold"] > 0.05 else "") + (f"-trim {s['trim']:.1f}" if s["trim"] > 0.05 else "")
        for s in segs) + (f"; {n} captions" if n else "") + ("; " + "; ".join(notes) if notes else ""))
    cues = [{"part": part["id"], "para": i + 1, "start": LEAD + a, "end": LEAD + b, "text": paras[i]}
            for i, (a, b) in enumerate(ptimes)]
    return total, note, cues


def stills_part(part: dict, args, work: Path, out: Path, preview: bool) -> tuple[float, str, list[dict]]:
    paras = blocks()[part["block"]]
    sents = [s for p in paras for s in sentences(p)]
    audio = narration_file(part["audio"], preview)
    if audio is None and not preview:
        raise SystemExit(f"REFUSED: no narration {part['audio']}")
    speech = duration(audio) if audio else sum(map(nwords, sents)) / WPS
    total = LEAD + speech + TAIL
    w = [nwords(s) for s in sents]
    shares = []
    for idx, (sent, what, _cap) in enumerate(part["stills"]):
        nxt = part["stills"][idx + 1][0] if idx + 1 < len(part["stills"]) else len(sents)
        shares.append(sum(w[sent:nxt]) / sum(w))
    pieces, notes = [], []
    for (sent, what, cap), share in zip(part["stills"], shares):
        secs = round(total * share, 2)
        kind, name = what.split(":", 1)
        p = work / f"{part['id']}_{sent}.mp4"
        if kind == "film":
            film = next((V3 / f"{name}{e}" for e in FILM_EXT if (V3 / f"{name}{e}").exists()), None)
            if film:
                run(["ffmpeg", "-y", "-i", str(film), "-filter_complex", slides_vf(False).replace("[v]", ",tpad=stop_mode=clone:stop_duration=60[v]"),
                     "-map", "[v]", "-an", "-c:v", "libx264", "-crf", "17", "-t", f"{secs:.2f}", str(p)])
                pieces.append(p)
                notes.append(f"{name} (live capture)")
                continue
            name = "workbuddy_mcp_panel.png"                  # no live capture yet: the 15 Oct window capture
            notes.append("WorkBuddy: the 15 Oct still (no live capture yet)")
            src = REPO / "submission" / "02_workbuddy" / name
        else:
            src = next((d / name for d in (V3 / "work" / "cards", REPO / "submission" / "finals_video" / "cards")
                        if (d / name).exists()), REPO / "submission" / "finals_video" / "cards" / name)
        if not src.exists():
            if not preview:
                raise SystemExit(f"REFUSED C6b: {src} missing (node docs/finals/video/record_finals.cjs cards)")
            src = placeholder(part["id"], name, work / f"{name}_missing.png")
            notes.append(f"{name} MISSING")
        still(src, secs, p, work, [(0.6, max(1.0, secs - 1.2), cap)] if cap else ())
        pieces.append(p)
    lst = work / f"{part['id']}_list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in pieces), encoding="utf-8")
    video = work / f"{part['id']}_video.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c:v", "libx264", "-crf", "17", "-r", str(FPS),
         "-t", f"{total:.2f}", str(video)])
    with_narration(video, audio, total, out)
    cues, t = [], LEAD
    for s in sents:
        d = speech * nwords(s) / sum(w)
        cues.append({"part": part["id"], "para": 1, "start": t, "end": t + d, "text": s})
        t += d
    return total, "; ".join(notes) or "stills", cues


# ---- subtitles and the cue sheet -----------------------------------------------------------------
def stamp(sec: float, srt: bool = True) -> str:
    h, rem = divmod(max(0.0, sec), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)) % 1000:03d}" if srt else f"{int(m)}:{int(s):02d}"


def srt_from(cues: list[dict]) -> str:
    """One subtitle per sentence, timed inside its paragraph by word count."""
    out, n = [], 1
    for c in cues:
        ss = sentences(c["text"])
        w = sum(map(nwords, ss)) or 1
        t = c["start"]
        for s in ss:
            d = (c["end"] - c["start"]) * nwords(s) / w
            out += [str(n), f"{stamp(t)} --> {stamp(t + max(0.8, d) - 0.05)}", s, ""]
            n, t = n + 1, t + d
    return "\n".join(out)


SPEAKER = {"B": "Speaker B (Mehul or Dev)", "A": "Aaruni"}
SCREEN = {  # what is on screen under each block (VIDEO_PLAN_V4_FINALS.md), for the speakers
    "C4": "Dev's Atlas film: the Meet Alex card, the question, Writing…, the page beside the chat, the team, the blockers, the checklist",
    "C5": "the team's recording of The Call: Amy (script, sources, draft), then Rachel (her answer, Open as page, 37)",
    "C6a": "the map Rachel vs Ken · Ken's identical answers · the journey · Verify and the tamper demo · the platform",
    "C6b": "the live Tencent WorkBuddy session (the answer, then its audit row) · the CodeBuddy card · the Miora cover · the deploy kit",
    "C6c": "the proof slide", "C7": "the close card: Walnut DataBridge · Answers you can prove",
}


def planned(preview_root: Path | None = None) -> list[dict]:
    """The planned timeline: real lengths for C1/C3 (and C2 if present), the narration files' lengths (scaled to
    a human read for the placeholder voice), or words / 2.4 per second."""
    bl = blocks()
    out, t = [], 0.0
    fixed = {"C1": 10.1, "C2": 0.0, "C3": 79.8}
    ai = next((V3 / f"C2_ai_film{e}" for e in FILM_EXT if (V3 / f"C2_ai_film{e}").exists()), None)
    if ai:
        try:
            fixed["C2"] = duration(ai)
        except Exception:                                    # noqa: BLE001  (no ffprobe: keep the plan's 1:00)
            fixed["C2"] = 60.0
    for p in PARTS:
        if p["kind"] in ("camera", "film", "slides"):
            d = fixed[p["id"]]
        elif p["kind"] == "team_film":
            clean = V3 / "work" / f"{p['film']}_clean.mp4"
            try:
                d = duration(clean) if clean.exists() else p["plan_seconds"]
            except Exception:                                # noqa: BLE001
                d = p["plan_seconds"]
        elif p.get("audio"):
            f = narration_file(p["audio"], True, preview_root)
            try:
                # a placeholder under another name (B_C5 for A_C5) was read from an older text: use the words
                d = duration(f) * (HUMAN if "placeholder" in str(f) else 1.0) if f and f.stem == p["audio"] else None
            except Exception:                                # noqa: BLE001
                d = None
            d = (d or p.get("plan_seconds") or sum(nwords(x) for x in bl[p["block"]]) / WPS) + LEAD + TAIL
            d = max(d, p.get("seconds", 0))
        else:
            d = p["seconds"]
        if d:
            out.append({"part": p["id"], "start": round(t, 2), "end": round(t + d, 2)})
            t += d
    return out


def cue_sheet(timeline: list[dict], title: str) -> str:
    bl = blocks()
    span = {x["part"]: x for x in timeline}
    md = [f"# Voice-over cue sheet, finals video v4 ({title})", "",
          "Generated by `assemble_v3.py --cues` from `NARRATION_SCRIPT_V5.md`; the timings are "
          + ("PLANNED (placeholder voice lengths x0.88, or 2.4 words a second); the real ones come with the cut."
             if "plan" in title else "the real cut's."), "",
          "One file per block, named as below (`.wav`, `.m4a` or `.mp3`), 2 s of silence at the start, into "
          "`submission/finals_video/v3/narration/`. Read calmly; pause at the paragraph breaks: the picture "
          "is lined up with them."
          + ("" if any(x["part"] == "C2" for x in timeline) else " The AI film is not in yet: every time below moves "
             "later by its length."), "",
          "C4 (Atlas, Dev's film) and C5 (The Call, the team's recording) keep their own voice-over exactly as "
          "recorded: nothing to record for them (A_C5 only if the Call recording turns out to have no sound).", ""]
    for p in PARTS:
        if not p.get("audio") or p["id"] not in span:
            continue
        s = span[p["id"]]
        paras = bl[p["block"]]
        speech = s["end"] - s["start"] - LEAD - TAIL
        md += [f"## {p['audio']} · {SPEAKER[p['audio'][0]]} · {stamp(s['start'], False)}–{stamp(s['end'], False)} "
               f"(about {speech:.0f} s)", "", f"*On screen:* {SCREEN.get(p['block'], '')}", ""]
        w = [nwords(x) for x in paras]
        t = 0.0
        for i, x in enumerate(paras):
            md.append(f"{i + 1}. **+{t:4.1f} s** {x}")
            t += speech * w[i] / sum(w)
        md.append("")
    md += ["## Never say", "", "- That an STR has been filed, or \"11 of 37 on one device\".",
           "- That it runs on Tencent Hunyuan, or that Hy3 answered (it is wired for it).",
           "- That it saved time, or that the onboarding goal is achieved.",
           "- Native email or WhatsApp integration (they are imported archives); \"asking by phone\".",
           "- The name of any AI provider or model.", ""]
    return "\n".join(md)


# ---- self-test ---------------------------------------------------------------------------------------
def self_test() -> int:
    bl = blocks()
    want = {"C4": 8, "C5": 11, "C6a": 5, "C6c": 1, "C6b": 1, "C7": 1}
    assert {k: len(v) for k, v in bl.items()} == want, {k: len(v) for k, v in bl.items()}
    assert bl["C5"][0].startswith("So meet Amy") and bl["C5"][10].endswith("That's Walnut.")
    assert bl["C6a"][1].endswith("It hides that a secret exists.") and "*" not in bl["C6a"][1]
    assert "·" not in " ".join(sum(bl.values(), [])) and "[1]" not in bl["C5"][0]
    spoken = " ".join(sum(bl.values(), [])).lower()
    hit = [b for b in BANNED_SPOKEN if b in spoken]
    assert not hit, f"the narration says {hit}"
    assert spoken.count("company a") == 1, "Company A is named once, in Atlas's first line"
    # the numbers said are the evidence's
    e = json.loads(EVAL_SUMMARY.read_text(encoding="utf-8"))
    c = json.loads(CONSTANTS.read_text(encoding="utf-8"))
    text = norm(spoken)
    assert e["leaks"] == 0 and e["differential"]["mismatches"] == 0 and not e.get("stale")
    for n in (e["requests"], e["differential"]["pairs"], c["codebuddy_sessions"]):
        assert norm(words(n)) in text, f"the narration never says {words(n)}"
    # every part with narration has its block; names as the coordinator gave them
    assert [p["audio"] for p in PARTS if p.get("audio")] == ["B_C6a", "A_C6b", "B_C6c", "A_C7"]
    assert [p["id"] for p in PARTS if p["kind"] == "team_film"] == ["C4", "C5a", "C5b"]
    assert not any("captions" in p or "lo" in p for p in PARTS if p["kind"] == "team_film"), "the films are not re-timed"
    assert CARD_HINGE[1] == "Can it keep a secret?" and "not live yet" in PILL_TENCENT
    assert [p["id"] for p in PARTS] == ["C1", "C2", "C3", "T1", "C4", "T2", "C5a", "C5b", "C6a", "C6b", "C6c", "C7"]
    assert all(p["block"] in bl for p in PARTS if p.get("block"))
    assert "Pandan" not in json.dumps(PARTS) and CARD_COMPANY[0] == "Company A"
    # narration files: any of the extensions, the placeholder folder only for a preview
    d = Path(tempfile.mkdtemp(prefix="v3-assemble-"))
    try:
        (d / "narration").mkdir()
        (d / "narration_placeholder").mkdir()
        (d / "narration" / "B_C4.m4a").write_bytes(b"x")
        (d / "narration_placeholder" / "B_C5.wav").write_bytes(b"x")
        (d / "narration_placeholder" / "B_C6a.wav").write_bytes(b"x")
        assert narration_file("B_C4", False, d).name == "B_C4.m4a"
        assert narration_file("A_C5", False, d) is None and narration_file("A_C5", True, d).name == "B_C5.wav"
        assert narration_file("B_C6a", False, d) is None and narration_file("B_C6a", True, d) is not None
        # take checks: a good take passes; a failed or missing check, or a non-v3 take, is refused
        (d / "clips" / "takes" / "s" / "A").mkdir(parents=True)
        (d / "clips" / "takes" / "s" / "A" / "s.webm").write_bytes(b"\1" * 100)
        good = {"scene": "s", "take": "A", "ok": True, "duration": 20.0, "expected_labels": 1,
                "video": "takes/s/A/s.webm", "bytes": 100,
                "marks": [{"expect_checks": ["x", "y"]}, {"start": 1.0}, {"seg": 1, "at": 1.2}, {"check": "x", "ok": True},
                          {"check": "y", "ok": True}, {"at": 3, "seconds": 3, "text": "one"}, {"cut": [5, 9]}]}
        ptr = d / "clips" / "s.marks.json"
        for t, why in ((good, ""), ({**good, "marks": good["marks"][:4] + good["marks"][5:]}, "check y never ran"),
                       ({**good, "marks": good["marks"] + [{"check": "x", "ok": False}]}, "check x failed"),
                       ({**good, "marks": good["marks"][1:]}, "no expected checks"),
                       ({**good, "ok": False, "error": "boom"}, "the take failed")):
            ptr.write_text(json.dumps(t), encoding="utf-8")
            try:
                v3_take(d / "clips", "s")
                got = ""
            except TakeError as ex:
                got = str(ex)
            assert (why in got) if why else got == "", (why, got)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    # paragraphs found at the pauses; the fit: anchors land on their paragraphs, holds and trims within limits
    pt = para_times(20.0, ["a b c d", "e f g h", "i j"], [(0.0, 1.0), (8.1, 8.9), (15.2, 15.8), (19.5, 20.0)])
    assert pt[0][0] == 1.0 and pt[1][0] == 8.5 and pt[2][0] == 15.5 and pt[2][1] == 19.5, pt
    segs = fit_segments([(1, 0.0), (2, 10.0), (3, 30.0)], 40.0, {1.0: 0.5, 2.0: 8.0, 3.0: 20.0}, 30.0, 0.8, 1.8)
    assert [s["t0"] for s in segs] == [0.0, 8.0, 20.0] and segs[1]["speed"] == round(20 / 12, 4), segs
    assert abs(map_time(10.0, segs) - 8.0) < 1e-6 and segs[-1]["speed"] == 1.0
    slow = fit_segments([(2, 2.0)], 4.0, {2.0: 10.0}, 20.0, 0.8, 1.8)
    assert slow[0]["speed"] == 0.8 and slow[0]["hold"] > 0 and slow[1]["hold"] > 0, slow
    fast = fit_segments([(2, 30.0)], 31.0, {2.0: 5.0}, 6.0, 0.8, 2.0)
    assert fast[0]["speed"] == 2.0 and fast[0]["trim"] == 20.0 and map_time(25.0, fast) is None, fast
    assert abs(shift_cuts(50.0, [(35.8, 42.1), (70.3, 76.6)]) - (50.0 - 6.3)) < 1e-9
    # the timeline: under 8:00 with the placeholder lengths (as a human reads them) and the AI film at 1:00
    lengths = {"B_C4": 56.3, "A_C5": 60.8, "B_C6a": 69.8, "B_C6c": 11.7, "A_C6b": 25.9, "A_C7": 13.6}
    tot = 10.1 + 61 + 79.8 + 3.5 + 4.0 + 89.2 + 52.3 + 31.7 + sum(v * HUMAN + LEAD + TAIL for k, v in lengths.items()
                                                         if k not in ("B_C4", "A_C5"))
    assert tot < MAX_MAIN, tot
    # subtitles, cue sheet, filters
    srt = srt_from([{"start": 1.0, "end": 5.0, "text": "One two. Three four five six."}])
    assert srt.startswith("1\n00:00:01,000 --> ") and "\n2\n" in srt
    cs = cue_sheet(planned(), "planned")
    assert "## B_C6a" in cs and "## A_C7 · Aaruni" in cs and "## B_C4" not in cs and "Pandan" not in cs
    assert len(slide_lines()) >= 8
    assert "gblur" in blurfill(864) and "overlay=(W-w)/2:(H-h)/2" in blurfill(864)
    assert "crop=iw-108:ih-60:54:60" in slides_vf(True) and "crop" not in slides_vf(False)
    gi = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^/?submission/finals_video/v3/?$", gi, re.M), ".gitignore does not cover submission/finals_video/v3/"
    try:
        import PIL  # noqa: F401
        tmp = Path(tempfile.mkdtemp(prefix="v3-card-"))
        card_png(CARD_HINGE, tmp / "c.png")
        shutil.rmtree(tmp, ignore_errors=True)
        pil = "cards drawn"
    except ImportError:
        pil = "cards not drawn (no Pillow in this Python)"
    print(f"self-test ok: blocks {want}; the narration's numbers match eval_summary.json; take checks; paragraph "
          f"timing; the fit (speed, hold, trim); planned main cut {int(tot // 60)}:{int(tot % 60):02d} (<= 8:00); "
          f"srt, cue sheet, filters; {pil}")
    return 0


# ---- main --------------------------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--cues", action="store_true", help="write the cue sheet with planned timings")
    ap.add_argument("--to-docs", action="store_true", help="with --cues: also docs/finals/video/CUE_SHEET_V4.md")
    ap.add_argument("--preview", action="store_true", help="placeholder narration; missing takes become cards")
    ap.add_argument("--intro", type=Path, default=None, help=f"default <v3>/C1_intro.mov, else {DEFAULT_INTRO}")
    ap.add_argument("--ai-film", type=Path, default=None, help="default <v3>/C2_ai_film.* (left out if missing)")
    ap.add_argument("--slides", type=Path, default=None, help=f"default <v3>/C3_slides.*, else {DEFAULT_SLIDES}")
    ap.add_argument("--mask-topbar", action="store_true", help="C3: crop the top bar (cursor, focus ring) and re-scale")
    ap.add_argument("--intro-height", type=int, default=864, help="C1: the camera frame's height on the 1080 canvas")
    ap.add_argument("--burn-subs", action="store_true", help="burn the subtitles into the picture as well")
    ap.add_argument("--allow-long", action="store_true", help="do not refuse a main cut over 8:00")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.cues:
        md = cue_sheet(planned(), "planned timings")
        V3.mkdir(parents=True, exist_ok=True)
        (V3 / "cue_sheet_planned.md").write_text(md, encoding="utf-8")
        if a.to_docs:
            (HERE / "CUE_SHEET_V4.md").write_text(md, encoding="utf-8")
        print(md)
        return 0

    pick = lambda base, names: next((base / f"{n}{e}" for n in names for e in FILM_EXT  # noqa: E731
                                     if (base / f"{n}{e}").exists()), None)
    intro = a.intro or pick(V3, ["C1_intro"]) or DEFAULT_INTRO
    ai = a.ai_film or pick(V3, ["C2_ai_film"])                # never C2_film.mp4: that is Dev's Atlas film
    slides = a.slides or pick(V3, ["C3_slides"]) or DEFAULT_SLIDES
    outdir = V3 / "out"
    outdir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="v3video-", dir=V3))
    parts, cues, timeline, t = [], [], [], 0.0
    try:
        for p in PARTS:
            out = work / f"{len(parts):02d}_{p['id']}.mp4"
            if p["kind"] == "camera":
                if not intro.exists():
                    raise SystemExit(f"REFUSED: no intro at {intro}")
                length, note = own_sound(intro, blurfill(a.intro_height), out), f"{intro.name}, own sound"
            elif p["kind"] == "film":
                if not ai or not ai.exists():
                    print("skip  C2 (no AI film yet: left out, no placeholder)")
                    continue
                length, note = own_sound(ai, blurfill(H) if True else "", out), f"{ai.name}, own sound"
            elif p["kind"] == "slides":
                if not slides.exists():
                    raise SystemExit(f"REFUSED: no slides recording at {slides}")
                length, note = own_sound(slides, slides_vf(a.mask_topbar), out), slides.name
                lines = slide_lines()
                w = [nwords(x) for x in lines]
                cues += [{"part": "C3", "para": 1, "start": t + length * sum(w[:i]) / sum(w),
                          "end": t + length * sum(w[:i + 1]) / sum(w), "text": x, "abs": True} for i, x in enumerate(lines)]
            elif p["kind"] == "card" and not p.get("audio"):
                length, note = silent_card(p["card"], p["seconds"], out, work, p.get("png")), "card"
            elif p["kind"] == "card":                         # the close: the card under A_C7
                paras = blocks()[p["block"]]
                audio = narration_file(p["audio"], a.preview)
                if audio is None and not a.preview:
                    raise SystemExit(f"REFUSED: no narration {p['audio']}")
                speech = duration(audio) if audio else sum(map(nwords, paras)) / WPS
                length = LEAD + speech + TAIL + 1.0
                v = work / "close_v.mp4"
                still(card_png(p["card"], work / "close.png", p.get("png")), length, v, work)
                with_narration(v, audio, length, out)
                note = f"close card, {audio.name if audio else 'no narration'}"
                pc = [{"part": p["id"], "para": 1, "start": LEAD, "end": LEAD + speech, "text": " ".join(paras)}]
                cues += [{**c, "start": c["start"] + t, "end": c["end"] + t} for c in pc]
            elif p["kind"] == "stills":
                length, note, pc = stills_part(p, a, work, out, a.preview)
                cues += [{**c, "start": c["start"] + t, "end": c["end"] + t} for c in pc]
            elif p["kind"] == "team_film":
                clean, _, bad = film_source(p, a)
                if bad:
                    if not a.preview:
                        raise SystemExit(f"REFUSED {p['id']}: {bad[0]}")
                    length = silent_card(("MISSING", bad[0][:80]), 5.0, out, work)
                    note = f"MISSING: {bad[0]}"
                else:
                    length, note = team_film(p, clean, out, work, a.preview)
            else:
                length, note, pc = narrated(p, a, work, out, a.preview)
                cues += [{**c, "start": c["start"] + t, "end": c["end"] + t} for c in pc]
            parts.append(out)
            timeline.append({"part": p["id"], "start": round(t, 2), "end": round(t + length, 2), "note": note})
            print(f"{stamp(t, False):>6}  {p['id']:4} {length:6.1f}s  {note}")
            t += length
        if t > MAX_MAIN and not (a.preview or a.allow_long):
            raise SystemExit(f"REFUSED: the main cut is {stamp(t, False)}, over 8:00 (cut order: VIDEO_PLAN_V4 section 0)")
        lst = work / "parts.txt"
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
        name = "walnut_databridge_finals_v4" + ("_PREVIEW" if a.preview else "")
        final = outdir / f"{name}.mp4"
        srt = outdir / f"{name}.srt"
        srt.write_text(srt_from(cues), encoding="utf-8")
        joined = work / "joined.mp4"
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)])
        if a.burn_subs:
            shutil.copy(srt, work / "subs.srt")
            subprocess.run(["ffmpeg", "-y", "-i", "joined.mp4", "-vf", "subtitles=subs.srt:force_style='FontSize=20,MarginV=40'",
                            *ENC, "-movflags", "+faststart", str(final)], cwd=work, check=True, capture_output=True)
        else:
            run(["ffmpeg", "-y", "-i", str(joined), "-c", "copy", "-movflags", "+faststart", str(final)])
        (outdir / f"{name}_timeline.json").write_text(json.dumps(timeline, indent=1), encoding="utf-8")
        (outdir / f"{name}_cue_sheet.md").write_text(cue_sheet(timeline, "this cut"), encoding="utf-8")
        print(f"total {stamp(t, False)} -> {final}\n      subtitles {srt.name}; timeline and cue sheet beside it")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
