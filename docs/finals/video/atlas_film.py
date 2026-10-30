"""Prepare a team-recorded film for the finals video: Dev's Atlas film (founder's decision, option A: used as
recorded) and the team's "The Call" recording (``--film <v3>/call_film.mp4 --out <v3>/work/call_clean.mp4``).

    python docs/finals/video/atlas_film.py                 detect, blur, render, then verify
    python docs/finals/video/atlas_film.py --verify-only   re-check an already rendered file
    python docs/finals/video/atlas_film.py --self-test     the box, range and level logic, no media

(Needs a Python with numpy and opencv, e.g. ``databridge/.venv/Scripts/python``; ffmpeg on PATH.)

Founder's order (30 Oct): the film is NOT cut, re-ordered or re-timed, and its own sound (the voice-over) is
kept exactly as recorded. The only picture changes:

1. **The model-label line is blurred** wherever it is on screen. The answer card shows, next to "Answer", the
   provider and the model that wrote it, and the page header says "Laid out by <model>". Both must never be
   seen (no provider name anywhere). The two words are found in every frame by template matching (whole
   words and their halves, so a half-scrolled label is caught too); a provider hit is widened over the model
   id after it. The templates are cut from the Atlas film at run time (frame ``REF_TIME``, rectangles
   ``PROVIDER_BOX`` / ``MODEL_BOX``): no image of the label is stored, and neither name is written here.
   Each hit is blurred with a few frames of margin either side.
2. Scaled to 1920x1080 (fitted to 1080 high, centred) and 120 -> 30 fps without changing the length.

The sound: the source track, its timing untouched (copied as is). The one allowed change is a whole-clip gain
towards -16 LUFS when it is more than 1 LU off (never above -1 dBTP; no limiter, no compression).

**Verified** after rendering (exit 1 if not): the matcher on the rendered file every 0.5 s across the whole
clip and on every frame near a detection, no score above ``VERIFY_MAX``; the length equals the source's
(+-0.05 s); the sound is present and as long as the source's (+-0.05 s).

Written to ``<v3>/work/`` (outside git): ``<name>_clean.mp4``, ``<name>_film.json`` (blurred ranges, level,
verification) and ``<name>_clean_hits.json`` (the detections, reused by a re-run).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
V3 = Path(os.environ.get("WALNUT_V3_DIR") or REPO / "submission" / "finals_video" / "v3")
W, H, FPS = 1920, 1080, 30
# where the label's two words sit in the reference frame, in a 1512-wide copy of the film (measured on
# the frame at 33 s: the answer card, just right of "Answer"); scaled to the working size at run time
REF_TIME = 33.0
PROVIDER_BOX = (593, 333, 652, 353)
MODEL_BOX = (658, 333, 759, 353)
# the two page-loading waits, in seconds of the source (contact sheets every 0.5 s: loading 35.0-42.5 and
# 69.5-77.0); each keeps ~0.8 s of "Checking your sources again…" at its start and ~0.4 s at its end
# Founder's order (30 Oct): the films are NOT cut, re-timed or re-voiced. The two page-loading waits of the
# Atlas film (35.0-42.5 s and 69.5-77.0 s, from contact sheets) are noted here only; --cuts can still cut a
# silent film (--no-audio), never one whose own voice is kept.
ATLAS_WAITS = [(35.8, 42.1), (70.3, 76.6)]
CUTS: list[tuple[float, float]] = []
TARGET_LUFS = -16.0
# Template-match scores (measured on the Atlas film, 30 Oct): the real label scores 0.85-1.0; ordinary text
# never above 0.78 (whole words and halves alike), with nothing in between. So a hit is >= 0.85 (a half word
# >= 0.88), and after blurring no frame may score 0.82 or more.
DETECT_MIN, HALF_MIN = 0.85, 0.88
VERIFY_MAX = 0.82
PAD_PX, PAD_FRAMES = 8, 4


def _cv():
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    return cv2, np


def content_size(src_w: int, src_h: int) -> tuple[int, int]:
    """The film fitted to 1080 high (even width), centred on the 1920x1080 frame."""
    cw = int(round(src_w * H / src_h / 2)) * 2
    return min(cw, W), H


def probe(path: Path) -> tuple[int, int, float]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    return d["streams"][0]["width"], d["streams"][0]["height"], float(d["format"]["duration"])


def frames(path: Path, cw: int, ch: int, start: float = 0.0, dur: float | None = None):
    """Decoded BGR frames at 30 fps and the working size, with their source time."""
    cv2, np = _cv()
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", str(path)]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-vf", f"fps={FPS},scale={cw}:{ch}:flags=lanczos", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)   # a reader that stops early closes the pipe
    size, i = cw * ch * 3, 0
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            yield start + i / FPS, np.frombuffer(buf, np.uint8).reshape(ch, cw, 3)
            i += 1
    finally:
        p.stdout.close()
        p.wait()


def templates(film: Path, cw: int, ch: int) -> list[tuple[str, object, float, int]]:
    """The label's two words, whole and halved, cut from the reference frame (always the ATLAS film, the
    one measured) at the working size. A provider hit is widened to the right to cover the model id after
    it, whatever that id is (the team's Call film may name another one)."""
    cv2, _ = _cv()
    _, frame = next(frames(ref_film(film), cw, ch, REF_TIME, 0.2))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    k = cw / 1512
    out = []
    for name, (x0, y0, x1, y1) in (("provider", PROVIDER_BOX), ("model", MODEL_BOX)):
        t = gray[int(y0 * k):int(y1 * k), int(x0 * k):int(x1 * k)].copy()
        half = t.shape[1] // 2
        ext = int((MODEL_BOX[2] - MODEL_BOX[0]) * 1.6 * k) if name == "provider" else 0
        out += [(name, t, DETECT_MIN, ext), (f"{name}-left", t[:, :half + 4].copy(), HALF_MIN, ext + t.shape[1] - half),
                (f"{name}-right", t[:, half - 4:].copy(), HALF_MIN, ext)]
    return out


REF_FILM: Path | None = None


def ref_film(film: Path) -> Path:
    """The film the templates are cut from: --ref-film, else the Atlas film, else this film."""
    for p in (REF_FILM, V3 / "atlas_film.mp4"):
        if p and p.exists():
            return p
    return film


def find(gray, temps, floor: float | None = None) -> tuple[list[tuple[int, int, int, int, str, float]], float]:
    """All label hits in one grey frame (x0, y0, x1, y1, template, score), and the best score seen."""
    cv2, np = _cv()
    hits, best = [], 0.0
    for name, t, thr, ext in temps:
        r = cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED)
        th, tw = t.shape
        for _ in range(6):                                  # a few hits per template (badge and page header)
            _, mx, _, (x, y) = cv2.minMaxLoc(r)
            best = max(best, float(mx))
            if mx < (thr if floor is None else floor):
                break
            hits.append((x, y, x + tw + ext, y + th, name, round(float(mx), 3)))
            r[max(0, y - th):y + th, max(0, x - tw):x + tw] = -1
    return hits, best


def pad_box(b, cw: int, ch: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = b[:4]
    return max(0, x0 - PAD_PX), max(0, y0 - PAD_PX // 2), min(cw, x1 + PAD_PX), min(ch, y1 + PAD_PX // 2)


def boxes_for(i: int, hits_by_frame: dict[int, list]) -> list:
    """The boxes to blur on frame i: every hit within PAD_FRAMES frames of it."""
    out = []
    for j in range(i - PAD_FRAMES, i + PAD_FRAMES + 1):
        out += hits_by_frame.get(j, [])
    return out


def in_cut(t: float, cuts=None) -> bool:
    return any(a <= t < b for a, b in (CUTS if cuts is None else cuts))


def ranges(hits_by_frame: dict[int, list], cw: int, ch: int, gap: int = PAD_FRAMES * 2) -> list[dict]:
    """Consecutive blurred frames merged into time ranges (source seconds) with the union of their boxes."""
    out: list[dict] = []
    for i in sorted(hits_by_frame):
        bs = [pad_box(b, cw, ch) for b in hits_by_frame[i]]
        u = [min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)]
        if out and i - out[-1]["_last"] <= gap:
            r = out[-1]
            r["box"] = [min(r["box"][0], u[0]), min(r["box"][1], u[1]), max(r["box"][2], u[2]), max(r["box"][3], u[3])]
            r["_last"], r["frames"] = i, r["frames"] + 1
        else:
            out.append({"_first": i, "_last": i, "box": u, "frames": 1})
    for r in out:
        r["from"] = round(max(0, r.pop("_first") - PAD_FRAMES) / FPS, 2)
        r["to"] = round((r.pop("_last") + PAD_FRAMES + 1) / FPS, 2)
    return out


def loudness(path: Path) -> tuple[float, float]:
    """(integrated LUFS, true peak dBTP) of a file's sound."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    summ = err.split("Summary:")[-1]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", summ)
    pk = re.search(r"Peak:\s+(-?[\d.]+) dBFS", summ)
    return (float(i.group(1)) if i else float("nan"), float(pk.group(1)) if pk else 0.0)


def level_gain(lufs: float, peak: float) -> float:
    """The one allowed sound change: a whole-clip gain towards -16 LUFS, only when it is more than 1 LU off,
    never pushing the peak above -1 dBTP (no limiter, no compression, no timing change)."""
    if lufs != lufs or abs(lufs - TARGET_LUFS) <= 1.0:
        return 0.0
    return round(min(TARGET_LUFS - lufs, -1.0 - peak), 2)


def stream_seconds(path: Path, kind: str) -> float | None:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", f"{kind}:0", "-show_entries",
                          "stream=duration", "-of", "json", str(path)], capture_output=True, text=True).stdout
    st = json.loads(out or "{}").get("streams") or []
    v = st[0].get("duration") if st else None
    return float(v) if v not in (None, "N/A") else None


def detect(film: Path, cw: int, ch: int, temps, cache: Path) -> dict[int, list]:
    """Pass 1: the label's hits per frame. Kept in <v3>/work (outside git) so a re-render need not search again."""
    cv2, _ = _cv()
    key = {"film": str(film), "bytes": film.stat().st_size, "mtime": int(film.stat().st_mtime), "size": [cw, ch]}
    if cache.exists():
        c = json.loads(cache.read_text(encoding="utf-8"))
        lo = c.get("key", {}).get("thresholds") or c.get("thresholds") or [9, 9]
        if {k: v for k, v in c.get("key", {}).items() if k in key} == key and lo[0] <= DETECT_MIN and lo[1] <= HALF_MIN:
            print("film: label hits read from the cache (re-filtered at the current thresholds)")
            keep = lambda h: h[5] >= (HALF_MIN if "-" in h[4] else DETECT_MIN)  # noqa: E731
            out = {int(k): [tuple(h) for h in v if keep(h)] for k, v in c["hits"].items()}
            return {k: v for k, v in out.items() if v}
    hits: dict[int, list] = {}
    for i, (_, f) in enumerate(frames(film, cw, ch)):
        hs, _ = find(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), temps)
        if hs:
            hits[i] = hs
    cache.write_text(json.dumps({"key": {**key, "thresholds": [DETECT_MIN, HALF_MIN]}, "hits": hits}), encoding="utf-8")
    return hits


def run_ff(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise SystemExit(f"ffmpeg failed: {r.stderr[-800:]}")


def render(film: Path, out: Path, report: Path, keep_audio: bool = True) -> dict:
    cv2, np = _cv()
    sw, sh, dur = probe(film)
    cw, ch = content_size(sw, sh)
    temps = templates(film, cw, ch)
    print(f"film: {sw}x{sh}, {dur:.2f}s -> {cw}x{ch} centred on {W}x{H} at {FPS} fps; pass 1, finding the label", flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    hits_by_frame = detect(film, cw, ch, temps, out.with_name(out.stem + "_hits.json"))
    rs = ranges(hits_by_frame, cw, ch)
    print(f"film: label seen on {len(hits_by_frame)} frames, {len(rs)} ranges; pass 2, blurring"
          + (" and cutting" if CUTS else " (no cuts: the source's timing)"), flush=True)
    silent = out.with_name(out.stem + ".video.mp4")
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                            "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "17",
                            "-pix_fmt", "yuv420p", str(silent)], stdin=subprocess.PIPE)
    canvas = np.zeros((H, W, 3), np.uint8)
    canvas[:] = (13, 11, 10)                                   # the video's dark background (0x0a0b0d)
    ox, kept = (W - cw) // 2, 0
    blurred_frames = 0
    for i, (t, f) in enumerate(frames(film, cw, ch)):
        if in_cut(t):
            continue
        f = f.copy()
        bs = boxes_for(i, hits_by_frame)
        for b in bs:
            x0, y0, x1, y1 = pad_box(b, cw, ch)
            roi = f[y0:y1, x0:x1]
            # a strong blur, then flattened to the area's mean colour: unreadable, no letter shapes left
            f[y0:y1, x0:x1] = cv2.addWeighted(cv2.GaussianBlur(roi, (0, 0), 9), 0.35,
                                              np.full_like(roi, roi.reshape(-1, 3).mean(0).astype(np.uint8)), 0.65, 0)
        blurred_frames += bool(bs)
        canvas[:, ox:ox + cw] = f
        enc.stdin.write(canvas.tobytes())
        kept += 1
    enc.stdin.close()
    if enc.wait():
        raise SystemExit("film: ffmpeg could not encode the clean picture")
    # the sound: the source's own track, its timing untouched; at most one whole-clip gain
    audio: dict = {"kept": False}
    if keep_audio and stream_seconds(film, "a") is not None:
        lufs, peak = loudness(film)
        gain = level_gain(lufs, peak)
        acodec = ["-c:a", "copy"] if gain == 0 else ["-af", f"volume={gain}dB", "-c:a", "aac", "-b:a", "256k"]
        run_ff(["ffmpeg", "-v", "error", "-y", "-i", str(silent), "-i", str(film), "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", *acodec, "-movflags", "+faststart", str(out)])
        audio = {"kept": True, "source_lufs": lufs, "source_peak_dbfs": peak, "gain_db": gain}
        silent.unlink()
    else:
        silent.replace(out)
    info = {"source": str(film), "source_size": [sw, sh], "source_seconds": round(dur, 3), "content": [cw, ch],
            "offset_x": ox, "cuts": CUTS, "blurred_ranges": rs, "frames_blurred": blurred_frames,
            "output": str(out), "output_frames": kept, "audio": audio}
    report.write_text(json.dumps(info, indent=1), encoding="utf-8")
    return info


def verify(film: Path, out: Path, report: Path, info: dict | None = None) -> bool:
    """No readable label (every 0.5 s of the whole output, and every frame near a blurred range), and, with no
    cuts, the same length as the source (+-0.05 s) with the sound present and as long as the source's."""
    cv2, _ = _cv()
    sw, sh, _ = probe(film)
    cw, ch = content_size(sw, sh)
    temps = templates(film, cw, ch)
    info = info or json.loads(report.read_text(encoding="utf-8"))
    ox = info["offset_x"]

    def out_t(s: float) -> float:                           # the blurred ranges in output time
        return s - sum(min(b, max(a, s)) - a for a, b in CUTS)
    near = [(out_t(r["from"]) - 1.0, out_t(r["to"]) + 1.0) for r in info["blurred_ranges"]]
    worst, bad, checked = 0.0, [], 0
    for i, (t, f) in enumerate(frames(out, W, H)):
        if i % (FPS // 2) and not any(a <= t <= b for a, b in near):
            continue
        g = cv2.cvtColor(f[:, ox:ox + cw], cv2.COLOR_BGR2GRAY)
        _, best = find(g, temps, floor=2.0)                    # floor 2: never "hit", just the best score
        checked += 1
        worst = max(worst, best)
        if best > VERIFY_MAX:
            bad.append(round(t, 2))
    problems = [f"a readable label at {bad[:10]}"] if bad else []
    src_d, out_d = probe(film)[2], probe(out)[2]
    src_a, out_a = stream_seconds(film, "a"), stream_seconds(out, "a")
    if not CUTS:
        if abs(out_d - src_d) > 0.05:
            problems.append(f"length {out_d:.3f}s, the source is {src_d:.3f}s")
        if (info.get("audio") or {}).get("kept") and (out_a is None or src_a is None or abs(out_a - src_a) > 0.05):
            problems.append(f"sound {out_a}s, the source's is {src_a}s")
    info["verify"] = {"frames_checked": checked, "worst_score": round(worst, 3), "max_allowed": VERIFY_MAX,
                      "failed_at": bad[:50], "source_seconds": round(src_d, 3), "output_seconds": round(out_d, 3),
                      "source_audio_seconds": src_a, "output_audio_seconds": out_a, "problems": problems,
                      "ok": not problems}
    report.write_text(json.dumps(info, indent=1), encoding="utf-8")
    print(f"film verify: {checked} frames checked, worst label score {worst:.3f} (max {VERIFY_MAX}); length "
          f"{out_d:.3f}s vs {src_d:.3f}s; sound {out_a} vs {src_a}; "
          + ("OK" if not problems else "FAILED: " + "; ".join(problems)))
    return not problems


def self_test() -> int:
    assert content_size(3024, 1964) == (1662, 1080), content_size(3024, 1964)
    assert content_size(1918, 1080) == (1918, 1080)
    hits = {10: [(100, 50, 160, 70, "model", 0.9)], 12: [(102, 50, 162, 70, "model", 0.9)],
            200: [(400, 300, 460, 320, "provider", 0.8)]}
    rs = ranges(hits, 1662, 1080)
    assert len(rs) == 2 and rs[0]["from"] == round(6 / 30, 2) and rs[0]["box"][0] == 92, rs
    assert boxes_for(14, hits) and boxes_for(16, hits) and not boxes_for(17, hits)   # 12 + PAD_FRAMES
    assert not CUTS and not in_cut(36.0), "the films are not cut by default (founder's order)"
    assert in_cut(36.0, ATLAS_WAITS) and not in_cut(35.0, ATLAS_WAITS) and not in_cut(76.6, ATLAS_WAITS)
    assert level_gain(-8.2, -0.5) == -7.8 and level_gain(-16.6, -3) == 0.0 and level_gain(float("nan"), 0) == 0.0
    assert level_gain(-24.0, -3.0) == 2.0, "a gain up never pushes the peak above -1 dBTP"
    assert pad_box((0, 0, 10, 10), 100, 100) == (0, 0, 18, 14)
    print("self-test ok: ranges, margins, no cuts by default, the whole-clip level gain")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--film", type=Path, default=V3 / "atlas_film.mp4",
                    help="the film to clean (default the Atlas film; the team's Call film: --film <v3>/call_film.mp4 "
                         "--out <v3>/work/call_clean.mp4)")
    ap.add_argument("--out", type=Path, default=V3 / "work" / "atlas_clean.mp4")
    ap.add_argument("--cuts", default="none", help="'none' (default; founder's order) or 'a-b,c-d' in source seconds, only with --no-audio")
    ap.add_argument("--no-audio", action="store_true", help="drop the film's sound (default: keep it exactly as recorded)")
    ap.add_argument("--ref-film", type=Path, default=None, help="where the label templates are cut from (default the Atlas film)")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    global CUTS, REF_FILM
    REF_FILM = a.ref_film
    CUTS = [] if a.cuts == "none" else [tuple(float(x) for x in c.split("-")) for c in a.cuts.split(",")]
    if CUTS and not a.no_audio:
        print("refused: cutting a film whose own voice is kept would put the voice out of sync (founder's order: no cuts)")
        return 2
    if not a.film.exists():
        print(f"no Atlas film at {a.film} (copy Dev's film there as atlas_film.mp4)")
        return 2
    report = a.out.with_name(a.out.stem.replace("_clean", "") + "_film.json")
    info = None if a.verify_only else render(a.film, a.out, report, keep_audio=not a.no_audio)
    if info:
        for r in info["blurred_ranges"]:
            print(f"  blurred {r['from']:6.2f}-{r['to']:6.2f}s  box {r['box']} ({r['frames']} frames)")
        print(f"film: {info['source_seconds']}s, {info['output_frames']} frames at {FPS} fps, sound {info['audio']} -> {a.out}")
    return 0 if verify(a.film, a.out, report, info) else 1


if __name__ == "__main__":
    sys.exit(main())
