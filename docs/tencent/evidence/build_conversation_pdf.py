"""Build the one-file conversation history PDF for the submission form:
submission/01_codebuddy/conversation_history_codebuddy_workbuddy_miora.pdf

    py -3.11 docs/tencent/evidence/build_conversation_pdf.py

Pages: a title page, one overview screenshot per CodeBuddy session (CB1-CB9, rendered from the saved
transcripts by render_transcripts.py + shoot_sessions.cjs), the WorkBuddy screenshots, the finals
WorkBuddy connector live test (if rendered), and the Miora sessions. Every page is 1600 x 1000 px at
150 dpi (768 x 480 pt). Needs Pillow; nothing is drawn onto the screenshots themselves.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from PIL import JpegImagePlugin  # noqa: F401  (the PDF writer encodes pages as JPEG)
from PIL import PdfImagePlugin  # noqa: F401

REPO = Path(__file__).resolve().parents[3]
SUB = REPO / "submission"
OUT = SUB / "01_codebuddy" / "conversation_history_codebuddy_workbuddy_miora.pdf"
W, H = 1600, 1000
BG, INK, GOLD = (244, 241, 234), (32, 41, 70), (164, 120, 23)
FONTS = Path("C:/Windows/Fonts")


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    name = "segoeuib.ttf" if bold else "segoeui.ttf"
    try:
        return ImageFont.truetype(str(FONTS / name), size)
    except OSError:
        return ImageFont.load_default()


CB_TITLES = [
    ("cb1", "session CB1"), ("cb2", "session CB2"), ("cb3", "session CB3"), ("cb4", "session CB4"),
    ("cb5", "session CB5"), ("cb6", "session CB6"),
    ("cb7", "session CB7 (finals): security review of the finals backend"),
    ("cb8", "session CB8 (finals): WorkBuddy connector upgrade"),
    ("cb8_part3", "session CB8, resumed after a rate limit (finals): the tests"),
    ("cb9", "session CB9 (finals): the WorkBuddy Walnut skill"),
]
WORKBUDDY = [
    (SUB / "02_workbuddy" / "workbuddy_mcp_panel.png", "WorkBuddy · the three Internal Brain connectors"),
    (SUB / "02_workbuddy" / "workbuddy_compare.png", "WorkBuddy · one question through Jordan's and Ken's connectors"),
    (SUB / "02_workbuddy" / "workbuddy_compliance.png", "WorkBuddy · compliance audit through the audit connector"),
]
FINALS_LIVE = (SUB / "02_workbuddy" / "finals" / "workbuddy_live_test.png",
               "WorkBuddy connectors (finals) · every tool driven live over stdio")
MIORA = [
    (SUB / "06_cover" / "miora_session1.png", "Miora · cover image session 1"),
    (SUB / "06_cover" / "miora_session2.png", "Miora · cover image session 2"),
    (SUB / "06_cover" / "finals" / "miora_session_finals.png", "Miora · finals cover session (30 Oct)"),
    (SUB / "06_cover" / "finals" / "cover_finals_option2_raw.png", "Miora · finals cover, chosen output"),
]


def _page(title: str, img: Path, *, top_crop: bool = False) -> Image.Image:
    page = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(page)
    d.text((40, 20), title, font=_font(True, 30), fill=INK)
    shot = Image.open(img).convert("RGB")
    if top_crop:                                     # a session page: keep its first screen only
        shot = shot.crop((0, 0, shot.width, min(shot.height, int(shot.width * 0.625))))
    box_w, box_h = W - 80, H - 70 - 30
    scale = min(box_w / shot.width, box_h / shot.height)
    shot = shot.resize((max(1, int(shot.width * scale)), max(1, int(shot.height * scale))),
                       Image.LANCZOS)
    page.paste(shot, ((W - shot.width) // 2, 70))
    return page


def main() -> None:
    cb = [(s, t) for s, t in CB_TITLES if (SUB / "01_codebuddy" / f"{s}_session_overview.png").exists()]
    wb = [x for x in WORKBUDDY if x[0].exists()] + ([FINALS_LIVE] if FINALS_LIVE[0].exists() else [])
    mi = [x for x in MIORA if x[0].exists()]
    n_cb, n_wb = len(cb), len(wb)
    p_cb = (2, 1 + n_cb)
    p_wb = (p_cb[1] + 1, p_cb[1] + n_wb)
    p_mi = (p_wb[1] + 1, p_wb[1] + len(mi))

    title = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(title)
    d.text((120, 305), "WALNUT DATABRIDGE", font=_font(True, 34), fill=GOLD)
    d.text((120, 370), "Conversation history", font=_font(True, 84), fill=INK)
    d.text((120, 490), "CodeBuddy, WorkBuddy and Miora, during development and the finals build",
           font=_font(False, 38), fill=INK)
    lines = [
        f"Pages {p_cb[0]}-{p_cb[1]}: CodeBuddy sessions CB1-CB9 (overview of each; "
        "CB7-CB9 are on the finals code)",
        f"Pages {p_wb[0]}-{p_wb[1]}: WorkBuddy: the connectors, one question through two people, "
        "compliance audit" + (", finals live test" if FINALS_LIVE in wb else ""),
        f"Pages {p_mi[0]}-{p_mi[1]}: Miora: the cover and thumbnail sessions",
        "Full transcripts: github.com/IshaanSirbhaiya/Tencent, docs/tencent/evidence/",
    ]
    for i, ln in enumerate(lines):
        d.text((120, 600 + i * 56), ln, font=_font(False, 29), fill=INK)

    pages = [title]
    pages += [_page(f"CodeBuddy · {t}", SUB / "01_codebuddy" / f"{s}_session_overview.png",
                    top_crop=True) for s, t in cb]
    pages += [_page(t, p) for p, t in wb]
    pages += [_page(t, p) for p, t in mi]
    pages[0].save(OUT, "PDF", save_all=True, append_images=pages[1:], resolution=150.0,
                  quality=88, title="conversation_history_codebuddy_workbuddy_miora")
    print(f"wrote {OUT} ({len(pages)} pages, {OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
