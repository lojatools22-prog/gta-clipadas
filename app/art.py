"""Artes procedurais dos 36 templates GTA Clipadas (desenho próprio, sem material da Rockstar).
Cada função devolve uma imagem RGBA 1080x1920 que é sobreposta ao vídeo."""
import math, random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .assets import palm, heli, car, band, SUNSET, _lerp

W, H = 1080, 1920
FONTS = Path(__file__).parent / "fonts"
OUT = Path(__file__).parent / "assets" / "tpl"

PINK = (255, 31, 168); CYAN = (60, 225, 255); YELLOW = (255, 196, 40); ORANGE = (255, 120, 50)
PURPLE = (155, 22, 200); GREEN = (60, 230, 120); RED = (255, 50, 60); BLUE = (40, 110, 255); WHITE = (255, 255, 255)
NEONS = {"rosa": PINK, "ciano": CYAN, "roxo": (190, 70, 255), "verde": GREEN, "laranja": ORANGE, "amarelo": YELLOW}


def font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


BOWLBY = "BowlbyOne_400Regular.ttf"
ANTON = "Anton_400Regular.ttf"
MONO = "SpecialElite_400Regular.ttf"
SCRIPT = "KaushanScript_400Regular.ttf"


SCENES = Path(__file__).parent / "scenes"
_scene_cache = {}


def scene(name: str) -> Image.Image:
    """Ilustração completa 1080x1920 (praia, noite, marina)."""
    if name not in _scene_cache:
        _scene_cache[name] = Image.open(SCENES / f"{name}.jpg").convert("RGBA").resize((W, H), Image.LANCZOS)
    return _scene_cache[name].copy()


def strip(name: str, y0: int, h: int, dark: float = 0.0) -> Image.Image:
    """Faixa horizontal recortada da ilustração (coordenadas em 1080x1920)."""
    im = scene(name).crop((0, y0, W, y0 + h))
    if dark:
        im.alpha_composite(Image.new("RGBA", im.size, (10, 2, 22, int(255 * dark))))
    return im


def fade_top(im: Image.Image, px: int) -> Image.Image:
    a = im.getchannel("A")
    g = Image.linear_gradient("L").resize((im.width, px))
    mask = Image.new("L", im.size, 255); mask.paste(g, (0, 0))
    im.putalpha(Image.composite(a, Image.new("L", im.size, 0), mask))
    return im


def scene_band(name, sy, h=170):
    b = strip(name, sy, h)
    d = ImageDraw.Draw(b)
    d.rectangle([0, 0, W, 5], fill=(*CYAN, 255)); d.rectangle([0, h - 6, W, h], fill=(*PINK, 255))
    return b


def blank():
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def glow_rect(img, box, color, width=8, blur=14, radius=0):
    g = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(g)
    d.rounded_rectangle(box, radius=radius, outline=(*color, 255), width=width + 6)
    g = g.filter(ImageFilter.GaussianBlur(blur))
    img.alpha_composite(g)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=radius, outline=(*color, 255), width=width)
    d.rounded_rectangle([box[0] + width // 2, box[1] + width // 2, box[2] - width // 2, box[3] - width // 2],
                        radius=max(0, radius - 4), outline=(255, 255, 255, 200), width=2)


def glow_line(img, y, color, width=8):
    glow_rect(img, [-20, y - width // 2, W + 20, y + width // 2], color, width=width, blur=16)


def text_center(d, xy, txt, f, fill, stroke=6, stroke_fill=(0, 0, 0), anchor="mm"):
    d.text(xy, txt, font=f, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill, anchor=anchor)


def paste(img, part, x, y):
    img.alpha_composite(part, (int(x), int(y)))


def star(d, cx, cy, r, fill, outline=(0, 0, 0)):
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    d.polygon(pts, fill=fill, outline=outline)
    d.line(pts + [pts[0]], fill=outline, width=4)


def sunset_bg(w=W, h=H, sun=True):
    img = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=(*_lerp(SUNSET, y / (h - 1)), 255))
    if sun:
        r = int(w * 0.32); cx, cy = int(w * 0.55), int(h * 0.62)
        s = Image.new("RGBA", (w, h), (0, 0, 0, 0)); sd = ImageDraw.Draw(s)
        sd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 210, 90, 255))
        for i in range(7):
            yy = cy + i * r * 0.14
            sd.rectangle([cx - r, yy, cx + r, yy + 6 + i * 3], fill=(0, 0, 0, 0))
        img.alpha_composite(s)
    return img


# ---------------- Vice Beach ----------------
def vb_band_at(y, name="praia", sy=1150):
    img = blank(); b = scene_band(name, sy)
    paste(img, b, 0, y - 10); return img


def vb_float():
    img = blank(); d = ImageDraw.Draw(img)
    # moldura da facecam flutuante
    glow_rect(img, (40, 190, 480, 440), PINK, width=8, radius=18)
    bottom = fade_top(strip("praia", 1360, 560), 220)
    paste(img, bottom, 0, H - 560)
    return img


def vb_moldura():
    img = blank(); d = ImageDraw.Draw(img)
    paste(img, strip("praia", 180, 330), 0, 0)
    glow_rect(img, (24, 24, W - 24, H - 24), PINK, width=10, radius=30)
    glow_line(img, 330, CYAN, 6)
    return img


# ---------------- Neon Night ----------------
def neon_night(color):
    img = blank(); d = ImageDraw.Draw(img)
    glow_rect(img, (16, 16, W - 16, H - 16), color, width=8, radius=26)
    glow_line(img, 608, color, 10)
    # placa neon do título
    box = (90, 548, W - 90, 668)
    pl = strip("noite", 760, box[3] - box[1], dark=0.45).crop((box[0], 0, box[2], box[3] - box[1]))
    m = Image.new("L", pl.size, 0); ImageDraw.Draw(m).rounded_rectangle((0, 0, pl.width - 1, pl.height - 1), radius=24, fill=255)
    pl.putalpha(m); paste(img, pl, box[0], box[1])
    glow_rect(img, box, color, width=6, radius=24, blur=12)
    return img


# ---------------- Procurado ----------------
def wanted(n, busted=False):
    img = blank(); d = ImageDraw.Draw(img)
    bar = Image.new("RGBA", (W, H), (0, 0, 0, 0)); bd = ImageDraw.Draw(bar)
    img.alpha_composite(bar)
    paste(img, strip("noite", 960, 200, dark=0.35), 0, 560)
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0)); ImageDraw.Draw(shade).rectangle([0, 660, W, 760], fill=(0, 0, 0, 150))
    img.alpha_composite(shade); d = ImageDraw.Draw(img)
    glow_line(img, 560, RED, 6); glow_line(img, 760, BLUE, 6)
    for i in range(5):
        cx = W - 110 - i * 110
        star(d, cx, 704, 42, (255, 255, 255, 255) if i < n else (60, 60, 60, 255))
    d.text((60, 704), "PROCURADO", font=font(BOWLBY, 46), fill=(255, 60, 70), stroke_width=5, stroke_fill=(0, 0, 0), anchor="lm")
    if busted:
        for x in range(90, W, 150):
            d.rectangle([x, 0, x + 26, 520], fill=(30, 30, 36, 170))
    return img


def siren(color, side):
    img = blank()
    g = Image.new("RGBA", (W, H), (0, 0, 0, 0)); gd = ImageDraw.Draw(g)
    x0 = -80 if side == "l" else W - 120
    gd.ellipse([x0, 300, x0 + 200, H - 300], fill=(*color, 200))
    img.alpha_composite(g.filter(ImageFilter.GaussianBlur(70)))
    return img


# ---------------- Missão ----------------
def mission_panel(accent=YELLOW):
    img = blank(); d = ImageDraw.Draw(img)
    p = Image.new("RGBA", (W, H), (0, 0, 0, 0)); pd = ImageDraw.Draw(p)
    img.alpha_composite(p)
    paste(img, strip("praia", 1180, 120, dark=0.62), 0, 548)
    d.rectangle([0, 548, 16, 668], fill=(*accent, 255))
    d.rectangle([0, 664, W, 668], fill=(*accent, 255))
    d.text((40, 572), "OBJETIVO", font=font(ANTON, 30), fill=(*accent, 255), anchor="lm")
    return img


# ---------------- Celular ----------------
PHONE = (110, 200, 970, 1780)
SCREEN = (150, 300, 930, 1680)


def phone_body(bg="praia"):
    img = scene(bg) if bg else blank()
    d = ImageDraw.Draw(img)
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0)); sd = ImageDraw.Draw(sh)
    sd.rounded_rectangle((PHONE[0] + 14, PHONE[1] + 24, PHONE[2] + 14, PHONE[3] + 24), radius=90, fill=(0, 0, 0, 160))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(18)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(PHONE, radius=90, fill=(18, 10, 30, 255), outline=(*PINK, 255), width=6)
    # buraco da tela
    hole = Image.new("L", (W, H), 255); hd = ImageDraw.Draw(hole)
    hd.rounded_rectangle(SCREEN, radius=46, fill=0)
    a = img.getchannel("A"); a = Image.composite(a, Image.new("L", (W, H), 0), hole); img.putalpha(a)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((470, 236, 610, 270), radius=17, fill=(0, 0, 0, 255))  # câmera
    # barra de status
    st = Image.new("RGBA", (W, H), (0, 0, 0, 0)); sd = ImageDraw.Draw(st)
    sd.rectangle((SCREEN[0], SCREEN[1], SCREEN[2], SCREEN[1] + 60), fill=(0, 0, 0, 120))
    sd.text((SCREEN[0] + 40, SCREEN[1] + 30), "19:19", font=font(ANTON, 30), fill=WHITE, anchor="lm")
    sd.text((SCREEN[2] - 40, SCREEN[1] + 30), "5G", font=font(ANTON, 26), fill=WHITE, anchor="rm")
    img.alpha_composite(st)
    return img


PHONE_BG = {"chat": "praia", "chamada": "noite", "social": "marina", "gps": "noite", "foto": "marina", "banco": "praia"}


def phone_ui(kind):
    img = phone_body(PHONE_BG.get(kind, "praia")); d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = SCREEN
    ui = Image.new("RGBA", (W, H), (0, 0, 0, 0)); u = ImageDraw.Draw(ui)
    if kind == "chat":
        u.rectangle((x0, y0 + 60, x1, y0 + 170), fill=(20, 12, 36, 235))
        u.ellipse((x0 + 30, y0 + 80, x0 + 100, y0 + 150), fill=(*PINK, 255))
        u.text((x0 + 120, y0 + 115), "Clipadas", font=font(ANTON, 40), fill=WHITE, anchor="lm")
        u.rounded_rectangle((x0 + 30, y1 - 330, x1 - 30, y1 - 120), radius=36, fill=(255, 255, 255, 240))
        u.rounded_rectangle((x0 + 20, y1 - 100, x1 - 20, y1 - 30), radius=35, fill=(20, 12, 36, 230))
        u.text((x0 + 60, y1 - 65), "Mensagem...", font=font(ANTON, 30), fill=(180, 170, 200), anchor="lm")
    elif kind == "chamada":
        u.rectangle((x0, y0 + 60, x1, y0 + 420), fill=(10, 6, 20, 200))
        u.ellipse((W // 2 - 90, y0 + 100, W // 2 + 90, y0 + 280), fill=(*PINK, 255), outline=WHITE, width=6)
        u.text((W // 2, y0 + 340), "chamando...", font=font(ANTON, 40), fill=WHITE, anchor="mm")
        for cx, col in ((W // 2 - 190, RED), (W // 2 + 190, GREEN)):
            u.ellipse((cx - 70, y1 - 230, cx + 70, y1 - 90), fill=(*col, 255))
    elif kind == "social":
        u.rectangle((x0, y0 + 60, x1, y0 + 160), fill=(0, 0, 0, 150))
        u.ellipse((x0 + 30, y0 + 75, x0 + 100, y0 + 145), fill=(*PINK, 255), outline=(*YELLOW, 255), width=5)
        u.text((x0 + 120, y0 + 110), "gtaclipadas", font=font(ANTON, 38), fill=WHITE, anchor="lm")
        for i in range(3):
            cy = y1 - 520 + i * 150
            u.ellipse((x1 - 130, cy - 50, x1 - 30, cy + 50), fill=(0, 0, 0, 120))
        hx, hy = x1 - 80, y1 - 522
        u.ellipse((hx - 30, hy - 22, hx + 2, hy + 8), fill=(*PINK, 255)); u.ellipse((hx - 2, hy - 22, hx + 30, hy + 8), fill=(*PINK, 255))
        u.polygon([(hx - 29, hy - 2), (hx + 29, hy - 2), (hx, hy + 30)], fill=(*PINK, 255))
        u.text((x1 - 80, y1 - 440), "1,2 mi", font=font(ANTON, 28), fill=WHITE, anchor="mm")
    elif kind == "gps":
        u.ellipse((x0 + 30, y1 - 330, x0 + 330, y1 - 30), fill=(20, 40, 30, 230), outline=WHITE, width=6)
        u.line([(x0 + 80, y1 - 120), (x0 + 160, y1 - 200), (x0 + 220, y1 - 170), (x0 + 280, y1 - 260)], fill=(*PINK, 255), width=14)
        u.polygon([(x0 + 180, y1 - 165), (x0 + 165, y1 - 205), (x0 + 195, y1 - 205)], fill=WHITE)
        u.rectangle((x0, y0 + 60, x1, y0 + 150), fill=(0, 0, 0, 170))
        u.text((W // 2, y0 + 105), "300 m  •  Vice Beach", font=font(ANTON, 36), fill=WHITE, anchor="mm")
    elif kind == "foto":
        for i in (1, 2):
            u.line([(x0 + (x1 - x0) * i // 3, y0 + 60), (x0 + (x1 - x0) * i // 3, y1)], fill=(255, 255, 255, 110), width=3)
            u.line([(x0, y0 + 60 + (y1 - y0 - 60) * i // 3), (x1, y0 + 60 + (y1 - y0 - 60) * i // 3)], fill=(255, 255, 255, 110), width=3)
        u.ellipse((W // 2 - 70, y1 - 200, W // 2 + 70, y1 - 60), fill=WHITE, outline=(*PINK, 255), width=10)
        u.ellipse((x0 + 40, y0 + 92, x0 + 76, y0 + 128), fill=(*RED, 255)); u.text((x0 + 90, y0 + 110), "REC", font=font(ANTON, 36), fill=(*RED, 255), anchor="lm")
    elif kind == "banco":
        u.rounded_rectangle((x0 + 24, y0 + 90, x1 - 24, y0 + 300), radius=34, fill=(255, 255, 255, 245))
        u.rounded_rectangle((x0 + 50, y0 + 120, x0 + 140, y0 + 210), radius=20, fill=(*GREEN, 255))
        u.text((x0 + 95, y0 + 165), "$", font=font(BOWLBY, 54), fill=WHITE, anchor="mm")
        u.text((x0 + 170, y0 + 145), "PIX RECEBIDO", font=font(ANTON, 34), fill=(20, 20, 30), anchor="lm")
        u.text((x0 + 170, y0 + 200), "R$ 50.000,00", font=font(BOWLBY, 46), fill=(20, 150, 70), anchor="lm")
        u.text((x0 + 50, y0 + 262), "Banco Leonida • agora", font=font(ANTON, 26), fill=(110, 110, 130), anchor="lm")
    img.alpha_composite(ui)
    return img


# ---------------- Rádio / VHS ----------------
def scanlines(alpha=40):
    img = blank(); d = ImageDraw.Draw(img)
    for y in range(0, H, 6):
        d.rectangle([0, y, W, y + 2], fill=(0, 0, 0, alpha))
    return img


def vhs():
    img = scanlines(45); d = ImageDraw.Draw(img)
    f = font(MONO, 56)
    d.polygon([(70, 128), (70, 178), (112, 153)], fill=WHITE, outline=(0, 0, 0))
    d.text((130, 120), "PLAY", font=f, fill=WHITE, stroke_width=3, stroke_fill=(0, 0, 0))
    d.text((W - 130, 120), "REC", font=f, fill=(*RED, 255), stroke_width=3, stroke_fill=(0, 0, 0), anchor="ra")
    d.ellipse((W - 110, 128, W - 66, 172), fill=(*RED, 255), outline=(0, 0, 0), width=3)
    d.text((70, H - 200), "SP  19.NOV.2026", font=font(MONO, 48), fill=WHITE, stroke_width=3, stroke_fill=(0, 0, 0))
    d.text((70, H - 140), "AM 03:19:26", font=font(MONO, 48), fill=WHITE, stroke_width=3, stroke_fill=(0, 0, 0))
    return img


def radio():
    img = blank(); d = ImageDraw.Draw(img)
    p = Image.new("RGBA", (W, H), (0, 0, 0, 0)); pd = ImageDraw.Draw(p)
    pd.rounded_rectangle((30, 520, W - 30, 700), radius=26, fill=(14, 8, 26, 240))
    img.alpha_composite(p)
    glow_rect(img, (30, 520, W - 30, 700), ORANGE, width=5, radius=26, blur=10)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((60, 545, 330, 675), radius=14, fill=(40, 20, 10, 255), outline=(*ORANGE, 255), width=3)
    d.text((195, 610), "103.7", font=font(MONO, 64), fill=(*ORANGE, 255), anchor="mm")
    for i in range(18):
        x = 370 + i * 34
        d.rectangle([x, 640, x + 4, 675 - (i % 3) * 8], fill=(255, 200, 150, 200))
    d.polygon([(560, 545), (580, 545), (570, 640)], fill=(*RED, 255))
    return img


def fita():
    img = blank(); d = ImageDraw.Draw(img)
    p = Image.new("RGBA", (W, H), (0, 0, 0, 0)); pd = ImageDraw.Draw(p)
    pd.rounded_rectangle((70, 150, W - 70, 600), radius=30, fill=(25, 20, 30, 245), outline=(80, 70, 90, 255), width=6)
    img.alpha_composite(p); d = ImageDraw.Draw(img)
    d.rounded_rectangle((110, 190, W - 110, 400), radius=14, fill=(250, 240, 225, 255))
    for i, c in enumerate((PINK, ORANGE, YELLOW)):
        d.rectangle((110, 190 + i * 22, W - 110, 210 + i * 22), fill=(*c, 255))
    d.rounded_rectangle((300, 430, W - 300, 560), radius=60, fill=(10, 8, 14, 255))
    for cx in (390, W - 390):
        d.ellipse((cx - 45, 450, cx + 45, 540), fill=(240, 240, 240, 255))
        d.ellipse((cx - 18, 477, cx + 18, 513), fill=(10, 8, 14, 255))
    return img


def tv():
    img = scene("praia"); d = ImageDraw.Draw(img)
    body = (50, 560, W - 50, 1300)
    d.rounded_rectangle(body, radius=60, fill=(70, 40, 30, 255), outline=(20, 10, 10, 255), width=8)
    d.rounded_rectangle((90, 620, 990, 1146), radius=50, fill=(10, 10, 10, 255))
    for cx in (300, 540, 780):
        d.ellipse((cx - 34, 1180, cx + 34, 1248), fill=(200, 180, 150, 255), outline=(30, 20, 10, 255), width=5)
    d.line([(400, 560), (300, 380)], fill=(30, 30, 30, 255), width=10)
    d.line([(680, 560), (800, 380)], fill=(30, 30, 30, 255), width=10)
    d.rectangle((300, 1300, 380, 1380), fill=(40, 20, 15, 255)); d.rectangle((700, 1300, 780, 1380), fill=(40, 20, 15, 255))
    hole = Image.new("L", (W, H), 255); ImageDraw.Draw(hole).rounded_rectangle(TV_SCREEN, radius=40, fill=0)
    a = Image.composite(img.getchannel("A"), Image.new("L", (W, H), 0), hole); img.putalpha(a)
    img.alpha_composite(scanlines(35))
    return img


TV_SCREEN = (110, 640, 970, 1126)
BB_SCREEN = (80, 560, 1000, 1078)


def outdoor():
    img = scene("marina"); d = ImageDraw.Draw(img)
    for x in (300, 780):
        d.rectangle((x - 18, 1080, x + 18, H), fill=(20, 8, 30, 255))
    d.rectangle((50, 530, 1030, 1108), fill=(30, 14, 40, 255), outline=(*CYAN, 255), width=8)
    for x in range(120, 1000, 130):
        d.polygon([(x, 530), (x - 20, 500), (x + 20, 500)], fill=(255, 230, 150, 255))
    hole = Image.new("L", (W, H), 255); ImageDraw.Draw(hole).rectangle(BB_SCREEN, fill=0)
    a = Image.composite(img.getchannel("A"), Image.new("L", (W, H), 0), hole); img.putalpha(a)
    return img


def revista():
    img = blank(); d = ImageDraw.Draw(img)
    g = Image.new("RGBA", (W, H), (0, 0, 0, 0)); gp = ImageDraw.Draw(g)
    gp.rectangle((0, 0, W, 420), fill=(0, 0, 0, 90))
    img.alpha_composite(g.filter(ImageFilter.GaussianBlur(30)))
    d.text((W // 2, 190), "CLIPADAS", font=font(BOWLBY, 170), fill=(*PINK, 255), stroke_width=8, stroke_fill=(0, 0, 0), anchor="mm")
    d.text((W // 2, 320), "EDIÇÃO ESPECIAL  •  GTA 6  •  Nº 1", font=font(ANTON, 40), fill=WHITE, stroke_width=3, stroke_fill=(0, 0, 0), anchor="mm")
    d.rectangle((60, 1650, 330, 1720), fill=(*YELLOW, 255))
    d.text((195, 1685), "EXCLUSIVO", font=font(ANTON, 44), fill=(0, 0, 0), anchor="mm")
    d.rectangle((W - 260, H - 160, W - 60, H - 60), fill=WHITE)
    for i in range(18):
        d.rectangle((W - 245 + i * 10, H - 150, W - 241 + i * 10 + (i % 3) * 2, H - 90), fill=(0, 0, 0))
    return img


def build_all(force=False) -> dict:
    """Gera todas as artes uma vez e devolve {nome: caminho}."""
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = {
        "vb_top": lambda: vb_band_at(523, "praia", 1150), "vb_bottom": lambda: vb_band_at(1227, "noite", 600), "vb_float": vb_float,
        "vb_blur": lambda: vb_band_at(360, "marina", 640), "vb_split": lambda: vb_band_at(875, "praia", 1200), "vb_moldura": vb_moldura,
        "mission": mission_panel, "mission_red": lambda: mission_panel(RED), "mission_green": lambda: mission_panel(GREEN),
        "siren_r": lambda: siren(RED, "l"), "siren_b": lambda: siren(BLUE, "r"),
        "vhs": vhs, "radio": radio, "fita": fita, "tv": tv, "outdoor": outdoor, "revista": revista,
    }
    for k, c in NEONS.items():
        jobs[f"neon_{k}"] = (lambda c=c: neon_night(c))
    for n in range(1, 6):
        jobs[f"wanted_{n}"] = (lambda n=n: wanted(n))
    jobs["wanted_busted"] = lambda: wanted(5, busted=True)
    for k in ("chat", "chamada", "social", "gps", "foto", "banco"):
        jobs[f"phone_{k}"] = (lambda k=k: phone_ui(k))
    paths = {}
    for name, fn in jobs.items():
        p = OUT / f"{name}.png"
        if force or not p.exists():
            fn().save(p, optimize=True)
        paths[name] = p
    return paths


if __name__ == "__main__":
    print(len(build_all(force=True)), "artes geradas")
