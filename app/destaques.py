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


async def _one(c: httpx.AsyncClient, u: str) -> dict | None:
    full = u
    try:
        if "vt.tiktok.com" in u or "vm.tiktok.com" in u:
            r = await c.get(u, headers=UA, follow_redirects=True)
            full = str(r.url).split("?")[0]
        r = await c.get("https://www.tiktok.com/oembed", params={"url": full}, headers=UA)
        j = r.json()
        return {"url": full, "short": u, "title": (j.get("title") or "")[:140], "author": j.get("author_name") or "",
                "handle": (j.get("author_unique_id") or "").lstrip("@"), "thumb": j.get("thumbnail_url") or ""}
    except Exception as e:
        log.warning("Destaque %s: %s", u, e)
        return {"url": full, "short": u, "title": "", "author": "", "handle": "", "thumb": ""}


async def items() -> list[dict]:
    if time.time() - _cache["at"] < 3 * 3600 and _cache["items"]:
        return _cache["items"]
    async with httpx.AsyncClient(timeout=12) as c:
        out = [x for x in [await _one(c, u) for u in urls()] if x]
    if any(x["thumb"] for x in out):
        _cache.update(at=time.time(), items=out)
    return out
