"""Gera as prévias (imagens) dos 36 templates e 36 legendas a partir de uma cena de exemplo desenhada."""
import shutil, subprocess, tempfile, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

from . import art, templates, captions, video
from .assets import palm, car, heli

OUT = Path(__file__).parent / "previews"
SAMPLE = Path(__file__).parent / "assets" / "sample.mp4"
WORDS = [{"word": w, "start": 0.05 + i * 0.32, "end": 0.05 + i * 0.32 + 0.3}
         for i, w in enumerate("MANO ELE PULOU DA PONTE DIRETO NO CARRO DA POLÍCIA".split())]


def scene() -> Image.Image:
    """Cena 1920x1080 tirada da ilustração (perseguição à noite) + facecam no canto."""
    w, h = 1920, 1080
    src = Image.open(Path(__file__).parent / "scenes" / "noite.jpg").convert("RGBA")
    img = src.crop((0, 820, 1152, 820 + 648)).resize((w, h), Image.LANCZOS)
    cw, ch = int(w * 0.3), int(h * 0.3)
    cam = Image.new("RGBA", (cw, ch), (16, 8, 30, 255)); cd = ImageDraw.Draw(cam)
    glow = Image.new("RGBA", (cw, ch), (0, 0, 0, 0)); gd = ImageDraw.Draw(glow)
    gd.ellipse([cw * 0.45, -60, cw * 1.2, ch * 0.9], fill=(255, 31, 168, 170)); gd.ellipse([-80, ch * 0.3, cw * 0.4, ch * 1.3], fill=(60, 225, 255, 130))
    cam.alpha_composite(glow.filter(ImageFilter.GaussianBlur(40)))
    cd = ImageDraw.Draw(cam)
    cd.ellipse([cw / 2 - 70, 55, cw / 2 + 70, 205], fill=(12, 6, 20, 255))
    cd.rounded_rectangle([cw / 2 - 160, 215, cw / 2 + 160, ch + 60], radius=70, fill=(12, 6, 20, 255))
    cd.arc([cw / 2 - 92, 35, cw / 2 + 92, 215], 180, 360, fill=(255, 31, 168, 255), width=14)
    cd.rounded_rectangle([cw / 2 - 108, 115, cw / 2 - 76, 175], radius=10, fill=(255, 31, 168, 255))
    cd.rounded_rectangle([cw / 2 + 76, 115, cw / 2 + 108, 175], radius=10, fill=(255, 31, 168, 255))
    img.alpha_composite(cam, (0, 0))
    return img


def make_sample():
    SAMPLE.parent.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    scene().convert("RGB").save(tmp / "scene.png")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(tmp / "scene.png"),
                    "-f", "lavfi", "-i", "sine=f=220:d=4", "-t", "4", "-r", "30",
                    "-vf", "scale=2112:1188,crop=1920:1080:'96+60*sin(t*1.5)':'54+20*cos(t*1.5)'",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(SAMPLE)], check=True)
    shutil.rmtree(tmp)


def frame(job: Path, t: float, out: Path):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(job / "corte.mp4"),
                    "-frames:v", "1", "-vf", "scale=360:640", "-q:v", "4", str(out)], check=True)


def build(only=None):
    OUT.mkdir(exist_ok=True)
    if not SAMPLE.exists():
        make_sample()
    art.build_all()
    work = Path(tempfile.mkdtemp())
    for t in templates.TEMPLATES:
        if only and t["id"] not in only:
            continue
        d = work / t["id"]; d.mkdir()
        start_card = any(c["at"] == "start" for c in t["cards"])
        end_card = any(c["at"] == "end" for c in t["cards"])
        video.render(SAMPLE, d, {"template": t["id"], "caption": "pesada-karaoke", "title": "ele não acreditou no que viu",
                                 "watermark": True, "cover": False}, (0.0, 3.0), WORDS)
        frame(d, 0.8 if start_card else (2.6 if end_card else 1.0), OUT / f"tpl-{t['id']}.jpg")
    cap_tpl = templates.BY_ID["vice-sem-cam"]
    for c in templates.CAPTION_STYLES:
        if only and c["id"] not in only:
            continue
        d = work / ("cap-" + c["id"]); d.mkdir()
        video.render(SAMPLE, d, {"template": "retro-revista" if False else "vice-sem-cam", "caption": c["id"], "title": "",
                                 "watermark": False, "cover": False}, (0.0, 1.6), WORDS)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.45", "-i", str(d / "corte.mp4"), "-frames:v", "1",
                        "-vf", "crop=1080:420:0:1240,scale=540:210", "-q:v", "4", str(OUT / f"cap-{c['id']}.jpg")], check=True)
    shutil.rmtree(work)


if __name__ == "__main__":
    build(set(sys.argv[1:]) or None)
