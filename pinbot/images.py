"""Generate 1000x1500 branded pin images (soft gradient + headline). Free, needs only Pillow."""
from PIL import Image, ImageDraw, ImageFont
from .config import get

W, H = 1000, 1500
PALETTES = [((255, 228, 225), (250, 190, 205)), ((255, 240, 220), (245, 205, 170)), ((232, 240, 228), (190, 215, 195))]
FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def font(size):
    for f in FONTS:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def wrap(draw, text, fnt, width):
    lines, cur = [], ""
    for word in text.split():
        t = f"{cur} {word}".strip()
        if draw.textlength(t, font=fnt) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = word
    return lines + [cur]


def make_pin(p, path, idx=0):
    top, bot = PALETTES[idx % len(PALETTES)]
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    d.rounded_rectangle([60, 60, W - 60, H - 60], radius=40, outline=(255, 255, 255), width=6)
    d.text((W // 2, 170), p.get("category", "Skincare").upper(), font=font(36), fill=(120, 80, 90), anchor="mm")
    y = 420
    for line in wrap(d, p["hook"], font(84), W - 220):
        d.text((W // 2, y), line, font=font(84), fill=(70, 40, 55), anchor="mm")
        y += 110
    y += 60
    for line in wrap(d, p["title"], font(44), W - 240):
        d.text((W // 2, y), line, font=font(44), fill=(120, 80, 90), anchor="mm")
        y += 64
    d.rounded_rectangle([300, H - 360, W - 300, H - 270], radius=45, fill=(70, 40, 55))
    d.text((W // 2, H - 315), "Read more", font=font(40), fill="white", anchor="mm")
    d.text((W // 2, H - 150), get("SITE_NAME", "My Picks"), font=font(34), fill=(120, 80, 90), anchor="mm")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
