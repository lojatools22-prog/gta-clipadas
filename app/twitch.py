"""Integração com a API oficial da Twitch (Helix). Sem credenciais, roda em modo demo."""
import os, time, json, datetime as dt
from pathlib import Path
import httpx

CLIENT_ID = os.getenv("TWITCH_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "")
HELIX = "https://api.twitch.tv/helix"
GTA_NAMES = ["Grand Theft Auto VI", "Grand Theft Auto V"]

STREAMERS_FILE = Path(__file__).parent / "streamers.json"

_token = {"value": None, "exp": 0}
_cache: dict = {}


def demo_mode() -> bool:
    return not (CLIENT_ID and CLIENT_SECRET)


def featured_logins() -> list[dict]:
    return json.loads(STREAMERS_FILE.read_text(encoding="utf-8"))


async def _get_token(client: httpx.AsyncClient) -> str:
    if _token["value"] and time.time() < _token["exp"] - 60:
        return _token["value"]
    r = await client.post("https://id.twitch.tv/oauth2/token", params={
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "grant_type": "client_credentials"})
    r.raise_for_status()
    d = r.json()
    _token.update(value=d["access_token"], exp=time.time() + d["expires_in"])
    return _token["value"]


async def helix(path: str, params, ttl: int = 60):
    key = path + json.dumps(params, sort_keys=True, default=str)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    async with httpx.AsyncClient(timeout=15) as c:
        tok = await _get_token(c)
        r = await c.get(f"{HELIX}/{path}", params=params,
                        headers={"Client-Id": CLIENT_ID, "Authorization": f"Bearer {tok}"})
        if r.status_code == 404:
            data = {"data": []}
        else:
            r.raise_for_status()
            data = r.json()
    _cache[key] = (time.time(), data)
    return data


async def gta_games() -> list[dict]:
    d = await helix("games", [("name", n) for n in GTA_NAMES], ttl=3600)
    return d.get("data", [])


async def users_by_login(logins: list[str]) -> dict:
    out = {}
    for i in range(0, len(logins), 100):
        d = await helix("users", [("login", l) for l in logins[i:i + 100]], ttl=3600)
        for u in d.get("data", []):
            out[u["login"].lower()] = u
    return out


def _thumb(url: str, w=640, h=360) -> str:
    return (url or "").replace("{width}", str(w)).replace("{height}", str(h)).replace("%{width}", str(w)).replace("%{height}", str(h))


async def featured() -> list[dict]:
    if demo_mode():
        return DEMO["featured"]
    entries = featured_logins()
    users = await users_by_login([e["login"] for e in entries])
    ids = [u["id"] for u in users.values()]
    live = {}
    for i in range(0, len(ids), 100):
        d = await helix("streams", [("user_id", x) for x in ids[i:i + 100]], ttl=45)
        for s in d.get("data", []):
            live[s["user_id"]] = s
    out = []
    for e in entries:
        u = users.get(e["login"].lower())
        if not u:
            continue
        s = live.get(u["id"])
        out.append({
            "id": u["id"], "login": u["login"], "name": u["display_name"], "avatar": u["profile_image_url"],
            "tag": e.get("tag", ""), "live": bool(s),
            "title": s["title"] if s else "", "game": s["game_name"] if s else "",
            "viewers": s["viewer_count"] if s else 0,
            "thumb": _thumb(s["thumbnail_url"]) if s else "",
            "is_gta": bool(s and "grand theft auto" in s["game_name"].lower()),
        })
    out.sort(key=lambda x: (not x["live"], not x["is_gta"], -x["viewers"]))
    return out


async def live_gta(lang: str = "pt") -> list[dict]:
    if demo_mode():
        return DEMO["live"]
    games = await gta_games()
    out = []
    for g in games:
        d = await helix("streams", {"game_id": g["id"], "language": lang, "first": 40}, ttl=60)
        for s in d.get("data", []):
            out.append({"login": s["user_login"], "name": s["user_name"], "title": s["title"],
                        "viewers": s["viewer_count"], "game": g["name"], "thumb": _thumb(s["thumbnail_url"]),
                        "started_at": s["started_at"]})
    out.sort(key=lambda x: -x["viewers"])
    return out[:60]


async def user_id(login: str) -> str | None:
    u = (await users_by_login([login])).get(login.lower())
    return u["id"] if u else None


async def vods(login: str) -> list[dict]:
    if demo_mode():
        return DEMO["vods"]
    uid = await user_id(login)
    if not uid:
        return []
    d = await helix("videos", {"user_id": uid, "type": "archive", "first": 12}, ttl=120)
    return [{"id": v["id"], "title": v["title"], "url": v["url"], "duration": v["duration"],
             "created_at": v["created_at"], "views": v["view_count"],
             "thumb": _thumb(v["thumbnail_url"], 320, 180)} for v in d.get("data", [])]


async def schedule(login: str) -> list[dict]:
    if demo_mode():
        return DEMO["schedule"]
    uid = await user_id(login)
    if not uid:
        return []
    try:
        d = await helix("schedule", {"broadcaster_id": uid, "first": 5}, ttl=600)
    except httpx.HTTPStatusError:
        return []
    segs = (d.get("data") or {}).get("segments") or []
    return [{"title": s.get("title"), "start": s.get("start_time"),
             "game": (s.get("category") or {}).get("name", "")} for s in segs]


def _clip(c) -> dict:
    return {"id": c["id"], "url": c["url"], "title": c["title"], "views": c["view_count"],
            "duration": c["duration"], "creator": c["broadcaster_name"], "thumb": c["thumbnail_url"],
            "created_at": c["created_at"], "vod_id": c.get("video_id"), "vod_offset": c.get("vod_offset")}


async def clips(login: str | None = None, hours: int = 48) -> list[dict]:
    """Clipes criados pela comunidade = melhores momentos já marcados pelos viewers."""
    if demo_mode():
        return DEMO["clips"]
    started = (dt.datetime.utcnow() - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    if login:
        uid = await user_id(login)
        if not uid:
            return []
        d = await helix("clips", {"broadcaster_id": uid, "started_at": started, "first": 30}, ttl=300)
        out = [_clip(c) for c in d.get("data", [])]
    else:
        for g in await gta_games():
            d = await helix("clips", {"game_id": g["id"], "started_at": started, "first": 100}, ttl=300)
            out += [_clip(c) for c in d.get("data", []) if (c.get("language") or "").startswith("pt")]
    out.sort(key=lambda x: -x["views"])
    return out[:40]


_PH = "https://placehold.co/640x360/1a0b2e/ff5fa2?text="
DEMO = {
    "featured": [
        {"id": "1", "login": "alanzoka", "name": "alanzoka", "avatar": _PH + "A", "tag": "Variedades", "live": True,
         "title": "GTA 6 DIA 1 — ZERANDO TUDO", "game": "Grand Theft Auto VI", "viewers": 98213,
         "thumb": _PH + "LIVE+GTA+VI", "is_gta": True},
        {"id": "2", "login": "loud_coringa", "name": "LOUD_Coringa", "avatar": _PH + "C", "tag": "GTA RP", "live": True,
         "title": "PRIMEIRA VEZ NO GTA 6", "game": "Grand Theft Auto VI", "viewers": 64110,
         "thumb": _PH + "LIVE+GTA+VI", "is_gta": True},
        {"id": "3", "login": "paulinholokobr", "name": "PaulinhoLOKObr", "avatar": _PH + "P", "tag": "GTA RP", "live": False,
         "title": "", "game": "", "viewers": 0, "thumb": "", "is_gta": False},
    ],
    "live": [
        {"login": "alanzoka", "name": "alanzoka", "title": "GTA 6 DIA 1 — ZERANDO TUDO", "viewers": 98213,
         "game": "Grand Theft Auto VI", "thumb": _PH + "LIVE+GTA+VI", "started_at": "2026-11-19T03:00:00Z"},
    ],
    "vods": [
        {"id": "000000001", "title": "GTA 6 DIA 1 (modo demo)", "url": "https://www.twitch.tv/videos/000000001",
         "duration": "6h12m3s", "created_at": "2026-11-19T03:00:00Z", "views": 120000, "thumb": _PH + "VOD"},
    ],
    "schedule": [{"title": "GTA 6 — lançamento", "start": "2026-11-19T03:00:00Z", "game": "Grand Theft Auto VI"}],
    "clips": [
        {"id": "demo", "url": "https://clips.twitch.tv/demo", "title": "ele caiu da ponte KKKKK (demo)", "views": 45122,
         "duration": 28, "creator": "alanzoka", "thumb": _PH + "CLIP", "created_at": "2026-11-19T04:10:00Z",
         "vod_id": "000000001", "vod_offset": 4200},
    ],
}
