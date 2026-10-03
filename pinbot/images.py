"""Generate 1000x1500 branded pin images (soft gradient + headline). Free, needs only Pillow."""
from PIL import Image, ImageDraw, ImageFont, ImageOps
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


def gradient(idx):
    top, bot = PALETTES[idx % len(PALETTES)]
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    return img, d


def load_photo(path):
    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.split()[-1])
        im = bg
    return im.convert("RGB")


def photo_card(img, d, photo, box):
    """White rounded card with the photo scaled to fit (never cropped), centered."""
    x0, y0, x1, y1 = box
    d.rounded_rectangle(box, radius=36, fill="white")
    fitted = ImageOps.contain(load_photo(photo), (x1 - x0 - 40, y1 - y0 - 40))
    img.paste(fitted, (x0 + (x1 - x0 - fitted.width) // 2, y0 + (y1 - y0 - fitted.height) // 2))


def make_photo_pin(p, path, photos, idx=0):
    """Photo-led layout: short headline on top, 1 big photo or a collage of up to 3 below."""
    img, d = gradient(idx)
    y = 170
    for line in wrap(d, p["hook"], font(80), W - 160):
        d.text((W // 2, y), line, font=font(80), fill=(70, 40, 55), anchor="mm")
        y += 100
    top, bottom = y + 40, H - 230
    if len(photos) == 1:
        photo_card(img, d, photos[0], (70, top, W - 70, bottom))
    else:
        n, gap = min(len(photos), 3), 24
        cw = (W - 140 - gap * (n - 1)) // n
        mid = (top + bottom) // 2
        for i, ph in enumerate(photos[:n]):
            x = 70 + i * (cw + gap)
            photo_card(img, d, ph, (x, mid - 330, x + cw, mid + 330))
    d.text((W // 2, H - 150), p["title"][:60], font=font(34), fill=(70, 40, 55), anchor="mm")
    d.text((W // 2, H - 95), get("SITE_NAME", "My Picks"), font=font(28), fill=(120, 80, 90), anchor="mm")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def make_pin(p, path, idx=0, photos=()):
    if photos:
        return make_photo_pin(p, path, photos, idx)
    img, d = gradient(idx)
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
