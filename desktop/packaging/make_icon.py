"""Draw the Lyra mark and write every raster the build needs.

The geometry matches ``packaging/lyra-mark.svg`` (64-unit viewBox): a deep indigo tile, an open
orbit ring (violet -> teal) whose gap holds a four-point star — Vega, the brightest star of the
Lyra constellation. The ring is also the voice orb in Lyra's window, so the icon and the app
share one shape.

Pillow has no SVG renderer, so the mark is drawn at 1024 px and scaled down. Outputs:
    packaging/lyra.ico, packaging/lyra.png            app / installer icon
    packaging/wizard-side.bmp, wizard-small.bmp/.png  installer artwork
    app/web/static/lyra-logo.png                      tray icon
"""

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

S = 16  # 64-unit viewBox -> 1024 px
N = 64 * S
HERE = Path(__file__).parent
STATIC = HERE.parent / "app" / "web" / "static"

CX, CY, R, STROKE = 31, 34, 15.5, 5.5
GAP = (-72, -18)                    # degrees (clockwise from +x) left open for the star
STAR = (46.2, 18.8, 6.2, 1.7)       # centre x, y, outer and inner radius


def linear(stops, p0, p1) -> Image.Image:
    """RGB image filled with a linear gradient from p0 to p1 (viewBox units)."""
    y, x = np.mgrid[0:N, 0:N].astype(np.float32) / S
    (ax, ay), (bx, by) = p0, p1
    t = ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / ((bx - ax) ** 2 + (by - ay) ** 2)
    t = np.clip(t, 0, 1)[..., None]
    out = np.zeros((N, N, 3), np.float32)
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        k = np.clip((t - t0) / max(t1 - t0, 1e-6), 0, 1)
        seg = (t >= t0) & (t <= t1)
        out = np.where(seg, np.array(c0, np.float32) + (np.array(c1) - np.array(c0)) * k, out)
    return Image.fromarray(out.clip(0, 255).astype(np.uint8))


def hexrgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def draw() -> Image.Image:
    # tile
    tile = linear([(0, hexrgb("1B1638")), (1, hexrgb("090B12"))], (4, 2), (60, 62)).convert("RGBA")
    mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, N - 1, N - 1], radius=15 * S, fill=255)
    tile.putalpha(mask)
    edge = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle([S // 2, S // 2, N - 1 - S // 2, N - 1 - S // 2], radius=int(14.5 * S),
                                           outline=(255, 255, 255, 20), width=S)
    tile.alpha_composite(edge)

    # soft core glow inside the ring
    glow = Image.new("L", (N, N), 0)
    ImageDraw.Draw(glow).ellipse([(CX - 9) * S, (CY - 9) * S, (CX + 9) * S, (CY + 9) * S], fill=110)
    glow = glow.filter(ImageFilter.GaussianBlur(5 * S))
    core = Image.new("RGBA", (N, N), hexrgb("7C6CFF") + (0,))
    core.putalpha(glow)
    tile.alpha_composite(core)

    # orbit ring: arc mask filled with the violet -> teal gradient, round caps
    ring = Image.new("L", (N, N), 0)
    d = ImageDraw.Draw(ring)
    w = STROKE * S
    box = [(CX - R - STROKE / 2) * S, (CY - R - STROKE / 2) * S, (CX + R + STROKE / 2) * S, (CY + R + STROKE / 2) * S]
    d.arc(box, start=GAP[1], end=360 + GAP[0], fill=255, width=int(w))
    for angle in GAP:
        a = math.radians(angle)
        x, y = (CX + R * math.cos(a)) * S, (CY + R * math.sin(a)) * S
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=255)
    orbit = linear([(0, hexrgb("8B5CF6")), (.55, hexrgb("7C6CFF")), (1, hexrgb("2DD4BF"))], (14, 50), (48, 18))
    orbit = orbit.convert("RGBA")
    orbit.putalpha(ring)
    tile.alpha_composite(orbit)

    # the star
    sx, sy, outer, inner = STAR
    pts = []
    for i in range(8):
        a = math.radians(-90 + i * 45)
        rr = outer if i % 2 == 0 else inner
        pts.append(((sx + rr * math.cos(a)) * S, (sy + rr * math.sin(a)) * S))
    star = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(star).polygon(pts, fill=hexrgb("E6FFFB") + (255,))
    halo = star.filter(ImageFilter.GaussianBlur(1.6 * S))
    tile.alpha_composite(Image.blend(Image.new("RGBA", (N, N), (0, 0, 0, 0)), halo, 0.55))
    tile.alpha_composite(star)
    return tile


if __name__ == "__main__":
    big = draw()
    big.resize((512, 512), Image.LANCZOS).save(HERE / "lyra.png")
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    big.resize((256, 256), Image.LANCZOS).save(HERE / "lyra.ico", sizes=[(s, s) for s in sizes])
    big.resize((128, 128), Image.LANCZOS).save(STATIC / "lyra-logo.png")
    # installer wizard artwork: logo and wordmark space on Lyra's night background
    side = linear([(0, hexrgb("141129")), (1, hexrgb("07080D"))], (0, 0), (40, 64)).resize((164, 314)).convert("RGBA")
    side.alpha_composite(big.resize((96, 96), Image.LANCZOS), (34, 88))
    font = None
    for name in ("segoeuisb.ttf", "seguisb.ttf", "segoeui.ttf", "DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(name, 30)
            break
        except OSError:
            continue
    if font is not None:
        d = ImageDraw.Draw(side)
        d.text((82, 212), "Lyra", fill=(236, 238, 245, 255), font=font, anchor="mm")
        small_font = ImageFont.truetype(font.path, 11)
        d.text((82, 238), "VOICE ASSISTANT", fill=(154, 163, 178, 255), font=small_font, anchor="mm")
    side.convert("RGB").save(HERE / "wizard-side.bmp")
    small_logo = big.resize((58, 58), Image.LANCZOS)
    small_logo.save(HERE / "wizard-small.png")
    small = Image.new("RGB", (58, 58), (255, 255, 255))
    small.paste(small_logo, (0, 0), small_logo)
    small.save(HERE / "wizard-small.bmp")
    print("icons written to", HERE)
