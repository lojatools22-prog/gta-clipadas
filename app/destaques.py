"""Destaques do topo da aba Virais: vídeos do TikTok escolhidos pela equipe (link, capa, título, autor)."""
import json, logging, time
from pathlib import Path
import httpx
from .video import DATA

log = logging.getLogger("uvicorn.error")
FILE = DATA / "destaques.json"
SEED = ["https://vt.tiktok.com/ZSpc41C7g/", "https://vt.tiktok.com/ZSpc4d6Bc/", "https://vt.tiktok.com/ZSpc4ABkb/",
        "https://vt.tiktok.com/ZSpc4FpkV/", "https://vt.tiktok.com/ZSpc46AY9/", "https://vt.tiktok.com/ZSpc4yRpx/"]
UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"}
_cache = {"at": 0, "items": []}


def urls() -> list[str]:
    try:
        return json.loads(FILE.read_text())
    except Exception:
        return list(SEED)


def save(lst: list[str]):
    FILE.write_text(json.dumps([u.strip() for u in lst if u.strip().startswith("http")][:30]))
    _cache["at"] = 0


THUMBS = DATA / "destaques"


def _px():
    import os
    from . import video
    px = os.getenv("YTDLP_PROXY", "")
    return video._geo_proxy(px) if px else None


async def _one(c: httpx.AsyncClient, u: str) -> dict | None:
    import re, asyncio
    from . import video
    full = u
    out = {"url": u, "short": u, "title": "", "author": "", "handle": "", "thumb": ""}
    try:
        if "vt.tiktok.com" in u or "vm.tiktok.com" in u:
            r = await c.get(u, headers=UA, follow_redirects=True)
            full = str(r.url).split("?")[0]
        out["url"] = full
        m = re.search(r"tiktok\.com/@([^/]+)/video/(\d+)", full)
        if m:
            out["handle"] = m.group(1)
        vid = m.group(2) if m else re.sub(r"\W", "", u)[-16:]
        THUMBS.mkdir(parents=True, exist_ok=True)
        jpg = THUMBS / f"{vid}.jpg"
        meta = THUMBS / f"{vid}.json"
        old = json.loads(meta.read_text()) if meta.exists() else None
        if old and (old.get("title") or old.get("tried")):
            out.update(old)
        else:
            j = {}
            for px in (None, _px()):  # direto; se o TikTok recusar, pelo proxy
                try:
                    async with httpx.AsyncClient(timeout=15, proxy=px) as c2:
                        r = await c2.get("https://www.tiktok.com/oembed", params={"url": full}, headers=UA)
                        j = r.json()
                    if j.get("thumbnail_url"):
                        async with httpx.AsyncClient(timeout=20, proxy=px) as c2:
                            img = await c2.get(j["thumbnail_url"], headers=UA)
                        if img.status_code == 200 and len(img.content) > 2000:
                            jpg.write_bytes(img.content)
                        break
                except Exception as e:
                    log.warning("Destaque %s (proxy=%s): %s", u, bool(px), e)
            yt = ""
            if not jpg.exists() or not j.get("title"):  # o yt-dlp pega a capa e a legenda do vídeo
                for _ in range(2):
                    try:
                        yt = await asyncio.to_thread(video.ytdlp, ["--skip-download", "--write-thumbnail", "--convert-thumbnails", "jpg",
                                                                   "--print", "%(title)s|||%(uploader)s", "--no-simulate",
                                                                   "-o", str(THUMBS / vid)], full, 90)
                        if jpg.exists():
                            break
                    except Exception as e:
                        log.warning("Destaque %s (yt-dlp): %s", u, e)
            t2, _, a2 = (yt or "").strip().splitlines()[-1].partition("|||") if (yt or "").strip() else ("", "", "")
            info = {"title": (j.get("title") or t2 or "")[:140], "author": j.get("author_name") or a2 or "", "tried": True}
            if jpg.exists():
                meta.write_text(json.dumps(info, ensure_ascii=False))
            out.update(info)
        if jpg.exists():
            out["thumb"] = f"/api/destaques/capa/{vid}.jpg"
    except Exception as e:
        log.warning("Destaque %s: %s", u, e)
    return out


async def items() -> list[dict]:
    if time.time() - _cache["at"] < 3 * 3600 and _cache["items"]:
        return _cache["items"]
    async with httpx.AsyncClient(timeout=12) as c:
        out = [x for x in [await _one(c, u) for u in urls()] if x]
    if all(x["thumb"] for x in out):
        _cache.update(at=time.time(), items=out)
    elif any(x["thumb"] for x in out):
        _cache.update(at=time.time() - 3 * 3600 + 600, items=out)  # tenta de novo as que faltaram em 10 min
    return out
