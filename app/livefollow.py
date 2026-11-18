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
    """Grava os próximos `secs` segundos da live (a partir de agora)."""
    dest.mkdir(parents=True, exist_ok=True)
    video.ytdlp(["--no-playlist", "--no-live-from-start", "-f", "best[height<=720]/best",
                 "--downloader", "ffmpeg", "--downloader-args", f"ffmpeg:-t {secs}",
                 "--no-part", "-o", str(dest / "bloco.%(ext)s")], url, timeout=secs + 300)
    files = sorted(dest.glob("bloco.*"))
    if not files or files[0].stat().st_size < 50_000:
        raise RuntimeError("A gravação da live não gerou vídeo")
    return files[0]


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
