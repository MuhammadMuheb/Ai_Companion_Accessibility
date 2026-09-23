"""Draw the MD logo (same geometry as assets/img/md-logo.svg) and save md.ico / md.png.

Pillow has no SVG renderer, so the mark is drawn directly at 1024 px and scaled down."""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

S = 16  # 64-unit viewBox -> 1024 px
N = 64 * S
HERE = Path(__file__).parent
INK = (10, 11, 18, 255)


def gradient() -> Image.Image:
    y, x = np.mgrid[0:N, 0:N].astype(np.float32)
    # along the diagonal from (6,4) to (60,62), like the SVG's linearGradient
    ax, ay, bx, by = 6 * S, 4 * S, 60 * S, 62 * S
    t = ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / ((bx - ax) ** 2 + (by - ay) ** 2)
    t = np.clip(t, 0, 1)[..., None]
    stops = [(0, (169, 156, 255)), (.45, (123, 108, 255)), (1, (47, 212, 196))]
    out = np.zeros((N, N, 3), np.float32)
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        k = np.clip((t - t0) / (t1 - t0), 0, 1)
        seg = (t >= t0) & (t <= t1)
        out = np.where(seg, np.array(c0) + (np.array(c1) - np.array(c0)) * k, out)
    shine = np.clip(1 - y / (34 * S), 0, 1)[..., None] * 0.35 * 255
    out = out + (255 - out) * (shine / 255)
    return Image.fromarray(out.clip(0, 255).astype(np.uint8)).convert("RGBA")


def draw() -> Image.Image:
    img = gradient()
    mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, N - 1, N - 1], radius=17 * S, fill=255)
    img.putalpha(mask)

    d = ImageDraw.Draw(img)
    w = int(5.2 * S)
    p = lambda x, y: (x * S, y * S)
    d.line([p(12, 45), p(12, 19), p(22.5, 34), p(33, 19), p(33, 45)], fill=INK, width=w, joint="curve")
    d.line([p(33, 19), p(38.5, 19)], fill=INK, width=w)
    d.line([p(33, 45), p(38.5, 45)], fill=INK, width=w)
    # Pillow grows an arc's stroke inward from its box, so widen the box by half the stroke
    h = 2.6
    d.arc([p(38.5 - 13 - h, 19 - h), p(38.5 + 13 + h, 45 + h)], start=-90, end=90, fill=INK, width=w)
    for cx, cy in [(12, 45), (12, 19), (33, 19), (33, 45)]:  # round caps and corners
        r = w / 2
        d.ellipse([cx * S - r, cy * S - r, cx * S + r, cy * S + r], fill=INK)
    d.ellipse([p(51.5 - 3, 14.5 - 3), p(51.5 + 3, 14.5 + 3)], fill=INK)
    return img


if __name__ == "__main__":
    big = draw()
    big.resize((512, 512), Image.LANCZOS).save(HERE / "md.png")
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    big.resize((256, 256), Image.LANCZOS).save(HERE / "md.ico", sizes=[(s, s) for s in sizes])
    # installer wizard artwork
    side = Image.new("RGBA", (164, 314), (7, 8, 13, 255))
    logo = big.resize((112, 112), Image.LANCZOS)
    side.alpha_composite(logo, (26, 60))
    side.convert("RGB").save(HERE / "wizard-side.bmp")
    big.resize((58, 58), Image.LANCZOS).convert("RGBA").save(HERE / "wizard-small.png")
    small = Image.new("RGB", (58, 58), (255, 255, 255))
    small.paste(big.resize((58, 58), Image.LANCZOS), (0, 0), big.resize((58, 58), Image.LANCZOS))
    small.save(HERE / "wizard-small.bmp")
    print("icons written to", HERE)
