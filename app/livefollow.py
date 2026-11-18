"""Cortar AO VIVO: o servidor acompanha a live enquanto ela acontece.

Grava a live em blocos (padrão 8 min) a partir de agora; cada bloco vira uma análise com "gerar sozinho"
(moments.create com o arquivo), então os melhores cortes de cada bloco vão aparecendo em Meus vídeos
enquanto a live continua. Funciona até em live que não deixa voltar no tempo (sem DVR).
"""
import json, os, shutil, threading, time, uuid, logging
from pathlib import Path

from . import video, moments

log = logging.getLogger("uvicorn.error")
DIR = video.DATA / "aovivo"
DIR.mkdir(parents=True, exist_ok=True)
CHUNK = int(os.getenv("LIVE_CHUNK_SECONDS", "480"))
STATE: dict[str, dict] = {}
_lock = threading.Lock()


def _save(fid):
    st = STATE[fid]
    (DIR / f"{fid}.json").write_text(json.dumps(st, ensure_ascii=False))


def _set(fid, **kw):
    with _lock:
        STATE[fid].update(kw)
        _save(fid)


def get(fid):
    st = STATE.get(fid)
    if st is None and (DIR / f"{fid}.json").exists():
        st = json.loads((DIR / f"{fid}.json").read_text())
        STATE[fid] = st
    return st


def public(st):
    return {k: st.get(k) for k in ("id", "url", "title", "status", "minutes", "started", "until", "blocks", "videos", "error", "note")}


def active_for(user_id):
    return [s for s in STATE.values() if s.get("user_id") == user_id and s.get("status") in ("na fila", "gravando", "analisando")]


def start(user_id: int, url: str, minutes: int, auto: dict, left_fn) -> dict:
    fid = uuid.uuid4().hex[:12]
    st = {"id": fid, "user_id": user_id, "url": url, "title": "", "status": "na fila", "minutes": minutes, "started": time.time(),
          "until": time.time() + minutes * 60, "blocks": 0, "videos": [], "auto": auto, "error": None, "note": None}
    STATE[fid] = st
    _save(fid)
    threading.Thread(target=_run, args=(fid, left_fn), daemon=True).start()
    return public(st)


def stop(fid):
    st = get(fid)
    if st and st.get("status") in ("na fila", "gravando", "analisando"):
        _set(fid, until=time.time(), note="Você parou o acompanhamento.")


def _record(url: str, secs: int, dest: Path) -> Path:
    """Grava os próximos `secs` segundos da live (a partir de agora). Se a conexão cair no meio, continua de onde parou
    e junta os pedaços."""
    dest.mkdir(parents=True, exist_ok=True)
    parts, got, t_end = [], 0.0, time.time() + secs + 120
    while got < secs * 0.9 and time.time() < t_end:
        k = len(parts)
        need = int(secs - got)
        try:
            video.ytdlp(["--no-playlist", "--no-live-from-start", "-f", "best[height<=720]/best",
                         "--downloader", "ffmpeg", "--downloader-args", f"ffmpeg:-t {need}",
                         "--no-part", "-o", str(dest / f"p{k}.%(ext)s")], url, timeout=need + 300)
        except Exception as e:
            if not parts:
                raise
            log.warning("Ao vivo: gravação parou (%s), seguindo com o que tem", str(e)[-200:])
            break
        f = next(iter(sorted(dest.glob(f"p{k}.*"))), None)
        if not f or f.stat().st_size < 20_000:
            break
        d = video.probe_duration(f)
        log.info("Ao vivo: pedaço %s com %.0fs e %.1f MB", k, d, f.stat().st_size / 1e6)
        parts.append(f); got += d
        if d < 5:
            time.sleep(5)
    if not parts:
        raise RuntimeError("A gravação da live não gerou vídeo")
    if len(parts) == 1:
        return parts[0]
    lst = dest / "lista.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    out = dest / "bloco.mp4"
    video._run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)], timeout=600)
    return out


def _run(fid, left_fn):
    st = get(fid)
    try:
        info = video.probe_url(st["url"])
        if not info.get("live"):
            raise RuntimeError("Esse link não está ao vivo agora. Pra vídeo que já acabou, use o corte normal.")
        _set(fid, title=info.get("title") or "Live", status="gravando")
        n = 0
        while time.time() < get(fid)["until"]:
            if left_fn(st["user_id"]) <= 0:
                _set(fid, note="Seus cortes acabaram, então parei de acompanhar a live.")
                break
            secs = int(min(CHUNK, max(60, get(fid)["until"] - time.time())))
            t0 = time.strftime("%H:%M", time.localtime(time.time() - 3 * 3600))  # horário de Brasília
            _set(fid, status="gravando")
            try:
                blk = _record(st["url"], secs, video.SCRATCH / "aovivo" / fid / f"b{n}")
            except Exception as e:
                try:
                    still = video.probe_url(st["url"]).get("live")
                except Exception:
                    still = True
                if not still:
                    _set(fid, note="A live acabou.")
                    break
                log.error("Ao vivo %s: bloco %d falhou: %s", fid, n, str(e)[-400:])
                time.sleep(20)
                continue
            n += 1
            auto = {**st["auto"], "n": int(st["auto"].get("n_bloco", 2))}
            m = moments.create(st["user_id"], upload=blk, auto=auto, name=f"{get(fid)['title'][:80]} · ao vivo {t0}")
            with _lock:
                STATE[fid]["videos"].append(m["id"])
                STATE[fid]["blocks"] = n
                _save(fid)
            log.info("Ao vivo %s: bloco %d gravado (%ds), análise %s", fid, n, secs, m["id"])
        _set(fid, status="pronto")
    except Exception as e:
        log.error("Ao vivo %s falhou: %s", fid, str(e)[-600:])
        _set(fid, status="erro", error=video.friendly_error(str(e)) if "código" in str(e) else str(e))
    finally:
        shutil.rmtree(video.SCRATCH / "aovivo" / fid, ignore_errors=True)


def _find_live(urls):
    """Primeira live no ar da lista (aceita /@canal/live) ou, se nenhuma, a primeira live de GTA achada na busca."""
    for u in urls:
        try:
            info = video.probe_url(u)
            if info.get("live"):
                return u, info
        except Exception as e:
            log.info("Demo ao vivo: %s não serviu (%s)", u, str(e)[-160:])
    try:
        out = video.ytdlp(["--flat-playlist", "-J", "--no-warnings"], "ytsearch20:GTA RP ao vivo", timeout=60)
        for e in json.loads(out).get("entries") or []:
            if e.get("live_status") == "is_live" or e.get("is_live"):
                u = f"https://www.youtube.com/watch?v={e['id']}"
                info = video.probe_url(u)
                if info.get("live"):
                    return u, info
    except Exception as e:
        log.info("Demo ao vivo: busca falhou (%s)", str(e)[-160:])
    return None, None


def demo():
    """Teste ponta a ponta do CORTAR AO VIVO numa live de GTA no ar: grava, a IA escolhe o melhor momento e monta o corte.
    O resultado fica em /files/demo-aovivo/corte.mp4 (só pra conferência)."""
    urls = [u.strip() for u in os.getenv("LIVE_DEMO_URLS", "").split(",") if u.strip()]
    if not urls:
        return
    t0 = time.time()
    try:
        url, info = _find_live(urls)
        if not url:
            log.error("Demo ao vivo: nenhuma live no ar")
            return
        secs = int(os.getenv("LIVE_DEMO_SECONDS", "300"))
        work = video.SCRATCH / "demo-aovivo"
        shutil.rmtree(work, ignore_errors=True)
        blk = _record(url, secs, work)
        log.info("Demo ao vivo: gravou %.0fs de '%s' (%.0f MB) | %s", video.probe_duration(blk), info.get("title", "")[:60], blk.stat().st_size / 1e6, url)
        aid = "demoaovivo"
        (moments.DIR / aid).mkdir(parents=True, exist_ok=True)
        moments.STATE[aid] = {"id": aid, "user_id": 0, "status": "ouvindo", "moments": []}
        audio = work / "audio.mp3"
        video._run(["ffmpeg", "-y", "-i", str(blk), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", str(audio)])
        dur = video.probe_duration(audio)
        segs = moments._transcribe(audio, work, aid, dur)
        peaks = moments._loud_peaks(audio, dur)
        ms = moments._pick(segs, peaks, dur, aid) or moments._fallback(segs, peaks, dur)
        if not ms and dur > 40:  # sem fala nem pico: pega o trecho do meio, só pra entregar o teste
            ms = [{"start": round(dur / 2 - 20), "end": round(dur / 2 + 20), "title": "MOMENTO DA LIVE AO VIVO", "score": 50}]
        ms = moments._rank(ms, segs)
        if not ms:
            raise RuntimeError("a IA não achou momento")
        m = ms[0]
        out = video.JOBS_DIR / "demo-aovivo"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        trim = (float(m["start"]), float(m["end"]))
        words = video.transcribe(blk, out, trim)
        video.render(blk, out, {"template": "capa-praia", "caption": "pesada-karaoke", "title": m["title"], "wm_text": "@gta6clips",
                                "corner": "auto", "cover": False}, trim, words)
        log.info("Demo ao vivo OK em %.0fs: '%s' (%s-%ss, nota %s) -> /files/demo-aovivo/corte.mp4 | live: %s",
                 time.time() - t0, m["title"], m["start"], m["end"], m.get("score"), url)
    except Exception as e:
        log.error("Demo ao vivo FALHOU: %s", str(e)[-800:])


threading.Thread(target=lambda: (time.sleep(90), demo()), daemon=True).start()
