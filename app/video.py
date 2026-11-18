"""Pipeline de corte: baixa o trecho, transcreve, monta 9:16 com template GTA e legenda."""
import os, json, uuid, time, shutil, subprocess, threading
from pathlib import Path
import httpx

from . import assets, art, templates, captions

DATA = Path(os.getenv("DATA_DIR", Path(__file__).parent.parent / "data"))
JOBS_DIR = DATA / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
FONTS = Path(__file__).parent / "fonts"
MAX_LEN = int(os.getenv("MAX_CLIP_SECONDS", "180"))
GROQ_KEY = os.getenv("GROQ_API_KEY", "")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")

W, H = 1080, 1920
JOBS: dict[str, dict] = {}
ON_FAIL = None  # chamado com o id do corte quando ele falha (devolve o crédito)
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "30"))

# Fila com prioridade: planos com prioridade (Pro) passam na frente no horário de pico.
import queue, itertools
_q: "queue.PriorityQueue" = queue.PriorityQueue()
_seq = itertools.count()


def _worker():
    while True:
        _, _, jid = _q.get()
        try:
            _work(jid)
        finally:
            _q.task_done()


for _ in range(int(os.getenv("WORKERS", "2"))):
    threading.Thread(target=_worker, daemon=True).start()


def queue_size() -> int:
    return _q.qsize()


def _cleanup_loop():
    """Apaga cortes com mais de RETENTION_DAYS dias e uploads esquecidos."""
    while True:
        try:
            limit = time.time() - RETENTION_DAYS * 86400
            for d in JOBS_DIR.iterdir():
                if d.is_dir() and d.stat().st_mtime < limit:
                    shutil.rmtree(d, ignore_errors=True)
                    JOBS.pop(d.name, None)
        except Exception:
            pass
        time.sleep(6 * 3600)


threading.Thread(target=_cleanup_loop, daemon=True).start()
_lock = threading.Lock()

# ---------------- Layouts ----------------
LAYOUT_LABELS = {"cam_top": "Facecam em cima", "cam_bottom": "Facecam embaixo", "float": "Facecam flutuante",
                 "blur": "Sem facecam", "split": "Dividido", "crop": "Tela cheia", "moldura": "Moldura", "inset": "Moldura de objeto"}
CAM_CORNERS = ["top-left", "top-right", "bottom-left", "bottom-right"]


def _cam(corner, cs):
    x = "0" if "left" in corner else f"iw-iw*{cs}"
    y = "0" if "top" in corner else f"ih-ih*{cs}"
    return f"crop=iw*{cs}:ih*{cs}:{x}:{y}"


def _cover(w, h):
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"


def layout_filter(lay: dict, corner: str, cs: float) -> str:
    k = lay["kind"]
    if k in ("cam_top", "cam_bottom", "split"):
        ch = 960 if k == "split" else 608
        gh = H - ch
        f = f"[0:v]split=2[a][b];[a]{_cam(corner, cs)},{_cover(W, ch)}[cam];[b]{_cover(W, gh)}[game];"
        return f + ("[game][cam]" if k == "cam_bottom" else "[cam][game]") + "vstack=inputs=2[base]"
    if k == "float":
        return (f"[0:v]split=2[a][b];[b]{_cover(W, H)}[game];[a]{_cam(corner, cs)},{_cover(424, 234)}[cam];"
                f"[game][cam]overlay=48:198[base]")
    if k == "blur":
        return (f"[0:v]split=2[a][b];[a]{_cover(W, H)},boxblur=25:3,eq=brightness=-0.05,colorchannelmixer=rr=0.85:gg=0.45:bb=0.95[bg];"
                f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[base]")
    if k == "crop":
        return f"[0:v]{_cover(W, H)}[base]"
    if k == "moldura":
        return f"[0:v]{_cover(W, 1590)},pad={W}:{H}:0:330:color=0x1a0630[base]"
    if k == "inset":
        x0, y0, x1, y1 = lay["rect"]
        return (f"[0:v]split=2[a][b];[a]{_cover(W, H)},boxblur=30:3[bg];[b]{_cover(x1 - x0, y1 - y0)}[fg];"
                f"[bg][fg]overlay={x0}:{y0}[base]")
    raise ValueError(k)


def build_filter(tpl: dict, opts: dict, dur: float, with_text: bool = True) -> tuple[str, list[Path]]:
    """Monta o filtro completo. Devolve (filtro, lista de imagens extras na ordem dos inputs 1..n)."""
    paths = art.build_all()
    inputs: list[Path] = []

    def inp(p: Path) -> str:
        inputs.append(p)
        return f"[{len(inputs)}:v]"

    vf = layout_filter(tpl["layout"], opts.get("corner", "top-left"), float(opts.get("cam_size", 0.30)))
    cur, n = "[base]", 0

    def nxt():
        nonlocal n
        n += 1
        return f"[v{n}]"

    if "vhs" in tpl["fx"]:
        o = nxt(); vf += f";{cur}noise=alls=14:allf=t,eq=saturation=1.25:contrast=1.08,rgbashift=rh=4:bh=-4{o}"; cur = o
    for a, b, period in tpl["flash"]:
        for name, cond in ((a, f"lt(mod(t,{period}),{period / 2})"), (b, f"gte(mod(t,{period}),{period / 2})")):
            o = nxt(); vf += f";{inp(paths[name])}format=rgba[f{n}];{cur}[f{n}]overlay=0:0:enable='{cond}'{o}"; cur = o
    for name in tpl["overlays"]:
        o = nxt(); vf += f";{inp(paths[name])}format=rgba[o{n}];{cur}[o{n}]overlay=0:0{o}"; cur = o
    if "crt" in tpl["fx"]:
        o = nxt(); vf += f";{cur}noise=alls=6:allf=t{o}"; cur = o
    if opts.get("watermark", True):
        o = nxt(); vf += f";{inp(assets.OUT / 'logo_round.png')}scale=150:150[lg];{cur}[lg]overlay={W - 180}:{H - 230}{o}"; cur = o
    if "gray_end" in tpl["fx"]:
        o = nxt(); vf += f";{cur}hue=s=0:enable='gte(t,{max(0, dur - 1.8):.2f})'{o}"; cur = o
    if with_text:
        vf += f";{cur}ass=subs.ass:fontsdir={FONTS}[v]"
    else:
        vf += f";{cur}null[v]"
    return vf, inputs


def _run(cmd, cwd=None, timeout=1800):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout)[-1500:])
    return p.stdout


def probe_duration(path: Path) -> float:
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)])
    return float(json.loads(out)["format"]["duration"])


_PROBE: dict = {}


def probe_url(url: str) -> dict:
    """Duração, título e thumb de um link (YouTube, Twitch, Kick) sem baixar o vídeo."""
    hit = _PROBE.get(url)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    out = _run(["yt-dlp", "-J", "--no-playlist", "--skip-download", "--no-warnings", url], timeout=40)
    j = json.loads(out)
    info = {"duration": float(j.get("duration") or 0), "title": (j.get("title") or "")[:140],
            "thumb": j.get("thumbnail") or "", "live": bool(j.get("is_live")),
            "extractor": (j.get("extractor_key") or "").lower(), "id": j.get("id") or ""}
    if len(_PROBE) > 500:
        _PROBE.clear()
    _PROBE[url] = (time.time(), info)
    return info


def download(url: str, start: float | None, end: float | None, dest: Path) -> Path:
    cmd = ["yt-dlp", "--no-playlist", "-f", "best[height<=1080]/bestvideo[height<=1080]+bestaudio/best",
           "--merge-output-format", "mp4", "-o", str(dest / "src.%(ext)s")]
    if start is not None and end is not None:
        cmd += ["--download-sections", f"*{start:.2f}-{end:.2f}"]
    cmd.append(url)
    _run(cmd, timeout=900)
    files = sorted(dest.glob("src.*"))
    if not files:
        raise RuntimeError("Download não gerou arquivo")
    return files[0]


def transcribe(src: Path, job_dir: Path) -> list[dict]:
    key, url, model = (GROQ_KEY, "https://api.groq.com/openai/v1/audio/transcriptions", "whisper-large-v3-turbo") if GROQ_KEY \
        else (OPENAI_KEY, "https://api.openai.com/v1/audio/transcriptions", "whisper-1")
    if not key:
        return []
    audio = job_dir / "audio.mp3"
    _run(["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(audio)])
    with open(audio, "rb") as f:
        r = httpx.post(url, headers={"Authorization": f"Bearer {key}"}, timeout=300,
                       files={"file": ("audio.mp3", f, "audio/mpeg")},
                       data={"model": model, "language": "pt", "response_format": "verbose_json",
                             "timestamp_granularities[]": "word"})
    r.raise_for_status()
    return [{"word": w["word"], "start": float(w["start"]), "end": float(w["end"])} for w in r.json().get("words", [])]


def render(src: Path, job_dir: Path, opts: dict, trim: tuple | None, words: list[dict]) -> Path:
    tpl = templates.BY_ID.get(opts.get("template"), templates.TEMPLATES[0])
    cap = templates.CAPTION_BY_ID.get(opts.get("caption"), templates.CAPTION_STYLES[0])
    dur = (trim[1] - trim[0]) if trim else probe_duration(src)
    (job_dir / "subs.ass").write_text(captions.build(words, dur, tpl, cap, opts.get("caption_color", "auto"), opts.get("title", "")), encoding="utf-8")
    vf, extra = build_filter(tpl, opts, dur)
    pre = ["-ss", f"{trim[0]:.2f}", "-t", f"{dur:.2f}"] if trim else []
    ins = pre + ["-i", str(src)]
    for p in extra:
        ins += ["-i", str(p)]
    out = job_dir / "corte.mp4"
    _run(["ffmpeg", "-y", *ins, "-filter_complex", vf, "-map", "[v]", "-map", "0:a?", "-r", "30",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest", out.name], cwd=job_dir)
    _run(["ffmpeg", "-y", "-ss", f"{min(1.0, dur / 2):.2f}", "-i", out.name, "-frames:v", "1", "-vf", "scale=360:-2", "thumb.jpg"], cwd=job_dir)
    if opts.get("cover", True):
        try:
            make_cover(src, job_dir, tpl, opts, trim, dur)
        except Exception as e:  # capa é extra: não derruba o corte
            (job_dir / "cover_error.txt").write_text(str(e))
    return out


def make_cover(src: Path, job_dir: Path, tpl: dict, opts: dict, trim, dur):
    """Capa 9:16: frame do momento no template + título grande + selo."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    t = (trim[0] if trim else 0) + dur * 0.45
    vf, extra = build_filter(tpl, {**opts, "watermark": False}, dur, with_text=False)
    ins = ["-ss", f"{t:.2f}", "-i", str(src)]
    for p in extra:
        ins += ["-i", str(p)]
    _run(["ffmpeg", "-y", *ins, "-filter_complex", vf, "-map", "[v]", "-frames:v", "1", "cover_raw.png"], cwd=job_dir)
    im = Image.open(job_dir / "cover_raw.png").convert("RGBA")
    shade = Image.new("RGBA", im.size, (0, 0, 0, 0)); sd = ImageDraw.Draw(shade)
    for y in range(900):
        sd.line([(0, H - 900 + y), (W, H - 900 + y)], fill=(16, 4, 31, int(235 * (y / 899) ** 1.3)))
    im.alpha_composite(shade)
    d = ImageDraw.Draw(im)
    title = (opts.get("title") or "OLHA ISSO").upper()
    f = ImageFont.truetype(str(FONTS / "BowlbyOne_400Regular.ttf"), 92)
    lines, cur = [], ""
    for w in title.split():
        test = (cur + " " + w).strip()
        if d.textlength(test, font=f) > W - 120 and cur:
            lines.append(cur); cur = w
        else:
            cur = test
    lines.append(cur)
    lines = lines[:4]
    y = H - 140 - len(lines) * 112
    for i, ln in enumerate(lines):
        col = (255, 31, 168) if i % 2 == 0 else (255, 255, 255)
        d.text((W // 2 + 6, y + i * 112 + 8), ln, font=f, fill=(60, 225, 255), anchor="mm")
        d.text((W // 2, y + i * 112), ln, font=f, fill=col, stroke_width=8, stroke_fill=(0, 0, 0), anchor="mm")
    badge = ImageFont.truetype(str(FONTS / "Anton_400Regular.ttf"), 54)
    d.rounded_rectangle((50, 60, 330, 150), radius=18, fill=(255, 31, 168), outline=(0, 0, 0), width=5)
    d.text((190, 105), "GTA 6", font=badge, fill=(255, 255, 255), anchor="mm")
    logo = Image.open(assets.OUT / "logo_round.png").convert("RGBA").resize((170, 170))
    im.alpha_composite(logo, (W - 210, 40))
    im.convert("RGB").save(job_dir / "capa.jpg", quality=90)


def _set(jid, **kw):
    with _lock:
        JOBS[jid].update(kw)


def _work(jid: str):
    j = JOBS[jid]
    d = JOBS_DIR / jid
    try:
        o = j["opts"]
        start, end = o.get("start"), o.get("end")
        if j.get("upload"):
            src = Path(j["upload"])
            trim = (start, end) if start is not None and end is not None else None
        else:
            _set(jid, status="baixando", progress=15)
            src = download(o["url"], start, end, d)
            trim = None
        if trim is None and probe_duration(src) > MAX_LEN + 1:
            trim = (0.0, float(MAX_LEN))
        words = []
        if o.get("captions", True):
            _set(jid, status="legendando", progress=45)
            try:
                words = transcribe(src, d)
                if trim:
                    words = [{**w, "start": w["start"] - trim[0], "end": w["end"] - trim[0]} for w in words
                             if trim[0] <= w["start"] < trim[1]]
            except Exception as e:  # legenda é opcional: não derruba o corte
                _set(jid, warning=f"Legenda falhou: {e}")
        _set(jid, status="renderizando", progress=70)
        render(src, d, o, trim, words)
        for f in list(d.glob("upload.*")) + list(d.glob("src.*")) + list(d.glob("audio.mp3")) + list(d.glob("cover_raw.png")):
            f.unlink(missing_ok=True)
        cover = f"/files/{jid}/capa.jpg" if (d / "capa.jpg").exists() else None
        _set(jid, status="pronto", progress=100, video=f"/files/{jid}/corte.mp4", thumb=f"/files/{jid}/thumb.jpg", cover=cover,
             finished=time.time(), captions=len(words) > 0)
    except Exception as e:
        _set(jid, status="erro", error=str(e)[-800:])
        if ON_FAIL:
            try:
                ON_FAIL(jid)
            except Exception:
                pass


def create_job(opts: dict, upload: Path | None = None, priority: int = 0, keep_upload: bool = False) -> dict:
    s, e = opts.get("start"), opts.get("end")
    if s is not None and e is not None:
        if e <= s:
            raise ValueError("O fim precisa ser depois do início")
        if e - s > MAX_LEN:
            raise ValueError(f"Corte máximo de {MAX_LEN} segundos")
    jid = uuid.uuid4().hex[:12]
    d = JOBS_DIR / jid
    d.mkdir(parents=True)
    job = {"id": jid, "status": "na fila", "progress": 5, "opts": opts, "created": time.time()}
    if upload:
        dst = d / ("upload" + upload.suffix)
        if keep_upload:  # corte em lote: vários cortes do mesmo arquivo
            try:
                os.link(upload, dst)
            except OSError:
                shutil.copy(upload, dst)
        else:
            shutil.move(str(upload), dst)
        job["upload"] = str(dst)
    JOBS[jid] = job
    _q.put((-priority, next(_seq), jid))
    return public(job)


def public(j: dict) -> dict:
    return {k: v for k, v in j.items() if k not in ("upload",)}
