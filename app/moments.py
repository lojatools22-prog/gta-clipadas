"""Corte automático: ouve a live inteira, transcreve em partes e pede para a IA os momentos com mais chance de viralizar."""
import json, os, queue, re, shutil, subprocess, threading, time, uuid
from pathlib import Path
import httpx

from . import video

log = video.log
DIR = video.DATA / "analises"
DIR.mkdir(parents=True, exist_ok=True)
MAX_SECONDS = int(os.getenv("MOMENTS_MAX_SECONDS", str(12 * 3600)))
LIVE_WINDOW = int(os.getenv("MOMENTS_LIVE_WINDOW", str(3 * 3600)))  # live no ar: analisa as últimas 3 horas
CHUNK = 600  # 10 minutos por pedaço de áudio
WINDOW = 420  # a IA olha 7 minutos de conversa por vez
MODEL = os.getenv("MOMENTS_MODEL", "llama-3.3-70b-versatile")
GROQ = "https://api.groq.com/openai/v1"

STATE: dict[str, dict] = {}
_q: "queue.Queue[str]" = queue.Queue()
_lock = threading.Lock()


def _save(aid):
    st = STATE.get(aid)
    if st:
        (DIR / aid / "estado.json").write_text(json.dumps(st))


def _set(aid, **kw):
    with _lock:
        STATE[aid].update(kw)
        _save(aid)


def get(aid: str) -> dict | None:
    st = STATE.get(aid)
    if st is None and (DIR / aid / "estado.json").exists():
        st = json.loads((DIR / aid / "estado.json").read_text())
        if st.get("status") not in ("pronto", "erro"):  # o servidor reiniciou no meio da análise
            st.update(status="erro", error="A análise foi interrompida. Toque em analisar de novo.")
        STATE[aid] = st
    return st


def source_of(aid: str) -> Path | None:
    st = get(aid)
    if st and st.get("source") and Path(st["source"]).exists():
        return Path(st["source"])
    return None


def create(user_id: int, url: str | None = None, upload: Path | None = None, auto: dict | None = None, name: str = "") -> dict:
    aid = uuid.uuid4().hex[:12]
    d = DIR / aid
    d.mkdir(parents=True)
    st = {"id": aid, "user_id": user_id, "status": "na fila", "progress": 2, "url": url, "created": time.time(), "moments": [], "auto": auto, "name": name[:120]}
    if upload:
        sd = video.SCRATCH / "analises" / aid
        sd.mkdir(parents=True, exist_ok=True)
        dst = sd / ("fonte" + upload.suffix)
        shutil.move(str(upload), dst)
        st["source"] = str(dst)
    STATE[aid] = st
    _save(aid)
    _q.put(aid)
    return public(st)


ON_READY = None  # chamado com o id da análise quando ela termina com "gerar sozinho" ligado (main cria os cortes)


def public(st: dict) -> dict:
    out = {k: st.get(k) for k in ("id", "status", "progress", "moments", "error", "duration", "title", "url", "created", "note")}
    out["auto"] = bool(st.get("auto"))
    out["name"] = st.get("title") or st.get("name") or (st.get("url") or "Vídeo enviado")
    return out


def list_for(user_id: int, limit: int = 30) -> list:
    """Vídeos (projetos) do usuário, do mais novo pro mais velho."""
    out = []
    for f in DIR.glob("*/estado.json"):
        try:
            st = STATE.get(f.parent.name) or json.loads(f.read_text())
        except Exception:
            continue
        if st.get("user_id") == user_id and st.get("auto"):
            out.append(st)
    out.sort(key=lambda s: -s.get("created", 0))
    return [public(s) for s in out[:limit]]


# ---------------- trabalho ----------------
def _groq_key():
    return video.GROQ_KEY


def _post(path, retries=8, **kw):
    """Chama o Groq respeitando o limite de uso (espera e tenta de novo no 429)."""
    for i in range(retries):
        r = httpx.post(GROQ + path, headers={"Authorization": f"Bearer {_groq_key()}"}, timeout=300, **kw)
        if r.status_code == 429 or r.status_code >= 500:
            wait = float(r.headers.get("retry-after") or 0) or min(60, 6 * (i + 1))
            time.sleep(min(wait, 90))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("A IA está ocupada agora. Tente de novo em alguns minutos.")


def _audio(st: dict, d: Path) -> Path:
    out = d / "audio.mp3"
    if st.get("source"):
        src = st["source"]
    else:
        info = video.probe_url(st["url"])
        live = bool(info.get("live"))
        st["live"] = live
        st["title"] = info.get("title")
        st["heatmap"] = info.get("heatmap") or []
        if not live and info.get("duration", 0) > MAX_SECONDS:
            raise RuntimeError(f"O vídeo tem mais de {MAX_SECONDS // 3600} horas. Corte em partes ou use um trecho menor.")
        args = ["--no-playlist", "-f", "worstaudio[abr>=32]/worstaudio/bestaudio/worst", "-o", str(d / "baixado.%(ext)s")]
        if live:  # live ainda no ar: analisa as últimas horas que já passaram (o YouTube guarda o começo via DVR)
            end = info.get("duration", 0)
            st["offset"] = max(0.0, end - LIVE_WINDOW)
            args += ["--live-from-start", "--download-sections", f"*{st['offset']:.0f}-{end:.0f}"]
        video.ytdlp(args, st["url"], timeout=3600)
        src = str(next(d.glob("baixado.*")))
    video._run(["ffmpeg", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", str(out)], timeout=1800)
    for f in d.glob("baixado.*"):
        f.unlink(missing_ok=True)
    return out


def _transcribe(audio: Path, d: Path, aid: str, dur: float) -> list[dict]:
    """Transcreve em pedaços de 10 min. Devolve frases com início e fim em segundos."""
    segs = []
    n = max(1, int(dur // CHUNK) + (1 if dur % CHUNK > 1 else 0))
    for i in range(n):
        part = d / f"parte{i}.mp3"
        video._run(["ffmpeg", "-y", "-ss", str(i * CHUNK), "-t", str(CHUNK), "-i", str(audio), "-c", "copy", str(part)])
        if part.stat().st_size < 2000:
            continue
        if _groq_key():
            with open(part, "rb") as f:
                j = _post("/audio/transcriptions", files={"file": (part.name, f, "audio/mpeg")},
                          data={"model": "whisper-large-v3-turbo", "language": "pt", "response_format": "verbose_json"})
            for s in j.get("segments", []):
                segs.append({"start": s["start"] + i * CHUNK, "end": s["end"] + i * CHUNK, "text": s["text"].strip()})
        else:
            for w in video._local_words(part):
                segs.append({"start": w["start"] + i * CHUNK, "end": w["end"] + i * CHUNK, "text": w["word"].strip()})
        part.unlink(missing_ok=True)
        _set(aid, progress=10 + int(55 * (i + 1) / n), status="ouvindo")
    return segs


def _loud_peaks(audio: Path, dur: float) -> list[float]:
    """Segundos com o áudio mais alto (grito, risada, explosão): ajudam a IA a achar a hype."""
    try:
        p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(audio), "-af", "asetnsamples=16000,astats=metadata=1:reset=1,"
                            "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=900)
        vals, t = [], 0
        for line in p.stdout.splitlines():
            m = re.search(r"RMS_level=(-?[\d.]+|-inf)", line)
            if m:
                v = float(m.group(1)) if m.group(1) != "-inf" else -99
                vals.append((v, t)); t += 1
        vals.sort(reverse=True)
        out = []
        for v, s in vals:
            if all(abs(s - o) > 30 for o in out):
                out.append(s)
            if len(out) >= max(5, int(dur // 300)):
                break
        return sorted(out)
    except Exception:
        return []


def _chat_peaks(st: dict, d: Path, dur: float) -> list[tuple[float, int]]:
    """Picos do chat da live (replay do YouTube ou da Twitch): quando o chat explode, aconteceu alguma coisa."""
    if not st.get("url"):
        return []
    try:
        video.ytdlp(["--no-playlist", "--skip-download", "--write-subs", "--sub-langs", "live_chat,rechat", "-o", str(d / "chat.%(ext)s")],
                    st["url"], timeout=600)
    except Exception as e:
        log.info("Momentos: sem replay do chat (%s)", str(e)[:120])
    ts = []
    for f in d.glob("chat*"):
        try:
            raw = f.read_text(errors="ignore")
        except Exception:
            continue
        ts += [int(x) / 1000 for x in re.findall(r'"videoOffsetTimeMsec"\s*:\s*"?(\d+)', raw)]
        ts += [float(x) for x in re.findall(r'"content_offset_seconds"\s*:\s*([\d.]+)', raw)]
        f.unlink(missing_ok=True)
    if len(ts) < 50:
        return []
    bins = [0] * (int(dur // 20) + 2)  # mensagens a cada 20 s
    for t in ts:
        if 0 <= t <= dur:
            bins[int(t // 20)] += 1
    avg = sum(bins) / max(1, len(bins))
    order = sorted(range(len(bins)), key=lambda i: -bins[i])
    out = []
    for i in order:
        if bins[i] < max(5, avg * 2.5):
            break
        t = i * 20.0
        if all(abs(t - o) > 60 for o, _ in out):
            out.append((t, bins[i] * 3))  # msgs por minuto
        if len(out) >= max(6, int(dur // 400)):
            break
    log.info("Momentos: %d mensagens no chat, %d picos", len(ts), len(out))
    return sorted(out)


def _heat_peaks(st: dict) -> list[float]:
    hm = st.get("heatmap") or []
    if not hm:
        return []
    top = sorted(hm, key=lambda h: -h[2])[:max(3, len(hm) // 10)]
    return sorted(h[0] for h in top if h[2] >= 0.5)


CHAT_MODELS = [MODEL, "openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b", "llama-3.1-8b-instant"]
_MODEL_OK = {"m": None}


def _chat(messages):
    """Pede a escolha à IA. Se um modelo não existir mais na conta (404/400), tenta o próximo e lembra o que funcionou."""
    order = ([_MODEL_OK["m"]] if _MODEL_OK["m"] else []) + [m for m in dict.fromkeys(CHAT_MODELS) if m != _MODEL_OK["m"]]
    last = None
    for m in order:
        body = {"model": m, "temperature": 0.3, "messages": messages, "response_format": {"type": "json_object"}}
        try:
            j = _post("/chat/completions", json=body)
            if _MODEL_OK["m"] != m:
                log.info("Momentos: usando o modelo %s", m)
            _MODEL_OK["m"] = m
            return j
        except httpx.HTTPStatusError as e:
            last = e
            if e.response.status_code in (400, 403, 404):
                log.warning("Momentos: modelo %s indisponível (%s): %s", m, e.response.status_code, e.response.text[:200])
                continue
            raise
    raise last


def _fallback(segs, peaks, dur, chat=(), heat=()):
    """Se a IA não escolher nada, monta os cortes pelos sinais: chat, mais repetidos e picos de áudio."""
    cands = [(p, 90, "O chat explodiu nesse momento") for p, _ in chat] + [(p, 80, "Parte que o público mais reviu") for p in heat] + \
            [(p, 65, "Pico de reação no áudio") for p in peaks]
    out = []
    for p, score, why in cands:
        a = max(0, p - 15); b = min(dur, a + 40)
        if b - a < 12 or any(not (b <= o["start"] or a >= o["end"]) for o in out):
            continue
        txt = " ".join(s["text"] for s in segs if a <= s["start"] < b)[:60].strip()
        out.append({"start": round(a), "end": round(b), "title": (txt.upper()[:40] or "MOMENTO DA LIVE"), "score": score, "reason": why})
    out.sort(key=lambda m: -m["score"])
    return out[:12]


def _fmt(s):
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


PROMPT = """Você é um editor de cortes de lives de GTA (GTA RP, GTA 6) que sabe o que viraliza no TikTok, Reels e Shorts no Brasil.
Abaixo está a transcrição de um trecho da live com o tempo de cada fala. Momentos com áudio muito alto estão marcados com [PICO DE ÁUDIO].
[CHAT EXPLODIU] marca quando o chat da live disparou de mensagens e [MAIS REPETIDO] marca onde o público voltou pra rever o vídeo: são os sinais mais fortes de momento bom, priorize-os.
Escolha até {n} momentos com mais chance de viralizar: reação forte, piada, briga, susto, perseguição, fala absurda, plot twist, momento engraçado ou épico.
Cada momento deve ter de 20 a 60 segundos, começar um pouco antes da ação (pra dar contexto) e terminar logo depois do auge.
Se o trecho não tiver nada bom, devolva lista vazia. Não invente falas.
Responda só JSON: {{"momentos":[{{"inicio":"H:MM:SS","fim":"H:MM:SS","titulo":"FRASE DE IMPACTO EM CAIXA ALTA, ATÉ 6 PALAVRAS","nota":0-100,"motivo":"uma frase"}}]}}

TRANSCRIÇÃO:
{texto}"""


def _secs(v):
    try:
        if isinstance(v, (int, float)):
            return float(v)
        p = [float(x) for x in str(v).strip().split(":")]
        return sum(x * 60 ** i for i, x in enumerate(reversed(p)))
    except Exception:
        return None


def _pick(segs: list[dict], peaks: list[float], dur: float, aid: str, chat=(), heat=()) -> list[dict]:
    if not segs:
        return []
    found = []
    windows = list(range(0, int(dur) + 1, WINDOW))
    for wi, w0 in enumerate(windows):
        w1 = w0 + WINDOW + 30
        lines = [f"[{_fmt(s['start'])}] {s['text']}" for s in segs if w0 <= s["start"] < w1 and s["text"]]
        if len(" ".join(lines)) < 120:
            continue
        for p in peaks:
            if w0 <= p < w1:
                lines.append(f"[{_fmt(p)}] [PICO DE ÁUDIO]")
        for p, n in chat:
            if w0 <= p < w1:
                lines.append(f"[{_fmt(p)}] [CHAT EXPLODIU: {n} msgs/min]")
        for p in heat:
            if w0 <= p < w1:
                lines.append(f"[{_fmt(p)}] [MAIS REPETIDO]")
        lines.sort()
        try:
            j = _chat([{"role": "user", "content": PROMPT.format(n=2, texto="\n".join(lines)[:14000])}])
            data = json.loads(j["choices"][0]["message"]["content"])
        except Exception as e:
            log.warning("Momentos: janela %s falhou: %s", wi, str(e)[:200])
            continue
        for m in data.get("momentos", [])[:3]:
            a, b = _secs(m.get("inicio")), _secs(m.get("fim"))
            if a is None or b is None:
                continue
            a, b = max(0.0, a - 1), min(dur, b + 1)
            if b - a < 12:
                b = min(dur, a + 25)
            if b - a > 90:
                b = a + 60
            found.append({"start": round(a), "end": round(b), "title": str(m.get("titulo", "")).upper()[:60],
                          "score": int(max(0, min(100, float(m.get("nota", 50))))), "reason": str(m.get("motivo", ""))[:140]})
        _set(aid, progress=65 + int(33 * (wi + 1) / len(windows)), status="escolhendo")
    for m in found:  # bônus quando o momento bate com o chat ou com o "mais repetidos"
        near = lambda t: m["start"] - 20 <= t <= m["end"] + 5
        if any(near(p) for p, _ in chat):
            m["score"] = min(100, m["score"] + 12); m["chat"] = True
        if any(near(p) for p in heat):
            m["score"] = min(100, m["score"] + 10); m["replay"] = True
    found.sort(key=lambda m: -m["score"])
    best = []
    for m in found:
        if all(m["end"] <= o["start"] or m["start"] >= o["end"] for o in best):
            best.append(m)
        if len(best) >= 12:
            break
    return best


def _work(aid: str):
    st = STATE[aid]
    d = DIR / aid
    try:
        if not _groq_key():
            raise RuntimeError("O corte automático precisa da IA configurada no servidor.")
        _set(aid, status="baixando" if st.get("url") and not st.get("source") else "preparando", progress=5)
        audio = _audio(st, d)
        dur = video.probe_duration(audio)
        if dur > MAX_SECONDS:
            raise RuntimeError(f"O vídeo tem mais de {MAX_SECONDS // 3600} horas. Corte em partes.")
        _set(aid, duration=dur, title=st.get("title"), status="ouvindo", progress=10)
        segs = _transcribe(audio, d, aid, dur)
        (d / "transcricao.json").write_text(json.dumps(segs, ensure_ascii=False))
        peaks = _loud_peaks(audio, dur)
        audio.unlink(missing_ok=True)
        chat = [] if st.get("live") else _chat_peaks(st, d, dur)  # chat de live no ar não termina de baixar
        heat = _heat_peaks(st)
        moments = _pick(segs, peaks, dur, aid, chat, heat) or _fallback(segs, peaks, dur, chat, heat)
        off = st.get("offset") or 0
        for m in moments:  # live no ar: os tempos da análise contam do começo do trecho baixado
            m["start"] += round(off); m["end"] += round(off)
        if not moments:
            raise RuntimeError("Não achamos momentos fortes nesse vídeo. Tente outro ou escolha o trecho na mão.")
        if st.get("auto") and ON_READY:
            _set(aid, status="cortando", progress=99, moments=moments)
            try:
                ON_READY(aid)
            except Exception as e:
                log.error("Cortes automáticos da análise %s falharam: %s", aid, e)
                _set(aid, note=str(getattr(e, "detail", e))[:200])
            _set(aid, status="pronto", progress=100)
        else:
            _set(aid, status="pronto", progress=100, moments=moments)
        log.info("Momentos %s: %s achados em %.0f min de vídeo", aid, len(moments), dur / 60)
    except Exception as e:
        msg = str(e)
        if "código" in msg or "Traceback" in msg or len(msg) > 200:
            log.error("Momentos %s falhou: %s", aid, msg[-800:])
            fe = video.friendly_error(msg)
            msg = fe if not fe.startswith("Algo deu errado") else "Não conseguimos analisar esse vídeo. Confira se o link é público ou tente subir o arquivo."
        _set(aid, status="erro", error=msg)


def _worker():
    while True:
        aid = _q.get()
        try:
            _work(aid)
        except Exception as e:
            log.error("Momentos worker: %s", e)
        finally:
            _q.task_done()


def _cleanup():
    while True:
        lim = time.time() - 2 * 86400  # vídeo enviado para análise fica 2 dias
        for d in DIR.iterdir():
            try:
                if d.is_dir() and d.stat().st_mtime < lim:
                    shutil.rmtree(d, ignore_errors=True)
                    shutil.rmtree(video.SCRATCH / "analises" / d.name, ignore_errors=True)
                    STATE.pop(d.name, None)
            except Exception:
                pass
        time.sleep(3 * 3600)


threading.Thread(target=_worker, daemon=True).start()
threading.Thread(target=_cleanup, daemon=True).start()
