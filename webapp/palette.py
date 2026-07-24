"""Per-track accent colour, pulled from the beatmap cover.

The osu! asset CDN serves no `Access-Control-Allow-Origin` header, so a browser
canvas that draws a cover becomes tainted and `getImageData` throws. The colour
therefore has to be extracted server-side. It is cheap: the `list` cover variant
is ~9 KB (the full `cover@2x` is ~175 KB), the result never changes, and one
lookup serves every user from then on.

What comes back is deliberately two colours, not one. `accent` fills large
shapes and only needs 3:1 against the page; `accent_text` is the same hue pushed
until it clears 4.5:1, so small text on the dark surface stays readable no matter
what the artwork looks like.
"""
from __future__ import annotations

import asyncio
import colorsys
import logging
from collections import OrderedDict
from io import BytesIO

log = logging.getLogger("spotiosu.palette")

try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow is in requirements.txt
    Image = None

# The surface an accent *fill* is judged against. Keep in step with --bg in style.css.
SURFACE = (0x0C, 0x0C, 0x11)
# Accent *text* is judged against a lighter reference, because it does not sit on
# the page background: the artist line sits on scrimmed cover art. Measured worst
# case there was rgb(27,27,31), so judging against --bg overstated the contrast by
# about half a point and let the artist name land at 4.07:1. This is that surface
# plus margin.
TEXT_SURFACE = (0x2A, 0x2A, 0x30)
# Used when a cover is missing, greyscale, or fails to decode.
FALLBACK = (0xFF, 0x66, 0xAA)

MIN_FILL_CONTRAST = 3.0   # large shapes
MIN_TEXT_CONTRAST = 4.5   # small text
SAMPLE = 24               # cover is resized to SAMPLE x SAMPLE before sampling
HUE_BUCKETS = 12


def _luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG relative luminance."""
    channels = []
    for value in rgb:
        c = value / 255.0
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: tuple[int, int, int], bg: tuple[int, int, int] = SURFACE) -> float:
    a, b = _luminance(fg), _luminance(bg)
    lo, hi = min(a, b), max(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def _lift(rgb: tuple[int, int, int], target: float,
          against: tuple[int, int, int] = SURFACE) -> tuple[int, int, int]:
    """Raise a colour's lightness just far enough to clear `target` contrast.

    Hue and saturation are preserved so the accent still reads as "this cover".
    Brightening is the only direction that works here: the surface is nearly
    black, so darkening moves the colour towards it.
    """
    h, l, s = colorsys.rgb_to_hls(*(c / 255.0 for c in rgb))
    best = rgb
    while l < 0.98:
        if contrast(best, against) >= target:
            return best
        l = min(0.98, l + 0.02)
        best = tuple(round(c * 255) for c in colorsys.hls_to_rgb(h, l, s))
    return best


def dominant_colour(data: bytes) -> tuple[int, int, int]:
    """The most present *vivid* colour in an image, ignoring greys.

    Averaging every pixel gives mud, because most covers are mostly background.
    Instead pixels vote for a hue bucket weighted by how colourful they are, and
    the winning bucket's members are averaged.
    """
    if Image is None:
        return FALLBACK
    with Image.open(BytesIO(data)) as img:
        # tobytes() rather than getdata(): the latter is deprecated for removal.
        raw = img.convert("RGB").resize((SAMPLE, SAMPLE), Image.BILINEAR).tobytes()

    buckets: dict[int, list[float]] = {}
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        if s < 0.18 or v < 0.12 or v > 0.97:
            continue  # grey, near-black or blown-out: carries no hue
        # Squared, so a small vivid region beats a large washed-out one. Measured:
        # without it, skin tones in anime artwork win on area and everything comes
        # out the same muddy brown.
        weight = s * s * v
        slot = buckets.setdefault(int(h * HUE_BUCKETS) % HUE_BUCKETS, [0.0, 0.0, 0.0, 0.0])
        slot[0] += r * weight
        slot[1] += g * weight
        slot[2] += b * weight
        slot[3] += weight

    if not buckets:
        return FALLBACK  # a genuinely greyscale cover
    _slot, best = max(buckets.items(), key=lambda kv: kv[1][3])
    total = best[3] or 1.0
    return tuple(max(0, min(255, round(best[i] / total))) for i in range(3))


def accents_for(data: bytes | None) -> dict[str, str]:
    """The colour pair the interface actually uses."""
    base = dominant_colour(data) if data else FALLBACK

    # Covers are often washed out; a limp accent reads as a rendering bug.
    h, l, s = colorsys.rgb_to_hls(*(c / 255.0 for c in base))
    if s < 0.45:
        base = tuple(round(c * 255) for c in colorsys.hls_to_rgb(h, l, 0.45))

    fill = _lift(base, MIN_FILL_CONTRAST)
    text = _lift(fill, MIN_TEXT_CONTRAST, against=TEXT_SURFACE)

    # A label printed *on* the accent needs its own decision: whichever of near
    # black or near white reads better against this particular fill.
    on_dark, on_light = (0x0B, 0x0B, 0x0E), (0xFF, 0xFF, 0xFF)
    on = on_dark if contrast(on_dark, fill) >= contrast(on_light, fill) else on_light

    return {
        "accent": _hex(fill),
        "accent_text": _hex(text),
        "on_accent": _hex(on),
        "contrast_fill": round(contrast(fill), 2),
        "contrast_text": round(contrast(text, TEXT_SURFACE), 2),
        "contrast_on": round(contrast(on, fill), 2),
    }


class AccentCache:
    """Bounded memo of set_id -> accent pair."""

    def __init__(self, api, max_entries: int = 2000) -> None:
        self._api = api
        self._max = max_entries
        self._cache: OrderedDict[int, dict[str, str]] = OrderedDict()

    async def get(self, set_id: int) -> dict[str, str]:
        key = int(set_id)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        data = await self._api.download_cover(key)
        try:
            result = await asyncio.to_thread(accents_for, data)
        except Exception as exc:  # a corrupt or unsupported cover must not 500
            log.warning("accent extraction failed for set %s: %s", key, exc)
            result = accents_for(None)

        self._cache[key] = result
        if len(self._cache) > self._max:
            self._cache.popitem(last=False)
        return result
