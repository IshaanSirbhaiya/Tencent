"""Prepare a team-recorded film for the finals video: Dev's Atlas film (founder's decision, option A: used as
recorded) and the team's "The Call" recording (``--film <v3>/call_film.mp4 --out <v3>/work/call_clean.mp4 --cuts none``).

    python docs/finals/video/atlas_film.py                 detect, blur, cut, render, then verify
    python docs/finals/video/atlas_film.py --verify-only   re-check an already rendered file
    python docs/finals/video/atlas_film.py --self-test     the box and range logic, no media

(Needs a Python with numpy and opencv, e.g. ``databridge\\.venv\\Scripts\\python``; ffmpeg on PATH.)

What it does to ``<v3>/atlas_film.mp4`` (3024x1964 at 120 fps, a music bed as audio):

1. **Blurs the model-label line** wherever it is on screen. The answer card shows, next to "Answer", the
   provider and the model that wrote it, and the page header says "Laid out by <model>". Both must never be
   seen (the founder's rule: no provider name anywhere). The two words are found in every frame by template
   matching (whole words and their halves, so a half-scrolled label is caught too). The templates are cut
   from the film itself at run time (frame ``--ref-time``, rectangles ``PROVIDER_BOX`` / ``MODEL_BOX``):
   no image of the label is ever stored, and neither name is written in this file. Each hit is blurred,
   with a few frames of margin either side.
2. **Cuts the page-loading waits** ("Checking your sources again and laying out the page…"), keeping about a
   second of each so the wait never looks faked (``CUTS``, seconds of the source).
3. Scales it to 1920x1080 at 30 fps (the 3024x1964 frame is fitted to 1080 high and centred) with no audio:
   the narration (B_C4) replaces the music bed.
4. **Verifies** the result: the same matcher runs on the rendered file every 0.5 s across the whole clip,
   and on every frame near a detection; any score above ``VERIFY_MAX`` fails the run (exit 1).

The detections, the blurred time ranges and the verification are written to ``<v3>/work/atlas_film.json``
(outside git). The output is ``<v3>/work/atlas_clean.mp4``; assemble_v3.py uses it for C4.
"""
from __future__ import annotations

import argparse
import json
import os
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
CUTS = [(35.8, 42.1), (70.3, 76.6)]
DETECT_MIN, HALF_MIN = 0.55, 0.72       # template-match scores that count as the label (whole word / half word)
VERIFY_MAX = 0.45                        # after blurring, no frame may score above this
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
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
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


def render(film: Path, out: Path, report: Path) -> dict:
    cv2, np = _cv()
    sw, sh, dur = probe(film)
    cw, ch = content_size(sw, sh)
    temps = templates(film, cw, ch)
    print(f"atlas: {sw}x{sh}, {dur:.1f}s -> {cw}x{ch} centred on {W}x{H}; pass 1, finding the label")
    hits_by_frame: dict[int, list] = {}
    for i, (t, f) in enumerate(frames(film, cw, ch)):
        hs, _ = find(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), temps)
        if hs:
            hits_by_frame[i] = hs
    rs = ranges(hits_by_frame, cw, ch)
    print(f"atlas: label seen on {len(hits_by_frame)} frames, {len(rs)} ranges; pass 2, blurring and cutting")
    out.parent.mkdir(parents=True, exist_ok=True)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                            "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "17",
                            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
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
        raise SystemExit("atlas: ffmpeg could not encode the clean film")
    info = {"source": str(film), "source_size": [sw, sh], "source_seconds": round(dur, 2), "content": [cw, ch],
            "offset_x": ox, "cuts": CUTS, "blurred_ranges": rs, "frames_blurred": blurred_frames,
            "output": str(out), "output_seconds": round(kept / FPS, 2)}
    report.write_text(json.dumps(info, indent=1), encoding="utf-8")
    return info


def verify(film: Path, out: Path, report: Path, info: dict | None = None) -> bool:
    """Every 0.5 s of the whole output, and every frame near a blurred range: no label score above VERIFY_MAX."""
    cv2, _ = _cv()
    sw, sh, _ = probe(film)
    cw, ch = content_size(sw, sh)
    temps = templates(film, cw, ch)
    info = info or json.loads(report.read_text(encoding="utf-8"))
    ox = info["offset_x"]
    # the blurred ranges in output time (the cuts moved them earlier)
    def out_t(s: float) -> float:
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
    info["verify"] = {"frames_checked": checked, "worst_score": round(worst, 3), "max_allowed": VERIFY_MAX,
                      "failed_at": bad[:50], "ok": not bad}
    report.write_text(json.dumps(info, indent=1), encoding="utf-8")
    print(f"atlas verify: {checked} frames checked, worst label score {worst:.3f} (max {VERIFY_MAX}); "
          + ("OK, no readable label" if not bad else f"FAILED at {bad[:10]}"))
    return not bad


def self_test() -> int:
    assert content_size(3024, 1964) == (1662, 1080), content_size(3024, 1964)
    assert content_size(1918, 1080) == (1918, 1080)
    hits = {10: [(100, 50, 160, 70, "model", 0.9)], 12: [(102, 50, 162, 70, "model", 0.9)],
            200: [(400, 300, 460, 320, "provider", 0.8)]}
    rs = ranges(hits, 1662, 1080)
    assert len(rs) == 2 and rs[0]["from"] == round(6 / 30, 2) and rs[0]["box"][0] == 92, rs
    assert boxes_for(14, hits) and boxes_for(16, hits) and not boxes_for(17, hits)   # 12 + PAD_FRAMES
    assert in_cut(36.0) and not in_cut(35.0) and in_cut(70.3) and not in_cut(76.6)
    assert all(b - a < 7 for a, b in CUTS), "a cut must leave the start and the end of the wait"
    assert pad_box((0, 0, 10, 10), 100, 100) == (0, 0, 18, 14)
    print(f"self-test ok: ranges, margins and cuts ({', '.join(f'{a}-{b}s' for a, b in CUTS)})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--film", type=Path, default=V3 / "atlas_film.mp4",
                    help="the film to clean (default the Atlas film; the team's Call film: --film <v3>/call_film.mp4 "
                         "--out <v3>/work/call_clean.mp4 --cuts none)")
    ap.add_argument("--out", type=Path, default=V3 / "work" / "atlas_clean.mp4")
    ap.add_argument("--cuts", default=None, help="waits to cut, 'a-b,c-d' in source seconds, or 'none' (default: the Atlas cuts)")
    ap.add_argument("--ref-film", type=Path, default=None, help="where the label templates are cut from (default the Atlas film)")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    global CUTS, REF_FILM
    REF_FILM = a.ref_film
    if a.cuts is not None:
        CUTS = [] if a.cuts == "none" else [tuple(float(x) for x in c.split("-")) for c in a.cuts.split(",")]
    if not a.film.exists():
        print(f"no Atlas film at {a.film} (copy Dev's film there as atlas_film.mp4)")
        return 2
    report = a.out.with_name(a.out.stem.replace("_clean", "") + "_film.json")
    info = None if a.verify_only else render(a.film, a.out, report)
    if info:
        for r in info["blurred_ranges"]:
            print(f"  blurred {r['from']:6.2f}-{r['to']:6.2f}s  box {r['box']} ({r['frames']} frames)")
        print(f"atlas: {info['source_seconds']}s -> {info['output_seconds']}s -> {a.out}")
    return 0 if verify(a.film, a.out, report, info) else 1


if __name__ == "__main__":
    sys.exit(main())
