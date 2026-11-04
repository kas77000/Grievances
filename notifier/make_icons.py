"""Generate the Order Alert icons (app logo + severity badges) into notifier/assets/.

    python notifier/make_icons.py

The logo is the Desk's three-bar mark on a dark tile; the .ico is also used for the form's
desktop shortcut so the whole tool looks like one product.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "assets"
BG = (13, 16, 22)
BARS = [((106, 134, 251), 0.55), ((229, 86, 74), 0.75), ((152, 162, 178), 0.95)]  # accent, alert, muted
SEVERITY = {"critical": (176, 125, 224), "high": (229, 86, 74), "medium": (224, 165, 48), "low": (106, 134, 251)}
S = 1024  # draw large, downsample for smooth edges


def logo() -> Image.Image:
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, S - 1, S - 1), radius=S * 0.22, fill=BG)
    bw, gap, base = S * 0.16, S * 0.07, S * 0.78
    x = (S - (3 * bw + 2 * gap)) / 2
    for color, height in BARS:
        d.rounded_rectangle((x, base - height * S * 0.58, x + bw, base), radius=bw * 0.28, fill=color)
        x += bw + gap
    return im


def badge(color) -> Image.Image:
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((0, 0, S - 1, S - 1), fill=color)
    try:
        font = ImageFont.truetype("arialbd.ttf", int(S * 0.62))
    except OSError:
        font = ImageFont.load_default(int(S * 0.62))
    d.text((S / 2, S / 2 + S * 0.02), "!", font=font, fill="white", anchor="mm")
    return im


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    big = logo()
    big.resize((256, 256), Image.LANCZOS).save(OUT / "order-alert.png")
    big.save(OUT / "order-alert.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    for name, color in SEVERITY.items():
        badge(color).resize((128, 128), Image.LANCZOS).save(OUT / f"sev-{name}.png")
    print("icons in", OUT)
