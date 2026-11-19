"""Virais do momento: busca sozinho os vídeos de GTA 6 mais vistos de hoje e da semana no YouTube (Shorts e cortes).
Atualiza a cada 2 h em segundo plano e guarda em disco, pra aba Virais nunca ficar vazia."""
import json, logging, re, threading, time
from urllib.parse import quote_plus
from .video import DATA

log = logging.getLogger("uvicorn.error")
FILE = DATA / "trending.json"
EVERY = 2 * 3600
# sp: CAMSAggC = mais vistos de hoje · CAMSAggD = mais vistos da semana
QUERIES = [("gta 6", "CAMSAggC"), ("gta 6 corte live", "CAMSAggD"), ("gta 6 momentos engraçados", "CAMSAggD"),
           ("gta 6 shorts", "CAMSAggD"), ("gta 6 gameplay pt-br", "CAMSAggC"), ("gta 6 clip", "CAMSAggD")]
BLOCK = re.compile(r"trailer oficial|official trailer|download|apk|mobile grátis|free download|hack|mod menu", re.I)
_state = {"busy": False, "at": 0}


def _load() -> dict:
    try:
        return json.loads(FILE.read_text())
    except Exception:
        return {"at": 0, "items": []}


def _search(q: str, sp: str) -> list[dict]:
    from . import video
    url = f"https://www.youtube.com/results?search_query={quote_plus(q)}&sp={sp}"
    out = video.ytdlp(["--flat-playlist", "--playlist-end", "25", "-J"], url, 120)
    data = json.loads(out[out.index("{"):])
    res = []
    for e in data.get("entries") or []:
        vid, title = e.get("id"), (e.get("title") or "").strip()
        if not vid or len(vid) != 11 or not re.search(r"gta|grand theft", title, re.I) or BLOCK.search(title):
            continue
        dur = e.get("duration") or 0
        res.append({"id": vid, "title": title[:140], "views": int(e.get("view_count") or 0), "duration": dur,
                    "channel": e.get("channel") or e.get("uploader") or "", "short": bool(dur and dur <= 70) or "/shorts/" in (e.get("url") or ""),
                    "url": f"https://www.youtube.com/{'shorts/' if (dur and dur <= 70) else 'watch?v='}{vid}",
                    "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", "q": q})
    return res


def refresh():
    if _state["busy"]:
        return
    _state["busy"] = True
    try:
        seen, items, errs = set(), [], []
        for q, sp in QUERIES:
            try:
                for it in _search(q, sp):
                    if it["id"] not in seen:
                        seen.add(it["id"])
                        items.append(it)
            except Exception as e:
                errs.append(f"{q}: {str(e)[:120]}")
        items.sort(key=lambda x: -x["views"])
        if items:
            FILE.write_text(json.dumps({"at": time.time(), "items": items[:36]}, ensure_ascii=False))
            log.info("Virais: %d vídeos atualizados (%d buscas com erro)", len(items), len(errs))
        else:
            log.warning("Virais: nenhuma busca deu certo: %s", " | ".join(errs)[:500])
        _state["at"] = time.time()
        _state["errs"] = errs
    finally:
        _state["busy"] = False


def items() -> dict:
    d = _load()
    stale = time.time() - d.get("at", 0) > EVERY
    if stale and not _state["busy"] and time.time() - _state["at"] > 600:
        threading.Thread(target=refresh, daemon=True).start()
    return {"at": d.get("at", 0), "items": d.get("items", []), "atualizando": _state["busy"]}


def status() -> dict:
    d = _load()
    return {"at": d.get("at", 0), "n": len(d.get("items", [])), "busy": _state["busy"], "errs": _state.get("errs", [])}


def start():
    def loop():
        time.sleep(30)
        while True:
            try:
                if time.time() - _load().get("at", 0) > EVERY:
                    refresh()
            except Exception as e:
                log.warning("Virais: %s", e)
            time.sleep(600)
    threading.Thread(target=loop, daemon=True).start()
