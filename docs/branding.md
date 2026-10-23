# Branding assets

The Key Slot Master mark — a teal rounded-square house-with-key glyph plus the "Key Slot Master"
wordmark — lives in one place:

```
custom_components/key_slot_master/brand/
```

That folder is canonical. There is no second copy to keep in sync.

## What's in it

| File | Dimensions | Purpose |
|---|---|---|
| `icon.png` | 256 × 256 | Square integration icon |
| `icon@2x.png` | 512 × 512 | hDPI variant of `icon.png` |
| `logo.png` | 721 × 128 | Horizontal logo — glyph + "Key Slot Master" wordmark |
| `logo@2x.png` | 1442 × 256 | hDPI variant of `logo.png` |
| `dark_icon.png` | 256 × 256 | Icon optimised for dark backgrounds |
| `dark_icon@2x.png` | 512 × 512 | hDPI variant of `dark_icon.png` |
| `dark_logo.png` | 721 × 128 | Logo optimised for dark backgrounds |
| `dark_logo@2x.png` | 1442 × 256 | hDPI variant of `dark_logo.png` |
| `icon.svg` | vector | Master artwork for the glyph; source for the PNG exports |
| `logo.svg`, `dark_logo.svg` | vector | Generated logo masters (wordmark converted to outlines) |

All PNGs have transparent backgrounds. `icon.svg` is the design master — HA never reads it, but
every PNG above is exported from it, so edit the SVG first.

## How Home Assistant picks them up

Since **Home Assistant 2026.3**, a custom integration ships brand images inside its own package
in a `brand/` folder, and the `brands` system integration serves them from
`/api/brands/integration/<domain>/<image>`
([announcement](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api)). They
drive the integration card, the config flow header, and the device pages. No extra
configuration — no manifest key, no static path registration.

The lookup HA performs is deliberately cheap, and both halves have to hit:

1. `"brand" in os.listdir(<integration dir>)` — the `Integration.has_branding` flag, computed
   from a directory listing taken **once per HA start**.
2. `<integration dir>/brand/<image>` exists, where `<image>` is one of the eight names in the
   table above.

Only those eight names are servable. A request for a name that isn't there falls through to the
brands CDN, and the CDN has nothing for this domain, so the user gets the generic
"image not found" placeholder rather than our mark. That's the whole failure mode, and
[`tests/test_brand_assets.py`](../tests/test_brand_assets.py) guards against it: it asserts the
folder sits where HA computes it, holds exactly those eight PNGs and no misnamed extras, and
that each decodes at the spec'd size. It then drives a real Home Assistant through
`/api/brands/integration/key_slot_master/<image>` for all eight and asserts the response bytes are
ours — so CI proves the artwork is actually being served, not merely present on disk.

Submitting to the [`home-assistant/brands`](https://github.com/home-assistant/brands) repo is
**not** an option any more, not merely unnecessary: that repo's `custom_integrations/` folder is
a legacy path and its PR template states custom-component additions are no longer accepted. The
in-package folder is the only route.

`hacs.json` sets the minimum Home Assistant version to `2026.3.0` to match, so every install
that can add the integration renders the real artwork. If that floor is ever lowered, older
installs will fall back to the generic gear icon — there is no CDN fallback to catch them.

## Still seeing the "image not found" placeholder?

The placeholder is served by HA itself when the local lookup misses, so it tells you the request
reached the brands API and found nothing. Work through this in order:

1. **Check the installed copy, not the repo.** `config/custom_components/key_slot_master/brand/`
   has to contain the PNGs. HACS downloads the whole integration directory from the default
   branch, so a download taken before the artwork landed won't have it — re-download in HACS. A
   manual install needs `cp -r` of the entire package directory, not just the `.py` files.
2. **Restart Home Assistant.** `has_branding` comes from a directory listing cached for the
   lifetime of the process. Dropping `brand/` into a running instance — or reloading the
   integration — changes nothing until a full restart.
3. **Hard-refresh the browser.** The proxy returns the raw PNG bytes, and browsers will happily
   keep serving the placeholder they cached earlier.
4. **Ask the API directly.** With a long-lived access token:

   ```bash
   curl -sI -H "Authorization: Bearer $HA_TOKEN" \
     "http://homeassistant.local:8123/api/brands/integration/key_slot_master/icon.png?placeholder=no"
   ```

   `?placeholder=no` turns the silent placeholder fallback into a `404`, which is what makes this
   worth running: `200` means HA is serving our file and any remaining problem is display-side,
   `404` means steps 1–2 aren't satisfied yet.

### HACS's own panel is a separate story

The HACS dashboard and downloads panel still resolve icons through HACS's own data service and
the old brands CDN rather than HA's brands proxy, so custom integrations that ship artwork
in-package show as "icon not available" *there* while rendering correctly in
**Settings → Devices & Services** (hacs/integration [#5171](https://github.com/hacs/integration/issues/5171),
[#5223](https://github.com/hacs/integration/issues/5223)). Since the brands repo no longer
accepts custom-integration PRs, there is no CDN copy that would satisfy HACS either. Nothing in
this repo can fix that one — it needs a HACS frontend release. HA's own integration list is the
surface to judge by.

## Updating the artwork

The mark is deliberately the STR Concierge house with a key in place of the cloche, in the
same teal palette, so the two integrations read as a family.

1. Edit `icon.svg` (the glyph is the white house; everything drawn in black inside the mask
   is cut out of it).
2. Run `python scripts/build_brand.py` (needs `pip install playwright fonttools` and a
   Chromium; set `CHROMIUM=/path/to/chrome` if Playwright's default isn't installed). It
   regenerates every PNG plus `logo.svg` / `dark_logo.svg`, using the dark palette for the
   `dark_*` variants and Liberation Sans Bold for the wordmark.
3. Ship it in a normal release — HA picks up the new images once the package is installed.

## Design guidance

Constraints worth preserving if the mark is ever reworked, drawn from the
[brands image specs](https://github.com/home-assistant/brands#adding-a-new-brand):

- **Icons** must be square (1:1). Transparency preferred.
- **Logos** should be landscape, with the shortest side 128–256 px (256–512 px for `@2x`), and an
  aspect ratio that respects the logo itself rather than being padded to a fixed box.
- Trim empty space — images should contain the minimum amount of padding.
- PNG only, lossless, optimised for web.
- The icon has to work small: it should still read at 32 × 32 favicon scale.
- Provide dark variants whenever the light artwork would lose contrast on a dark background.
