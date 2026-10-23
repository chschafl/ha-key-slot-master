"""Regenerate the brand PNGs from the vector masters.

    pip install playwright fonttools
    python scripts/build_brand.py

The glyph lives in ``brand/icon.svg``. The wordmark is converted to outlines
from Liberation Sans Bold (metric-compatible with Arial Bold) so the generated
``logo.svg`` / ``dark_logo.svg`` render identically everywhere. PNGs are
rasterised with headless Chromium, which is the renderer that gets SVG masks
right; set ``CHROMIUM`` if it is not on the default Playwright path.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from playwright.sync_api import sync_playwright

BRAND = Path(__file__).parent.parent / "custom_components" / "key_slot_master" / "brand"
FONT = os.environ.get("BRAND_FONT", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf")
CHROMIUM = os.environ.get("CHROMIUM")

# Same palette as STR Concierge so the two integrations read as a family.
LIGHT = {"grad": ("#34C4A8", "#0E8C7B"), "accent": "#0E8C7B", "text": "#1F2A37"}
DARK = {"grad": ("#3AD8BC", "#109D8A"), "accent": "#3AD8BC", "text": "#F4F6F7"}

WORD_ACCENT = "Key Slot"
WORD_REST = " Master"
FONT_SIZE = 79  # cap height ≈ 55 px on a 128 px tall logo
TRACKING = -2.0
TEXT_X = 152
BASELINE = 88


def _text_path(text: str, x0: float) -> tuple[str, float, float]:
    """Return (svg path, right edge of ink, pen x after the run)."""
    font = TTFont(FONT)
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap()
    scale = FONT_SIZE / font["head"].unitsPerEm
    pen = SVGPathPen(glyphs)
    bounds = BoundsPen(glyphs)
    x = x0
    for ch in text:
        glyph = glyphs[cmap[ord(ch)]]
        transform = (scale, 0, 0, -scale, x, BASELINE)
        glyph.draw(TransformPen(pen, transform))
        glyph.draw(TransformPen(bounds, transform))
        x += glyph.width * scale + TRACKING
    right = bounds.bounds[2] if bounds.bounds else x0
    return pen.getCommands(), right, x


def _icon_svg(palette: dict) -> str:
    svg = (BRAND / "icon.svg").read_text()
    start, end = palette["grad"]
    svg = svg.replace("#34C4A8", start).replace("#0E8C7B", end)
    return svg


def _logo_svg(palette: dict) -> tuple[str, int]:
    accent_path, _, pen_x = _text_path(WORD_ACCENT, TEXT_X)
    rest_path, right, _ = _text_path(WORD_REST, pen_x)
    width = int(round(right)) + 6
    icon = _icon_svg(palette)
    inner = re.search(r"<svg[^>]*>(.*)</svg>", icon, re.S).group(1)
    size = f'width="{width}" height="128" viewBox="0 0 {width} 128"'
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" {size}>
  <g transform="scale(0.5)">{inner}</g>
  <path d="{accent_path}" fill="{palette["accent"]}"/>
  <path d="{rest_path}" fill="{palette["text"]}"/>
</svg>
"""
    return svg, width


def _render(page, svg: str, out: Path, width: int, height: int) -> None:
    svg = svg.replace("<svg ", f'<svg style="width:{width}px;height:{height}px;display:block" ', 1)
    page.set_viewport_size({"width": width, "height": height})
    page.set_content(f'<html><body style="margin:0;background:transparent">{svg}</body></html>')
    page.screenshot(
        path=str(out), omit_background=True, clip={"x": 0, "y": 0, "width": width, "height": height}
    )


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROMIUM) if CHROMIUM else pw.chromium.launch()
        page = browser.new_page()
        for prefix, palette in (("", LIGHT), ("dark_", DARK)):
            icon = _icon_svg(palette)
            _render(page, icon, BRAND / f"{prefix}icon.png", 256, 256)
            _render(page, icon, BRAND / f"{prefix}icon@2x.png", 512, 512)
            logo, width = _logo_svg(palette)
            (BRAND / f"{prefix}logo.svg").write_text(logo)
            _render(page, logo, BRAND / f"{prefix}logo.png", width, 128)
            _render(page, logo, BRAND / f"{prefix}logo@2x.png", width * 2, 256)
        browser.close()


if __name__ == "__main__":
    main()
