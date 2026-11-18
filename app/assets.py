"""Gera as artes do template (faixa pôr do sol com palmeiras, helicóptero e carro) — desenho original, sem logo da Rockstar."""
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).parent / "assets"
OUT.mkdir(exist_ok=True)

SUNSET = [(0.0, (40, 6, 60)), (0.3, (170, 20, 140)), (0.58, (255, 31, 168)), (0.82, (255, 110, 60)), (1.0, (255, 196, 60))]
SIL = (14, 4, 24, 255)


def _lerp(stops, t):
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if a <= t <= b:
            k = (t - a) / (b - a)
            return tuple(int(ca[i] + (cb[i] - ca[i]) * k) for i in range(3))
    return stops[-1][1]


def palm(d: ImageDraw.ImageDraw, x, base, h, lean=1, s=1.0):
    # tronco curvo
    pts_l, pts_r = [], []
    for i in range(21):
        t = i / 20
        cx = x + lean * 40 * s * t * t
        cy = base - h * t
        w = (9 - 5 * t) * s
        pts_l.append((cx - w, cy)); pts_r.append((cx + w, cy))
    d.polygon(pts_l + pts_r[::-1], fill=SIL)
    tx, ty = pts_l[-1][0] + 5 * s, base - h
    # folhas
    for ang in (-170, -140, -110, -70, -40, -10, 200, 160):
        a = math.radians(ang)
        L = 70 * s
        poly = []
        for i in range(13):
            t = i / 12
            px = tx + math.cos(a) * L * t
            py = ty + math.sin(a) * L * t + 30 * s * t * t
            poly.append((px, py - 7 * s * math.sin(math.pi * t)))
        for i in range(12, -1, -1):
            t = i / 12
            px = tx + math.cos(a) * L * t
            py = ty + math.sin(a) * L * t + 30 * s * t * t
            poly.append((px, py + 7 * s * math.sin(math.pi * t)))
        d.polygon(poly, fill=SIL)


def heli(d, x, y, s=1.0):
    d.ellipse([x, y, x + 46 * s, y + 20 * s], fill=SIL)
    d.polygon([(x + 40 * s, y + 8 * s), (x + 95 * s, y + 6 * s), (x + 95 * s, y + 11 * s), (x + 40 * s, y + 14 * s)], fill=SIL)
    d.rectangle([x - 20 * s, y - 6 * s, x + 70 * s, y - 4 * s], fill=SIL)
    d.rectangle([x + 22 * s, y - 5 * s, x + 25 * s, y], fill=SIL)
    d.rectangle([x + 90 * s, y - 2 * s, x + 93 * s, y + 14 * s], fill=SIL)
    d.rectangle([x + 4 * s, y + 24 * s, x + 44 * s, y + 26 * s], fill=SIL)


def car(d, x, y, s=1.0):
    d.rounded_rectangle([x, y, x + 150 * s, y + 22 * s], radius=int(6 * s), fill=SIL)
    d.polygon([(x + 35 * s, y), (x + 55 * s, y - 18 * s), (x + 100 * s, y - 18 * s), (x + 118 * s, y)], fill=SIL)
    for wx in (x + 30 * s, x + 118 * s):
        d.ellipse([wx - 13 * s, y + 10 * s, wx + 13 * s, y + 36 * s], fill=SIL)


def sky(w, h, sun=True):
    img = Image.new("RGBA", (w, h))
    px = img.load()
    for y in range(h):
        c = _lerp(SUNSET, y / max(1, h - 1))
        for x in range(w):
            px[x, y] = (*c, 255)
    d = ImageDraw.Draw(img)
    if sun:
        r = int(h * 0.55)
        cx, cy = int(w * 0.68), int(h * 0.82)
        sun_img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sun_img)
        sd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 214, 120, 235))
        # listras de pôr do sol retrô
        for i in range(6):
            yy = cy - r * 0.1 + i * r * 0.18
            sd.rectangle([cx - r, yy, cx + r, yy + 3 + i * 2], fill=(0, 0, 0, 0))
        img.alpha_composite(sun_img)
    return img, d


def band(w=1080, h=150):
    img, _ = sky(w, h)
    d = ImageDraw.Draw(img)
    # skyline baixo
    import random
    random.seed(6)
    x = 180
    while x < w - 160:
        bw = random.randint(18, 40); bh = random.randint(14, 48)
        d.rectangle([x, h - bh, x + bw, h], fill=(22, 6, 38, 255))
        x += bw + random.randint(2, 8)
    palm(d, 60, h, h * 0.95, 1, 0.9)
    palm(d, 130, h, h * 0.7, -1, 0.65)
    palm(d, w - 70, h, h * 0.9, -1, 0.85)
    heli(d, w * 0.18, h * 0.18, 0.55)
    car(d, w * 0.72, h - 26, 0.6)
    # brilho nas bordas
    edge = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ed = ImageDraw.Draw(edge)
    ed.rectangle([0, 0, w, 5], fill=(60, 225, 255, 255)); ed.rectangle([0, h - 5, w, h], fill=(255, 31, 168, 255))
    img.alpha_composite(edge)
    return img


def frame(w=1080, h=1920):
    """Moldura para os layouts sem facecam: degradê roxo em cima e embaixo + palmeiras nos cantos."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = img.load()
    for y in range(h):
        t_top = max(0.0, 1 - y / 420)
        t_bot = max(0.0, (y - (h - 520)) / 520)
        a = int(235 * max(t_top, t_bot) ** 1.4)
        c = (30, 4, 48) if t_top >= t_bot else (120, 10, 100)
        for x in range(w):
            px[x, y] = (*c, a)
    d = ImageDraw.Draw(img)
    palm(d, 40, h, 360, 1, 1.5)
    palm(d, w - 60, h, 300, -1, 1.25)
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rectangle([0, h - 10, w, h], fill=(60, 225, 255, 200))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(8)))
    return img


def build():
    p1, p2 = OUT / "band.png", OUT / "frame.png"
    if not p1.exists():
        band().save(p1)
    if not p2.exists():
        frame().save(p2)
    return p1, p2


if __name__ == "__main__":
    print(build())
