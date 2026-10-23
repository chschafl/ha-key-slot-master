"""Tests for the in-package brand artwork (same scheme as STR Concierge).

Since Home Assistant 2026.3, a custom integration ships its own icons and logos
in a ``brand/`` folder next to ``manifest.json``; the ``brands`` integration
serves them from ``/api/brands/integration/<domain>/<image>``.

Home Assistant resolves them with two cheap checks — ``"brand" in
os.listdir(<integration dir>)`` (``Integration.has_branding``) and then
``<integration dir>/brand/<image>`` — and falls back to the brands CDN when
either misses. Nothing is submitted to the CDN for this domain, so a miss shows
the generic "image not found" placeholder in the integration list instead of the
real mark. These tests pin the file names, formats and dimensions that keep the
local lookup hitting.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

INTEGRATION_DIR = Path(__file__).parent.parent / "custom_components" / "key_slot_master"
BRAND_DIR = INTEGRATION_DIR / "brand"

# homeassistant.components.brands.const.ALLOWED_IMAGES — the only image names
# the proxy will serve. Anything the frontend asks for that is missing here
# falls through to the CDN, so all eight ship.
ALLOWED_IMAGES = frozenset(
    {
        "icon.png",
        "logo.png",
        "icon@2x.png",
        "logo@2x.png",
        "dark_icon.png",
        "dark_logo.png",
        "dark_icon@2x.png",
        "dark_logo@2x.png",
    }
)

ICON_SIZES = {"icon.png": 256, "dark_icon.png": 256, "icon@2x.png": 512, "dark_icon@2x.png": 512}

# Shortest side, per the brands image specification.
LOGO_BOUNDS = {
    "logo.png": (128, 256),
    "dark_logo.png": (128, 256),
    "logo@2x.png": (256, 512),
    "dark_logo@2x.png": (256, 512),
}

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_size(data: bytes) -> tuple[int, int]:
    """Return (width, height) from a PNG's IHDR chunk."""
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _png_chunks(data: bytes) -> list[str]:
    """Walk the PNG chunk stream, verifying every CRC. Returns chunk types."""
    chunks: list[str] = []
    offset = 8
    while offset < len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8].decode("ascii")
        crc_offset = offset + 8 + length
        assert crc_offset + 4 <= len(data), f"truncated {chunk_type} chunk"
        expected = struct.unpack(">I", data[crc_offset : crc_offset + 4])[0]
        actual = zlib.crc32(data[offset + 4 : crc_offset]) & 0xFFFFFFFF
        assert actual == expected, f"bad CRC on {chunk_type} chunk"
        chunks.append(chunk_type)
        offset = crc_offset + 4
    return chunks


class TestBrandFolderLayout:
    def test_brand_dir_sits_inside_the_integration_package(self):
        """This is the path HA builds as Integration.file_path / "brand"."""
        assert (INTEGRATION_DIR / "manifest.json").is_file()
        assert BRAND_DIR.is_dir()

    def test_brand_is_a_top_level_entry(self):
        """Integration.has_branding is `"brand" in os.listdir(integration dir)`."""
        assert "brand" in {entry.name for entry in INTEGRATION_DIR.iterdir()}

    def test_ships_every_servable_image_and_no_stray_png(self):
        """Exactly the eight names the proxy serves — no gaps, no misnamed extras.

        A missing name falls through to the CDN placeholder; a misnamed extra
        (`logo@3x.png`, `Icon.png`) is dead weight that never gets served.
        """
        assert {p.name for p in BRAND_DIR.glob("*.png")} == ALLOWED_IMAGES

    def test_vector_master_ships_alongside_the_exports(self):
        """icon.svg is the design master. HA never reads it; contributors do."""
        assert (BRAND_DIR / "icon.svg").is_file()


@pytest.mark.parametrize("name", sorted(ALLOWED_IMAGES))
class TestBrandImageFiles:
    def test_is_a_readable_png(self, name: str):
        """The proxy serves the raw bytes as image/png without transcoding."""
        data = (BRAND_DIR / name).read_bytes()
        assert data.startswith(PNG_SIGNATURE)
        chunks = _png_chunks(data)
        assert chunks[0] == "IHDR"
        assert chunks[-1] == "IEND"
        assert "IDAT" in chunks

    def test_matches_the_brands_dimension_spec(self, name: str):
        width, height = _png_size((BRAND_DIR / name).read_bytes())
        if name in ICON_SIZES:
            expected = ICON_SIZES[name]
            assert (width, height) == (expected, expected)
        else:
            low, high = LOGO_BOUNDS[name]
            assert width > height, "logos are landscape"
            assert low <= height <= high


class TestBrandsProxy:
    """End-to-end through the API the frontend actually calls.

    Everything above checks the files on disk. This checks that Home Assistant
    resolves them — `Integration.has_branding`, then the local file read — and
    hands back our bytes rather than falling through to the brands CDN.
    """

    @pytest.mark.parametrize("name", sorted(ALLOWED_IMAGES))
    async def test_serves_our_file_not_the_cdn_placeholder(
        self, hass, hass_client, enable_custom_integrations, name: str
    ):
        from homeassistant.setup import async_setup_component

        assert await async_setup_component(hass, "brands", {})
        await hass.async_block_till_done()

        client = await hass_client()
        # placeholder=no turns the silent CDN placeholder fallback into a 404,
        # so a miss fails loudly here instead of returning someone else's image.
        resp = await client.get(f"/api/brands/integration/key_slot_master/{name}?placeholder=no")

        assert resp.status == 200
        assert resp.content_type == "image/png"
        assert await resp.read() == (BRAND_DIR / name).read_bytes()
