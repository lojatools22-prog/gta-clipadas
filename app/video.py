"""Pipeline de corte: baixa o trecho, transcreve, monta 9:16 com template GTA e legenda."""
import os, re, json, uuid, time, shutil, subprocess, threading, logging, traceback
from pathlib import Path
import httpx

from . import assets, art, templates, captions, framing

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
LAYOUT_LABELS = {"gtabg": "Fundo GTA", "blur_plain": "Fundo desfocado", "letterbox": "Faixas pretas", "cam_top": "Facecam em cima", "cam_bottom": "Facecam embaixo", "float": "Facecam flutuante",
                 "blur": "Sem facecam", "split": "Dividido", "crop": "Tela cheia", "moldura": "Moldura", "inset": "Moldura de objeto"}
CAM_CORNERS = ["top-left", "top-right", "bottom-left", "bottom-right"]


def _cam(corner, cs):
    x = "0" if "left" in corner else f"iw-iw*{cs}"
    y = "0" if "top" in corner else f"ih-ih*{cs}"
    return f"crop=iw*{cs}:ih*{cs}:{x}:{y}"


def _cover(w, h):
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"


def layout_filter(lay: dict, corner: str, cs: float, cam_ref: str | None = None, fr: dict | None = None) -> str:
    k = lay["kind"]
    face = (fr or {}).get("cam")
    if corner == "auto" and fr is not None and not face and not cam_ref and k in ("cam_top", "cam_bottom", "split", "float"):
        # a IA não achou facecam nesse vídeo: em vez de recortar um canto qualquer, mostra o vídeo inteiro
        if k == "float":
            return f"[0:v]{_cover(W, H)}[base]"
        k = "blur_plain"

    def camf(w, h):
        if face:
            return framing.cam_crop(face, w, h, (fr or {}).get("aspect", 16 / 9))
        return f"{_cam('top-left' if corner == 'auto' else corner, cs)},{_cover(w, h)}"

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
        f = f"[0:v]split=2[a][b];[a]{camf(W, ch)}[cam];[b]{_cover(W, gh)}[game];"
        return f + ("[game][cam]" if k == "cam_bottom" else "[cam][game]") + "vstack=inputs=2[base]"
    if k == "float":
        return (f"[0:v]split=2[a][b];[b]{_cover(W, H)}[game];[a]{camf(424, 234)}[cam];"
                f"[game][cam]overlay=48:198[base]")
    if k == "blur":
        return (f"[0:v]split=2[a][b];[a]{_cover(W, H)},boxblur=25:3,eq=brightness=-0.05,colorchannelmixer=rr=0.85:gg=0.45:bb=0.95[bg];"
                f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[base]")
    if k == "crop":
        if (fr or {}).get("face_big"):  # live de câmera: o corte segue o rosto
            return f"[0:v]{framing.focus_crop(fr['focus'], W, H)}[base]"
        return f"[0:v]{_cover(W, H)}[base]"
    if k == "blur_plain":  # vídeo inteiro no meio, o próprio vídeo desfocado atrás, sem tingir
        return (f"[0:v]split=2[a][b];[a]{_cover(W, H)},boxblur=30:4,eq=brightness=-0.12[bg];"
                f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[base]")
    if k == "gtabg":  # vídeo inteiro numa janela no meio; a arte de fundo (com o buraco) entra por cima como overlay
        return f"[0:v]{_cover(1000, 562)},pad={W}:{H}:40:600:color=black[base]"
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
        vf = layout_filter(tpl["layout"], opts.get("corner", "top-left"), float(opts.get("cam_size", 0.30)), cam_ref if slot else None,
                           opts.get("_frame"))
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
    if opts.get("_punch"):  # zoom rápido + tremida nos picos de reação (grito, risada, explosão)
        z = "+".join(f"0.11*exp(-pow((t-{p:.2f})/0.22,2))" for p in opts["_punch"])
        sh = "+".join(f"14*sin(t*55)*exp(-pow((t-{p:.2f})/0.18,2))" for p in opts["_punch"])
        o = nxt()
        vf += (f";{cur}scale=w='trunc({W}*(1+{z})/2)*2':h='trunc({H}*(1+{z})/2)*2':eval=frame,"
               f"crop={W}:{H}:x='(iw-{W})/2+({sh})':y='(ih-{H})/2'{o}")
        cur = o
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


# O YouTube às vezes barra servidores ("confirm you're not a bot"). Tentamos outros clientes do player antes de desistir.
YT_CLIENTS = [None, "tv_simply", "tv", "mweb", "web_safari", "android_vr"]
_YT_OK = {"client": None}


def _is_yt(url: str) -> bool:
    return any(h in url for h in ("youtube.com", "youtu.be"))


def _cookies_file():
    """Cookies do YouTube: caminho em YTDLP_COOKIES ou o conteúdo do cookies.txt em YTDLP_COOKIES_TXT."""
    p = os.getenv("YTDLP_COOKIES")
    if p and Path(p).exists():
        return Path(p)
    txt = os.getenv("YTDLP_COOKIES_TXT")
    if txt:
        f = SCRATCH / "yt_cookies.txt"
        if not f.exists():
            f.write_text(_fix_cookies(txt))
        return f
    return None


_CK_REC = re.compile(r"((?:#HttpOnly_)?(?:\.|www\.|m\.|music\.|accounts\.)?(?:youtube\.com|google\.com(?:\.br)?|youtu\.be))\t(TRUE|FALSE)\t([^\t]*)\t(TRUE|FALSE)\t(\d+)\t([^\t]+)\t")


def _fix_cookies(txt: str) -> str:
    """Remonta o cookies.txt (formato Netscape). Campos de uma linha só (como o do Railway) engolem as quebras de linha."""
    txt = txt.replace("\\n", "\n").replace("\\t", "\t").replace("\r", "")
    ms = list(_CK_REC.finditer(txt))
    if not ms:
        return txt
    lines = ["# Netscape HTTP Cookie File"]
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(txt)
        val = txt[m.end():end].split("\n")[0].strip()
        lines.append("\t".join(m.groups()) + "\t" + val)
    log.info("Cookies do YouTube carregados: %d", len(lines) - 1)
    return "\n".join(lines) + "\n"


def friendly_error(msg: str) -> str:
    m = msg or ""
    if "not a bot" in m or "429" in m or "Sign in" in m:
        return "O YouTube bloqueou o download desse vídeo agora. Tente de novo em alguns minutos, use um link da Twitch ou Kick, ou envie o arquivo do vídeo."
    if "Private video" in m or "private" in m.lower():
        return "Esse vídeo é privado, então não dá pra baixar pelo link. Grave a tela do trecho ou baixe o vídeo e envie o arquivo aqui."
    if "claimed content" in m or "in your country" in m:
        return ("Essa live tem música com direitos autorais e o YouTube bloqueia o download dela. Solução: grave a tela do trecho "
                "que você quer (ou baixe o vídeo) e envie o arquivo aqui. A IA corta igualzinho.")
    if "is not available" in m or "unavailable" in m.lower() or "404" in m:
        return "Esse vídeo não está disponível pelo link (pode ter sido removido ou ter restrição). Grave a tela do trecho ou baixe o vídeo e envie o arquivo aqui."
    if "live event will begin" in m:
        return "Essa live ainda não começou."
    if "is_live" in m or "live-from-start" in m or "This live event" in m:
        return "Não deu pra pegar esse trecho da live ao vivo. Tente um trecho mais recente (o YouTube guarda só as últimas horas) ou use o VOD quando acabar."
    if "Disco" in m or "No space" in m:
        return "O servidor está cheio agora. Tente de novo em alguns minutos."
    return "Não conseguimos baixar ou montar esse vídeo pelo link. O jeito mais garantido: baixe o vídeo (ou grave a tela do trecho) e envie o arquivo aqui."


def _geo_proxy(px):
    """Força IP do Brasil no proxy (vídeo bloqueado por direitos em outro país, ex.: "claimed content by WMG").
    DataImpulse: o país vai no usuário como __cr.br. Muda só o usuário; a senha não é tocada."""
    cc = os.getenv("YTDLP_PROXY_COUNTRY", "br").lower()
    if not px or not cc or "dataimpulse" not in px or "__cr." in px:
        return px
    from urllib.parse import urlsplit, urlunsplit
    u = urlsplit(px)
    if not u.username:
        return px
    net = u.netloc.replace(u.username + ":", f"{u.username}__cr.{cc}:", 1) if u.password else u.netloc.replace(u.username + "@", f"{u.username}__cr.{cc}@", 1)
    return urlunsplit(u._replace(netloc=net))


_BLOCK = ("not a bot", "Sign in", "403", "429", "Requested format is not available", "player response", "claimed content", "not available in your country", "blocked it in your country")


def ytdlp(args: list, url: str, timeout: int):
    """Roda o yt-dlp. No YouTube tenta primeiro direto (de graça); se o YouTube barrar, passa pelo proxy residencial
    (YTDLP_PROXY, pago por GB) e tenta outros clientes do player. Depois de um bloqueio, vai direto pro proxy por 30 min."""
    base = ["yt-dlp"]
    ck = _cookies_file()
    if ck:
        base += ["--cookies", str(ck)]
    if not _is_yt(url):
        return _run(base + args + [url], timeout=timeout)
    proxy = _geo_proxy(os.getenv("YTDLP_PROXY"))
    plan = []
    if not proxy or _YT_OK.get("blocked_until", 0) < time.time():
        plan.append((None, _YT_OK["client"]))
    if proxy:
        plan += [(proxy, c) for c in [_YT_OK["client"]] + [c for c in YT_CLIENTS if c != _YT_OK["client"]]]
    else:
        plan += [(None, c) for c in YT_CLIENTS if c != _YT_OK["client"]]
    last = None
    for px, c in plan:
        extra = (["--proxy", px] if px else []) + (["--extractor-args", f"youtube:player_client={c}"] if c else [])
        try:
            out = _run(base + extra + args + [url], timeout=timeout)
            _YT_OK["client"] = c
            if px:
                log.info("yt-dlp: YouTube via proxy residencial")
            return out
        except Exception as e:
            last = e
            if not any(k in str(e) for k in _BLOCK):
                raise
            if not px:
                _YT_OK["blocked_until"] = time.time() + 1800
            log.warning("yt-dlp: %s cliente %s barrado, tentando o próximo", "proxy" if px else "direto", c or "padrão")
    raise last


def probe_url(url: str) -> dict:
    """Duração, título e thumb de um link (YouTube, Twitch, Kick) sem baixar o vídeo."""
    hit = _PROBE.get(url)
    if hit and time.time() - hit[0] < (60 if hit[1].get("live") else 3600):
        return hit[1]
    out = ytdlp(["-J", "--no-playlist", "--skip-download", "--no-warnings"], url, timeout=40)
    j = json.loads(out)
    info = {"duration": float(j.get("duration") or 0), "title": (j.get("title") or "")[:140],
            "thumb": j.get("thumbnail") or "", "live": bool(j.get("is_live")),
            "live_start": float(j.get("release_timestamp") or j.get("timestamp") or 0),
            "extractor": (j.get("extractor_key") or "").lower(), "id": j.get("id") or "",
            # gráfico "mais repetidos" do YouTube: onde o público voltou pra rever
            "heatmap": [(float(h.get("start_time", 0)), float(h.get("end_time", 0)), float(h.get("value", 0))) for h in (j.get("heatmap") or [])]}
    if info["live"] and info["live_start"]:
        info["duration"] = max(0.0, time.time() - info["live_start"] - 30)  # o que já passou da live (folga de 30 s)
    if len(_PROBE) > 500:
        _PROBE.clear()
    _PROBE[url] = (time.time(), info)
    return info


def download(url: str, start: float | None, end: float | None, dest: Path) -> Path:
    cmd = ["--no-playlist", "-f", "best[height<=1080]/bestvideo[height<=1080]+bestaudio/best",
           "--merge-output-format", "mp4", "-o", str(dest / "src.%(ext)s")]
    if start is not None and end is not None:
        cmd += ["--download-sections", f"*{start:.2f}-{end:.2f}"]
    if True:
        try:
            if probe_url(url).get("live"):  # live ainda no ar: o YouTube guarda o que já passou (DVR); os tempos contam do início
                cmd += ["--live-from-start"]
        except Exception:
            pass
    ytdlp(cmd, url, timeout=900)
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


def _silences(src: Path, a: float, d: float, noise="-33dB", mind=0.6):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{a:.2f}", "-t", f"{d:.2f}", "-i", str(src), "-vn",
                          "-af", f"silencedetect=noise={noise}:d={mind}", "-f", "null", "-"], capture_output=True, text=True, timeout=300).stderr
    st = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", out)]
    en = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", out)]
    return list(zip(st, en + [d] * (len(st) - len(en))))


def tighten(src: Path, trim, job_dir: Path):
    """Corte seco nos silêncios (pausas > 0,6 s viram 0,15 s): deixa o corte com ritmo de edição feita à mão."""
    a = trim[0] if trim else 0.0
    d = (trim[1] - trim[0]) if trim else probe_duration(src)
    if not has_audio(src):
        return src, trim
    sil = [(max(0, s0), min(d, s1)) for s0, s1 in _silences(src, a, d) if s1 - s0 > 0.6]
    cut = sum(s1 - s0 - 0.15 for s0, s1 in sil)
    if cut < 0.8 or cut > d * 0.5:  # quase nada a cortar, ou o vídeo é quase todo silêncio (não mexe)
        return src, trim
    keep, t = [], 0.0
    for s0, s1 in sil:
        keep.append((t, s0 + 0.08)); t = s1 - 0.07
    keep.append((t, d))
    keep = [(x, y) for x, y in keep if y - x > 0.05]
    sel = "+".join(f"between(t,{x:.3f},{y:.3f})" for x, y in keep)
    out = job_dir / "tight.mp4"
    _run(["ffmpeg", "-y", "-ss", f"{a:.2f}", "-t", f"{d:.2f}", "-i", str(src), "-filter_complex",
          f"[0:v]select='{sel}',setpts=N/FRAME_RATE/TB[v];[0:a]aselect='{sel}',asetpts=N/SR/TB[a]",
          "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-c:a", "aac", "-b:a", "192k", str(out)], timeout=900)
    log.info("Corte seco: %.1fs de silêncio removidos (%d pausas)", cut, len(sil))
    return out, None


def punch_points(src: Path, trim, dur: float) -> list[float]:
    """Momentos de pico de volume no corte (onde entra o zoom)."""
    a = trim[0] if trim else 0.0
    try:
        p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{a:.2f}", "-t", f"{dur:.2f}", "-i", str(src), "-vn", "-ac", "1", "-ar", "8000",
                            "-af", "asetnsamples=800,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
                            "-f", "null", "-"], capture_output=True, text=True, timeout=300)
        vals = [float(m) if m != "-inf" else -99 for m in re.findall(r"RMS_level=(-?[\d.]+|-inf)", p.stdout)]
    except Exception:
        return []
    if len(vals) < 20:
        return []
    srt = sorted(vals)
    thr = max(srt[int(len(srt) * 0.93)], sum(vals) / len(vals) + 9)
    pts = []
    for i, v in sorted(enumerate(vals), key=lambda x: -x[1]):
        t = i * 0.1
        if v < thr or len(pts) >= max(2, int(dur // 8)):
            break
        if 0.6 < t < dur - 0.4 and all(abs(t - q) > 2.5 for q in pts):
            pts.append(t)
    return sorted(pts)


def render(src: Path, job_dir: Path, opts: dict, trim: tuple | None, words: list[dict], cam: Path | None = None) -> Path:
    tpl = templates.BY_ID.get(opts.get("template"), templates.TEMPLATES[0])
    cap = templates.CAPTION_BY_ID.get(opts.get("caption"), templates.CAPTION_STYLES[0])
    dur = (trim[1] - trim[0]) if trim else probe_duration(src)
    opts = {**opts, "_portrait": is_portrait(src)}
    if not opts["_portrait"] and opts.get("corner", "auto") == "auto":
        opts["corner"] = "auto"
        try:  # enquadramento automático: acha a facecam e o ponto de ação
            a = trim[0] if trim else 0.0
            opts["_frame"] = framing.analyze(src, a, a + dur)
        except Exception as e:
            log.error("Enquadramento automático falhou: %s", e)
    if opts.get("punch", True):
        opts["_punch"] = punch_points(src, trim, dur)
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
        if o.get("tight", True) and not j.get("reviewed") and not j.get("tightened"):
            try:
                src, trim = tighten(src, trim, scratch(jid))
                _set(jid, src_path=str(src), tightened=True)
            except Exception as e:
                log.error("Corte seco falhou (segue sem): %s", str(e)[-300:])
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
        _set(jid, status="erro", error=str(e)[-1500:], hint=friendly_error(str(e)))
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
    _yt_selftest()


def _yt_selftest():
    """Teste ponta a ponta com um vídeo real do YouTube: baixa 12s, legenda e monta o corte. Só registra no log."""
    url = os.getenv("YT_TEST_URL", "https://youtu.be/hhM2RLXk4kk")
    if not url:
        return
    d = Path("/tmp/autoteste_yt")
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    t0 = time.time()
    try:
        info = probe_url(url)
        a = min(60.0, max(0.0, info["duration"] / 3))
        src = download(url, a, a + 12, d)
        words = transcribe(src, d, None)
        render(src, d, {"template": "fundo-skyline", "caption": "pesada-karaoke", "title": "AUTOTESTE YOUTUBE"}, None, words)
        log.info("Autoteste do YouTube OK: '%s' baixado, legendado (%d palavras) e cortado em %.0fs (%s bytes)",
                 info["title"][:60], len(words), time.time() - t0, (d / "corte.mp4").stat().st_size)
        from . import moments  # importa aqui pra evitar import circular
        txt = " ".join(w["word"] for w in words)[:600] or "teste"
        j = moments._chat([{"role": "user", "content": 'Responda só JSON {"ok": true, "resumo": "..."} resumindo em 5 palavras: ' + txt}])
        log.info("Autoteste da IA de momentos OK (modelo %s): %s", moments._MODEL_OK["m"], j["choices"][0]["message"]["content"][:120])
    except Exception as e:
        log.error("Autoteste do YouTube FALHOU: %s", friendly_error(str(e)) + " | " + str(e)[-400:])


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
