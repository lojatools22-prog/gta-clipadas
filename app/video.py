"""Pipeline de corte: baixa o trecho, transcreve, monta 9:16 com template GTA e legenda."""
import os, json, uuid, time, shutil, subprocess, threading, logging, traceback
from pathlib import Path
import httpx

from . import assets, art, templates, captions

DATA = Path(os.getenv("DATA_DIR", Path(__file__).parent.parent / "data"))
JOBS_DIR = DATA / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)


def _free(p: Path) -> int:
    try:
        p.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(p).free
    except Exception:
        return 0


def _pick_scratch() -> Path:
    """Disco de trabalho para vídeos em processamento (grandes e temporários).
    O volume fica só com os cortes prontos; o trabalho vai para onde houver mais espaço livre."""
    if os.getenv("SCRATCH_DIR"):
        return Path(os.environ["SCRATCH_DIR"])
    opts = [DATA / "trabalho", Path("/tmp/clipadas")]
    return max(opts, key=_free)


SCRATCH = _pick_scratch()
SCRATCH.mkdir(parents=True, exist_ok=True)


def scratch(jid: str) -> Path:
    p = SCRATCH / "cortes" / jid
    p.mkdir(parents=True, exist_ok=True)
    return p
FONTS = Path(__file__).parent / "fonts"
MAX_LEN = int(os.getenv("MAX_CLIP_SECONDS", "180"))
GROQ_KEY = os.getenv("GROQ_API_KEY", "")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")

W, H = 1080, 1920
log = logging.getLogger("uvicorn.error")
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


def _auto_workers() -> int:
    """Quantos cortes montar ao mesmo tempo: ~1 por 2 GB de memória do container (máx. 4)."""
    v = os.getenv("WORKERS", "auto").strip().lower()
    if v.isdigit() and v != "0":
        return int(v)
    mem = 0
    for f in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            t = open(f).read().strip()
            if t.isdigit() and int(t) < 1 << 50:
                mem = int(t); break
        except OSError:
            pass
    if not mem:
        try:
            mem = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (ValueError, OSError):
            mem = 2 << 30
    return max(1, min(int(os.getenv("WORKERS_MAX", "4")), mem // (2 << 30)))


WORKERS = _auto_workers()
for _ in range(WORKERS):
    threading.Thread(target=_worker, daemon=True).start()


def queue_position(jid: str) -> int | None:
    """Posição do corte na fila (1 = o próximo a ser montado)."""
    with _q.mutex:
        items = sorted(_q.queue)
    for i, it in enumerate(items):
        if it[2] == jid:
            return i + 1
    return None


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
LAYOUT_LABELS = {"blur_plain": "Fundo desfocado", "letterbox": "Faixas pretas", "cam_top": "Facecam em cima", "cam_bottom": "Facecam embaixo", "float": "Facecam flutuante",
                 "blur": "Sem facecam", "split": "Dividido", "crop": "Tela cheia", "moldura": "Moldura", "inset": "Moldura de objeto"}
CAM_CORNERS = ["top-left", "top-right", "bottom-left", "bottom-right"]


def _cam(corner, cs):
    x = "0" if "left" in corner else f"iw-iw*{cs}"
    y = "0" if "top" in corner else f"ih-ih*{cs}"
    return f"crop=iw*{cs}:ih*{cs}:{x}:{y}"


def _cover(w, h):
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"


def layout_filter(lay: dict, corner: str, cs: float, cam_ref: str | None = None) -> str:
    k = lay["kind"]
    if cam_ref and k in ("cam_top", "cam_bottom", "split", "float"):
        # webcam enviada em arquivo separado: usa o vídeo inteiro dela como facecam
        if k == "float":
            return f"[0:v]{_cover(W, H)}[game];{cam_ref}{_cover(424, 234)}[cam];[game][cam]overlay=48:198[base]"
        ch = 960 if k == "split" else 608
        f = f"{cam_ref}{_cover(W, ch)}[cam];[0:v]{_cover(W, H - ch)}[game];"
        return f + ("[game][cam]" if k == "cam_bottom" else "[cam][game]") + "vstack=inputs=2[base]"
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
    if k == "blur_plain":  # vídeo inteiro no meio, o próprio vídeo desfocado atrás, sem tingir
        return (f"[0:v]split=2[a][b];[a]{_cover(W, H)},boxblur=30:4,eq=brightness=-0.12[bg];"
                f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[base]")
    if k == "letterbox":  # vídeo inteiro no meio com faixas pretas
        return f"[0:v]scale={W}:-2,pad={W}:{H}:0:(oh-ih)/2:black[base]"
    if k == "moldura":
        return f"[0:v]{_cover(W, 1590)},pad={W}:{H}:0:330:color=0x1a0630[base]"
    if k == "inset":
        x0, y0, x1, y1 = lay["rect"]
        return (f"[0:v]split=2[a][b];[a]{_cover(W, H)},boxblur=30:3[bg];[b]{_cover(x1 - x0, y1 - y0)}[fg];"
                f"[bg][fg]overlay={x0}:{y0}[base]")
    raise ValueError(k)


def build_filter(tpl: dict, opts: dict, dur: float, with_text: bool = True, cam: Path | None = None) -> tuple[str, list[Path]]:
    """Monta o filtro completo. Devolve (filtro, lista de imagens extras na ordem dos inputs 1..n)."""
    paths = art.build_all()
    inputs: list[Path] = []

    def inp(p: Path) -> str:
        inputs.append(p)
        return f"[{len(inputs)}:v]"

    # react: webcam de quem está cortando. 'slot' ocupa o lugar da facecam do template; 'float' fica numa janela por cima
    cam_ref = inp(cam) if cam else None
    slot = cam_ref and opts.get("react_pos") == "slot" and tpl["layout"]["kind"] in ("cam_top", "cam_bottom", "split", "float")
    if opts.get("_portrait") and not slot:  # vídeo gravado em pé: ocupa a tela toda, sem recortar facecam
        vf = f"[0:v]{_cover(W, H)}[base]"
    else:
        vf = layout_filter(tpl["layout"], opts.get("corner", "top-left"), float(opts.get("cam_size", 0.30)), cam_ref if slot else None)
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
    if opts.get("_logo") and Path(opts["_logo"]).exists():  # logo do próprio clipador, pequena no canto
        o = nxt(); vf += (f";{inp(Path(opts['_logo']))}format=rgba,scale=w='min(150,iw)':h='min(150,ih)':force_original_aspect_ratio=decrease,"
                          f"colorchannelmixer=aa=0.9[lg];{cur}[lg]overlay=W-w-40:H-h-190{o}"); cur = o
    if "gray_end" in tpl["fx"]:
        o = nxt(); vf += f";{cur}hue=s=0:enable='gte(t,{max(0, dur - 1.8):.2f})'{o}"; cur = o
    if cam_ref and not slot:
        o = nxt()
        vf += (f";{cam_ref}{_cover(380, 380)},pad=392:392:6:6:color=0xff1fa8[rc];"
               f"{cur}[rc]overlay={W - 392 - 36}:{150}:eof_action=repeat{o}"); cur = o
    if with_text:
        vf += f";{cur}ass=subs.ass:fontsdir={FONTS}[v]"
    else:
        vf += f";{cur}null[v]"
    return vf, inputs


FF_THREADS = os.getenv("FFMPEG_THREADS", "2")


def _run(cmd, cwd=None, timeout=1800):
    if cmd and cmd[0] == "ffmpeg":
        # o container enxerga todos os núcleos da máquina física; sem limite o x264 abre dezenas de
        # threads e estoura a memória do plano
        cmd = ["ffmpeg", "-hide_banner", "-threads", FF_THREADS, "-filter_threads", FF_THREADS,
               "-filter_complex_threads", FF_THREADS] + cmd[1:]
        if "libx264" in cmd:
            i = cmd.index("libx264") + 1
            cmd = cmd[:i] + ["-threads", FF_THREADS, "-x264-params", f"threads={FF_THREADS}:lookahead_threads=1"] + cmd[i:]
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "")
        key = [l for l in err.splitlines() if any(w in l.lower() for w in ("error", "invalid", "no such", "killed", "cannot", "failed"))]
        raise RuntimeError(f"código {p.returncode}: " + (" | ".join(key[-6:]) or err[-600:])[-1500:])
    return p.stdout


def is_portrait(path: Path) -> bool:
    try:
        out = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:stream_side_data=rotation",
                    "-of", "json", str(path)])
        st = json.loads(out)["streams"][0]
        w, h = st["width"], st["height"]
        rot = abs(int(float(next((sd.get("rotation", 0) for sd in st.get("side_data_list", []) if "rotation" in sd), 0))))
        if rot in (90, 270):
            w, h = h, w
        return h > w * 1.1
    except Exception:
        return False


def has_audio(path: Path) -> bool:
    try:
        return bool(_run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)]).strip())
    except Exception:
        return False


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


_WM = None
_WM_LOCK = threading.Lock()
LOCAL_MODEL = os.getenv("WHISPER_LOCAL_MODEL", "base")


def _local_words(wav: Path) -> list[dict]:
    """Transcrição no próprio servidor (faster-whisper). Reserva para quando a API não está configurada ou falha."""
    global _WM
    import numpy as np
    from faster_whisper import WhisperModel
    with _WM_LOCK:
        if _WM is None:
            _WM = WhisperModel(LOCAL_MODEL, device="cpu", compute_type="int8", cpu_threads=int(FF_THREADS),
                               download_root=str(SCRATCH / "modelos"))
        audio = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
                                             capture_output=True, check=True).stdout, np.float32)
        segs, _ = _WM.transcribe(audio, language="pt", word_timestamps=True, vad_filter=True, beam_size=1,
                                 initial_prompt="Live de GTA em português do Brasil.")
        return [{"word": w.word, "start": float(w.start), "end": float(w.end)} for sg in segs for w in (sg.words or [])]


def transcribe(src: Path, job_dir: Path, trim: tuple | None = None) -> list[dict]:
    """Palavras com tempo, relativas ao início do corte. Só o trecho do corte é transcrito."""
    audio = job_dir / "audio.mp3"
    pre = ["-ss", f"{trim[0]:.2f}", "-t", f"{trim[1] - trim[0]:.2f}"] if trim else []
    _run(["ffmpeg", "-y", *pre, "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(audio)])
    key, url, model = (GROQ_KEY, "https://api.groq.com/openai/v1/audio/transcriptions", "whisper-large-v3-turbo") if GROQ_KEY \
        else (OPENAI_KEY, "https://api.openai.com/v1/audio/transcriptions", "whisper-1")
    if key:
        try:
            with open(audio, "rb") as f:
                r = httpx.post(url, headers={"Authorization": f"Bearer {key}"}, timeout=300,
                               files={"file": ("audio.mp3", f, "audio/mpeg")},
                               data={"model": model, "language": "pt", "response_format": "verbose_json",
                                     "timestamp_granularities[]": "word"})
            r.raise_for_status()
            return [{"word": w["word"], "start": float(w["start"]), "end": float(w["end"])} for w in r.json().get("words", [])]
        except Exception as e:
            log.warning("Transcrição pela API falhou, usando a local: %s", str(e)[:300])
    return _local_words(audio)


def render(src: Path, job_dir: Path, opts: dict, trim: tuple | None, words: list[dict], cam: Path | None = None) -> Path:
    tpl = templates.BY_ID.get(opts.get("template"), templates.TEMPLATES[0])
    cap = templates.CAPTION_BY_ID.get(opts.get("caption"), templates.CAPTION_STYLES[0])
    dur = (trim[1] - trim[0]) if trim else probe_duration(src)
    opts = {**opts, "_portrait": is_portrait(src)}
    (job_dir / "subs.ass").write_text(captions.build(words, dur, tpl, cap, opts.get("caption_color", "auto"), opts.get("title", "")), encoding="utf-8")
    vf, extra = build_filter(tpl, opts, dur, cam=cam)
    pre = ["-ss", f"{trim[0]:.2f}", "-t", f"{dur:.2f}"] if trim else []
    ins = pre + ["-i", str(src)]
    for p in extra:
        ins += ["-i", str(p)]  # o react começa junto com o corte, sem deslocamento
    amap = ["-map", "0:a?"]
    if cam:
        ci = extra.index(cam) + 1
        ga, ra = has_audio(src), has_audio(cam)
        if ga and ra:  # mistura a voz do react com o áudio do clipe
            vf += f";[0:a]volume=0.75[ga];[{ci}:a]volume=1.4[ra];[ga][ra]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
            amap = ["-map", "[a]"]
        elif ra:
            amap = ["-map", f"{ci}:a"]
    out = job_dir / "corte.mp4"
    _run(["ffmpeg", "-y", *ins, "-filter_complex", vf, "-map", "[v]", *amap, "-r", "30",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-t", f"{dur:.2f}", out.name], cwd=job_dir)
    _run(["ffmpeg", "-y", "-ss", f"{min(1.0, dur / 2):.2f}", "-i", out.name, "-frames:v", "1", "-vf", "scale=360:-2", "thumb.jpg"], cwd=job_dir)
    if opts.get("cover", True):
        try:
            make_cover(src, job_dir, tpl, opts, trim, dur, cam)
        except Exception as e:  # capa é extra: não derruba o corte
            (job_dir / "cover_error.txt").write_text(str(e))
    return out


def make_cover(src: Path, job_dir: Path, tpl: dict, opts: dict, trim, dur, cam: Path | None = None):
    """Capa 9:16: frame do momento no template + título grande + selo."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    t = (trim[0] if trim else 0) + dur * 0.45
    vf, extra = build_filter(tpl, {**opts, "_logo": None}, dur, with_text=False, cam=cam)
    ins = ["-ss", f"{t:.2f}", "-i", str(src)]
    for p in extra:
        ins += ["-ss", f"{max(0.0, dur * 0.45):.2f}", "-i", str(p)] if (cam and p == cam) else ["-i", str(p)]
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
    if opts.get("_logo") and Path(opts["_logo"]).exists():
        lg = Image.open(opts["_logo"]).convert("RGBA"); lg.thumbnail((170, 170))
        im.alpha_composite(lg, (W - lg.width - 40, 40))
    im.convert("RGB").save(job_dir / "capa.jpg", quality=90)


def _save(jid):
    j = JOBS.get(jid)
    if not j:
        return
    try:
        (JOBS_DIR / jid / "job.json").write_text(json.dumps(j))
    except Exception:
        pass


def _set(jid, **kw):
    with _lock:
        JOBS[jid].update(kw)
        _save(jid)


def _drop_sources(jid: str):
    """Apaga o vídeo original e os arquivos de trabalho de um corte (fica só o corte pronto)."""
    d = JOBS_DIR / jid
    for f in list(d.glob("cam.*")) + list(d.glob("upload.*")) + list(d.glob("src.*")) + list(d.glob("audio.mp3")) + list(d.glob("cover_raw.png")):
        f.unlink(missing_ok=True)
    shutil.rmtree(SCRATCH / "cortes" / jid, ignore_errors=True)


def free_space(need: int) -> int:
    """Libera espaço no disco de trabalho quando falta: restos de cortes terminados,
    envios abandonados e arquivos temporários antigos. Devolve o espaço livre."""
    if _free(SCRATCH) >= need:
        return _free(SCRATCH)
    now = time.time()
    for base in (SCRATCH / "cortes", JOBS_DIR):
        for d in list(base.iterdir()) if base.exists() else []:
            j = JOBS.get(d.name)
            if j is None or j.get("status") in ("pronto", "erro"):
                _drop_sources(d.name)
    for f in list(SCRATCH.rglob("*.part")) + [x for x in DATA.glob("tmp*") if x.is_file()] + [x for x in SCRATCH.glob("tmp*") if x.is_file()]:
        try:
            if now - f.stat().st_mtime > 1800:
                f.unlink(missing_ok=True)
        except Exception:
            pass
    return _free(SCRATCH)


def _boot_disk():
    """Ao ligar: tira do volume o que não precisa estar lá e registra o espaço livre."""
    try:
        for j in JOBS.values():
            if j.get("status") in ("pronto", "erro"):
                _drop_sources(j["id"])
        if SCRATCH != DATA / "trabalho":
            shutil.rmtree(DATA / "modelos", ignore_errors=True)
            shutil.rmtree(DATA / "trabalho", ignore_errors=True)
        for x in DATA.glob("tmp*"):
            if x.is_file():
                x.unlink(missing_ok=True)
        log.info("Disco: volume %s MB livres · trabalho (%s) %s MB livres",
                 _free(DATA) // 1048576, SCRATCH, _free(SCRATCH) // 1048576)
    except Exception as e:
        log.error("Limpeza do disco ao ligar falhou: %s", e)


def _work(jid: str):
    j = JOBS[jid]
    d = JOBS_DIR / jid
    try:
        o = j["opts"]
        start, end = o.get("start"), o.get("end")
        if j.get("upload"):
            src = Path(j["upload"])
            trim = (start, end) if start is not None and end is not None else None
        elif j.get("src_path") and Path(j["src_path"]).exists():  # já baixado (voltando da revisão de legenda)
            src, trim = Path(j["src_path"]), None
        else:
            _set(jid, status="baixando", progress=15)
            src = download(o["url"], start, end, scratch(jid))
            _set(jid, src_path=str(src))
            trim = None
        if trim is None and probe_duration(src) > MAX_LEN + 1:
            trim = (0.0, float(MAX_LEN))
        words = []
        if j.get("reviewed"):
            words = j.get("words") or []
        elif o.get("captions", True):
            _set(jid, status="legendando", progress=45)
            try:
                words = transcribe(src, d, trim)
            except Exception as e:  # legenda é opcional: não derruba o corte
                _set(jid, warning=f"Legenda falhou: {e}")
                log.error("Legenda do corte %s falhou: %s", jid, str(e)[-600:])
            if o.get("review") and words:  # pausa para a pessoa revisar a legenda antes de montar
                _set(jid, status="revisar", progress=55, words=words)
                return
        _set(jid, status="renderizando", progress=70)
        render(src, d, o, trim, words, Path(j["cam"]) if j.get("cam") else None)
        _drop_sources(jid)
        cover = f"/files/{jid}/capa.jpg" if (d / "capa.jpg").exists() else None
        _set(jid, status="pronto", progress=100, video=f"/files/{jid}/corte.mp4", thumb=f"/files/{jid}/thumb.jpg", cover=cover,
             finished=time.time(), captions=len(words) > 0)
    except Exception as e:
        _drop_sources(jid)
        _set(jid, status="erro", error=str(e)[-1500:])
        log.error("Corte %s falhou: %s\n%s", jid, str(e)[-1500:], traceback.format_exc()[-1500:])
        if ON_FAIL:
            try:
                ON_FAIL(jid)
            except Exception:
                pass


def create_job(opts: dict, upload: Path | None = None, priority: int = 0, keep_upload: bool = False, cam: Path | None = None) -> dict:
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
        dst = scratch(jid) / ("upload" + upload.suffix)
        if keep_upload:  # corte em lote: vários cortes do mesmo arquivo
            try:
                os.link(upload, dst)
            except OSError:
                shutil.copy(upload, dst)
        else:
            shutil.move(str(upload), dst)
        job["upload"] = str(dst)
    if cam:
        cdst = scratch(jid) / ("cam" + cam.suffix)
        try:
            os.link(cam, cdst)
        except OSError:
            shutil.copy(cam, cdst)
        job["cam"] = str(cdst)
    job["priority"] = priority
    JOBS[jid] = job
    _save(jid)
    _q.put((-priority, next(_seq), jid))
    log.info("Corte %s na fila (%s)", jid, "upload" if upload else opts.get("url"))
    return public(job)


def public(j: dict) -> dict:
    return {k: v for k, v in j.items() if k not in ("upload",)}


def _restore():
    """Depois de um reinício do servidor, retoma os cortes que estavam na fila ou em andamento."""
    n = 0
    for d in JOBS_DIR.iterdir():
        f = d / "job.json"
        if not f.exists():
            continue
        try:
            j = json.loads(f.read_text())
        except Exception:
            continue
        if j.get("status") in ("pronto", "erro"):
            JOBS[j["id"]] = j
            continue
        if j.get("upload") and not Path(j["upload"]).exists():
            j.update(status="erro", error="Arquivo enviado não encontrado após reinício")
            JOBS[j["id"]] = j
            continue
        j.update(status="na fila", progress=5)
        JOBS[j["id"]] = j
        _q.put((-int(j.get("priority", 0)), next(_seq), j["id"]))
        n += 1
    if n:
        log.info("Retomando %s corte(s) depois do reinício", n)


def _selftest():
    """Faz um corte de 3s com o vídeo de exemplo ao ligar e registra o resultado no log."""
    time.sleep(3)
    d = Path("/tmp/autoteste")
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    try:
        v = _run(["ffmpeg", "-version"]).splitlines()[0]
        render(assets.OUT / "sample.mp4", d, {"template": "vice-cam-cima", "caption": "pesada-karaoke", "title": "AUTOTESTE"}, (0.0, 3.0),
               [{"word": "teste", "start": 0.2, "end": 0.8}])
        log.info("Autoteste de corte OK (%s, %s bytes, %s corte(s) ao mesmo tempo)", v, (d / "corte.mp4").stat().st_size, WORKERS)
        t0 = time.time()
        transcribe(assets.OUT / "sample.mp4", d, (0.0, 3.0))  # já baixa/carrega o modelo de legenda
        log.info("Autoteste de legenda OK (%.1fs, %s)", time.time() - t0, "API" if (GROQ_KEY or OPENAI_KEY) else f"local {LOCAL_MODEL}")
    except Exception as e:
        log.error("Autoteste de corte FALHOU: %s", str(e)[-1500:])


try:
    _restore()
except Exception as e:
    log.error("Falha ao retomar cortes: %s", e)
threading.Thread(target=_boot_disk, daemon=True).start()
threading.Thread(target=_selftest, daemon=True).start()


def resume_with_words(jid: str, words: list[dict]):
    j = JOBS.get(jid)
    if not j or j.get("status") != "revisar":
        raise ValueError("Esse corte não está esperando revisão")
    _set(jid, words=words, reviewed=True, status="na fila", progress=60)
    _q.put((-int(j.get("priority", 0)) - 1, next(_seq), jid))
