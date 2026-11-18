import json, logging, os, shutil, tempfile, time
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx

from . import twitch, video, templates, accounts, plans, payments, notify, moments

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
app = FastAPI(title="GTA Clipadas")
STATIC = Path(__file__).parent / "static"


def secs(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = 0.0
    for p in str(v).strip().split(":"):
        t = t * 60 + float(p or 0)
    return t


async def safe(coro):
    try:
        return await coro
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Twitch indisponível: {e}")


def _codes(var):
    return {c.strip().upper() for c in os.getenv(var, "").split(",") if c.strip()}


CODES = {p: _codes(f"CODES_{p.upper()}") for p in plans.PAID}
CODES["pro"] |= _codes("PRO_CODES")
CODES["passe"] |= _codes("PASSE_CODES")


def bad(e: Exception, code=400):
    raise HTTPException(code, str(e))


def token_of(request: Request):
    h = request.headers.get("authorization", "")
    return h[7:] if h.lower().startswith("bearer ") else None


def current_user(request: Request, required=True):
    u = accounts.user_by_token(token_of(request))
    if required and not u:
        raise HTTPException(401, f"Faça seu cadastro grátis pra gerar cortes (você ganha {plans.PLANS['gratis']['quota']} cortes grátis).")
    return u


ADMIN_SESSIONS: dict[str, float] = {}
_ADMIN_FAILS: dict[str, list] = {}


def admin_user(request: Request):
    tok = token_of(request)
    if tok and ADMIN_SESSIONS.get(tok, 0) > time.time():
        return {"admin": True, "email": os.getenv("ADMIN_USER", "admin")}
    u = current_user(request, required=False)
    if not u or not accounts.is_admin(u):
        raise HTTPException(401 if not u else 403, "Entre com o usuário e a senha do painel")
    return u


class AdminLoginIn(BaseModel):
    user: str
    password: str


@app.post("/api/admin/login")
def admin_login(b: AdminLoginIn, request: Request):
    import hmac as _h, secrets as _s
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    now = time.time()
    fails = [t for t in _ADMIN_FAILS.get(ip, []) if t > now - 900]
    if len(fails) >= 6:
        raise HTTPException(429, "Muitas tentativas. Espere 15 minutos.")
    au, ap = os.getenv("ADMIN_USER", ""), os.getenv("ADMIN_PASSWORD", "")
    if not au or not ap:
        raise HTTPException(503, "O acesso do painel ainda não foi configurado no servidor.")
    if not (_h.compare_digest(b.user.strip().lower(), au.lower()) and _h.compare_digest(b.password, ap)):
        _ADMIN_FAILS[ip] = fails + [now]
        time.sleep(1)
        raise HTTPException(401, "Usuário ou senha errados")
    tok = "adm_" + _s.token_urlsafe(32)
    ADMIN_SESSIONS[tok] = now + 12 * 3600
    return {"token": tok, "user": au}


# ---------------- básico ----------------
@app.get("/api/health")
def health():
    return {"ok": True, "fila": video.queue_size()}


@app.get("/api/status")
def status():
    return {"demo": twitch.demo_mode(), "captions": bool(video.GROQ_KEY or video.OPENAI_KEY),
            "corners": video.CAM_CORNERS, "max_len": video.MAX_LEN, "free_clips": plans.PLANS["gratis"]["quota"],
            "plans": plans.public(), "checkout": payments.ready(), "provider": payments.PROVIDER, "sms": accounts.sms_on(),
            "max_upload_mb": max(p["max_mb"] for p in plans.PLANS.values()), "mb_base": plans.MB_BASE, "mb_pro": plans.MB_PRO}


@app.get("/api/catalog")
def catalog():
    return templates.public_catalog()


# ---------------- contas ----------------
class SignupIn(BaseModel):
    name: str
    email: str
    phone: str
    password: str
    device: str = ""


class LoginIn(BaseModel):
    email: str
    password: str
    device: str = ""


class CodeCheckIn(BaseModel):
    challenge: str
    code: str


class ForgotIn(BaseModel):
    email: str


class ResetIn(BaseModel):
    challenge: str
    code: str
    password: str
    device: str = ""


def _logged(tok: str):
    return {"step": "done", "token": tok, "user": accounts.public(accounts.user_by_token(tok))}


def _wrap(fn, *a):
    try:
        return fn(*a)
    except accounts.AuthError as e:
        bad(e)
    except RuntimeError as e:  # falha no envio de SMS/e-mail
        bad(e, 503)


@app.post("/api/auth/signup")
def signup(b: SignupIn):
    r = _wrap(accounts.signup, b.name, b.email, b.phone, b.password, b.device)
    return _logged(r["token"]) if r["step"] == "done" else r


@app.post("/api/auth/verify-phone")
def verify_phone(b: CodeCheckIn):
    return _logged(_wrap(accounts.verify_phone, b.challenge, b.code))


@app.post("/api/auth/login")
def login(b: LoginIn):
    r = _wrap(accounts.login, b.email, b.password, b.device)
    return _logged(r["token"]) if r["step"] == "done" else r


@app.post("/api/auth/verify-login")
def verify_login(b: CodeCheckIn):
    return _logged(_wrap(accounts.verify_login, b.challenge, b.code))


class ResendIn(BaseModel):
    challenge: str


@app.post("/api/auth/resend")
def resend(b: ResendIn):
    return _wrap(accounts.resend, b.challenge)


@app.post("/api/auth/forgot")
def forgot(b: ForgotIn):
    return _wrap(accounts.forgot, b.email)


@app.post("/api/auth/reset")
def reset(b: ResetIn):
    return _logged(_wrap(accounts.reset, b.challenge, b.code, b.password, b.device))


@app.post("/api/auth/logout")
def logout(request: Request):
    if token_of(request):
        accounts.logout(token_of(request))
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    return accounts.public(current_user(request))


# ---------------- planos e pagamento ----------------
class CodeIn(BaseModel):
    code: str


@app.post("/api/pro/redeem")
def redeem(b: CodeIn, request: Request):
    return _wrap(accounts.redeem, current_user(request), b.code, CODES)


class CheckoutIn(BaseModel):
    plan: str
    cpf: str = ""


@app.post("/api/pay/checkout")
def checkout(b: CheckoutIn, request: Request):
    u = current_user(request)
    if b.plan not in plans.PAID:
        raise HTTPException(400, "Plano inválido")
    if not payments.ready():
        raise HTTPException(503, "O pagamento online abre em breve. Fale com a gente no WhatsApp pra assinar.")
    try:
        if payments.PROVIDER == "asaas":
            cid = u["asaas_customer"]
            if not cid:
                if not b.cpf:
                    raise HTTPException(428, "Informe seu CPF pra emitir a cobrança")
                try:
                    cid = payments.asaas_customer(u, b.cpf)
                except ValueError as e:
                    bad(e)
                accounts.set_asaas_customer(u["id"], cid)
            return {"url": payments.asaas_checkout(u, b.plan, cid)}
        return {"url": payments.create_checkout(u, b.plan)}
    except RuntimeError as e:
        bad(e, 502)


@app.post("/api/pay/asaas")
async def asaas_webhook(request: Request):
    import hmac as _h
    if not payments.ASAAS_TOKEN or not _h.compare_digest(request.headers.get("asaas-access-token", ""), payments.ASAAS_TOKEN):
        log.warning("Webhook Asaas com token inválido")
        raise HTTPException(401, "token inválido")
    body = await request.json()
    ev, pay = body.get("event", ""), body.get("payment") or {}
    if ev not in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED") or not pay.get("id"):
        if ev in ("PAYMENT_REFUNDED", "PAYMENT_CHARGEBACK_REQUESTED"):
            log.warning("Asaas %s no pagamento %s: confira no painel", ev, pay.get("id"))
        return {"ok": True}
    try:
        p = payments.asaas_payment(pay["id"])  # confere direto na API
    except Exception as e:
        log.error("Não consegui consultar o pagamento Asaas %s: %s", pay.get("id"), e)
        raise HTTPException(502, "tente de novo")
    _asaas_grant(p)
    return {"ok": True}


def _asaas_grant(p: dict) -> bool:
    """Libera o plano de um pagamento Asaas já conferido na API. Pode ser chamada mais de uma vez."""
    if p.get("status") not in ("RECEIVED", "CONFIRMED", "RECEIVED_IN_CASH"):
        return False
    try:
        uid, plan = (p.get("externalReference") or "").split(":")
        uid = int(uid)
    except ValueError:
        return False
    if plan not in plans.PAID:
        return False
    amount = float(p.get("value") or 0)
    if accounts.payment_upsert("asaas:" + p["id"], uid, plan, amount, "approved", "asaas"):
        if amount + 0.01 < plans.PLANS[plan]["price"]:
            log.error("Pagamento Asaas %s com valor menor que o plano (%s)", p["id"], amount)
            return False
        accounts.activate(uid, plan, "asaas")
        log.info("Plano %s liberado para usuário %s (Asaas %s)", plan, uid, p["id"])
    return True


class PayIn(BaseModel):
    plan: str
    method: str = "pix"
    cpf: str = ""


@app.post("/api/pay/start")
def pay_start(b: PayIn, request: Request):
    """Checkout próprio: cria a cobrança e devolve o Pix para pagar na nossa tela."""
    u = current_user(request)
    if b.plan not in plans.PAID:
        raise HTTPException(400, "Plano inválido")
    if not payments.ready() or payments.PROVIDER != "asaas":
        raise HTTPException(503, "O pagamento online abre em breve. Fale com a gente no WhatsApp pra assinar.")
    cid = u["asaas_customer"]
    try:
        if not cid:
            if not b.cpf:
                raise HTTPException(428, "Informe seu CPF pra emitir a cobrança")
            try:
                cid = payments.asaas_customer(u, b.cpf)
            except ValueError as e:
                bad(e)
            accounts.set_asaas_customer(u["id"], cid)
        r = payments.asaas_charge(u, b.plan, cid, "pix" if b.method == "pix" else "card")
    except RuntimeError as e:
        bad(e, 502)
    accounts.payment_upsert("asaas:" + r["id"], u["id"], b.plan, float(r["value"]), "pending", "asaas")
    return r


@app.get("/api/pay/status/{pid}")
def pay_status(pid: str, request: Request):
    u = current_user(request)
    if not pid.startswith("pay_") or len(pid) > 40:
        raise HTTPException(400, "Pagamento inválido")
    try:
        p = payments.asaas_payment(pid)
    except Exception:
        return {"paid": False}
    if not (p.get("externalReference") or "").startswith(f"{u['id']}:"):
        raise HTTPException(404, "Pagamento não encontrado")
    paid = _asaas_grant(p)
    return {"paid": paid, "status": p.get("status"), "user": accounts.public(accounts._user(u["id"])) if paid else None}


@app.post("/api/pay/webhook")
async def pay_webhook(request: Request):
    body = await request.json() if (request.headers.get("content-type") or "").startswith("application/json") else {}
    pid = str((body.get("data") or {}).get("id") or request.query_params.get("data.id") or request.query_params.get("id") or "")
    kind = body.get("type") or request.query_params.get("type") or request.query_params.get("topic")
    if kind != "payment" or not pid:
        return {"ok": True}
    if not payments.valid_signature({k.lower(): v for k, v in request.headers.items()}, pid):
        log.warning("Webhook com assinatura inválida: %s", pid)
        raise HTTPException(401, "assinatura inválida")
    try:
        p = payments.fetch_payment(pid)
    except Exception as e:
        log.error("Não consegui consultar o pagamento %s: %s", pid, e)
        raise HTTPException(502, "tente de novo")
    try:
        uid, plan = p.get("external_reference", "").split(":")
        uid = int(uid)
    except ValueError:
        return {"ok": True}
    if plan not in plans.PAID:
        return {"ok": True}
    amount = float(p.get("transaction_amount") or 0)
    if accounts.payment_upsert(pid, uid, plan, amount, p.get("status", "")):
        if amount + 0.01 < plans.PLANS[plan]["price"]:
            log.error("Pagamento %s com valor menor que o plano (%s)", pid, amount)
        else:
            accounts.activate(uid, plan, "mercadopago")
            log.info("Plano %s liberado para usuário %s (pagamento %s)", plan, uid, pid)
    return {"ok": True}


# ---------------- twitch ----------------
@app.get("/api/probe")
async def probe(url: str, request: Request):
    current_user(request)
    if not url.startswith(("http://", "https://")) or len(url) > 500:
        raise HTTPException(400, "Link inválido")
    import asyncio
    try:
        return await asyncio.to_thread(video.probe_url, url)
    except Exception as e:
        log.warning("probe falhou %s: %s", url, str(e)[:300])
        raise HTTPException(422, "Não conseguimos ler esse link. Confira se o vídeo é público.")


@app.get("/api/live")
async def live(lang: str = "pt"):
    return await safe(twitch.live_gta(lang))


@app.get("/api/featured")
async def featured():
    return await safe(twitch.featured())


@app.get("/api/vods/{login}")
async def vods(login: str):
    return await safe(twitch.vods(login))


@app.get("/api/schedule/{login}")
async def schedule(login: str):
    return await safe(twitch.schedule(login))


@app.get("/api/clips")
async def clips(login: str | None = None, hours: int = 48):
    return await safe(twitch.clips(login, min(max(hours, 1), 24 * 30)))


# ---------------- cortes ----------------
class JobIn(BaseModel):
    url: str = ""
    analysis: str = ""
    start: str | float | None = None
    end: str | float | None = None
    segments: list = []
    template: str = "basico-legenda"
    caption: str = "pesada-karaoke"
    caption_color: str = "auto"
    watermark: bool = True
    logo: bool = True
    cover: bool = True
    corner: str = "top-left"
    cam_size: float = 0.30
    title: str = ""
    captions: bool = True
    review: bool = False


def _opts(d: dict, P: dict) -> dict:
    d = dict(d)
    d["start"], d["end"] = secs(d.get("start")), secs(d.get("end"))
    if (d["start"] is None) != (d["end"] is None):
        raise HTTPException(400, "Preencha início e fim, ou deixe os dois vazios")
    d["cam_size"] = min(max(float(d.get("cam_size") or 0.3), 0.15), 0.5)
    d["title"] = (d.get("title") or "")[:80]
    if d.get("template") not in templates.BY_ID:
        d["template"] = templates.TEMPLATES[0]["id"]
    if d.get("caption") not in templates.CAPTION_BY_ID:
        d["caption"] = templates.CAPTION_STYLES[0]["id"]
    if templates.BY_ID[d["template"]]["tier"] > P["tier"] or templates.CAPTION_BY_ID[d["caption"]]["tier"] > P["tier"]:
        raise HTTPException(402, "Esse visual ou legenda não está no seu plano. Suba de plano pra usar.")
    if not P["watermark_off"]:
        d["watermark"] = True
    d["react_pos"] = "slot" if d.get("react_pos") == "slot" else "float"
    for k in [k for k in d if k.startswith("_")]:
        d.pop(k)
    d.pop("segments", None)
    return d


def _segments(raw: dict, P: dict) -> list:
    segs = raw.get("segments") or []
    if isinstance(segs, str):
        try:
            segs = json.loads(segs or "[]")
        except ValueError:
            raise HTTPException(400, "Trechos inválidos")
    segs = [(secs(x[0]), secs(x[1]), (str(x[2])[:80] if len(x) > 2 else "")) for x in segs] if segs \
        else [(secs(raw.get("start")), secs(raw.get("end")), "")]
    if len(segs) > 1 and not P["batch"] and not (raw.get("analysis") and P.get("auto_moments")):
        raise HTTPException(402, "Corte em lote é do plano Pro.")
    if len(segs) > 12:
        raise HTTPException(400, "No máximo 12 trechos por vez")
    return segs


def _start(request: Request, raw: dict, upload: Path | None = None, cam: Path | None = None):
    return _start_for(current_user(request), raw, upload, cam)


def _start_for(u, raw: dict, upload: Path | None = None, cam: Path | None = None):
    P = accounts.plan_info(u)
    segs = _segments(raw, P)
    base = _opts(raw, P)
    lp = accounts.logo_path(u["id"])
    if P.get("own_logo") and lp.exists() and str(raw.get("logo", True)).lower() not in ("false", "0", ""):
        base["_logo"] = str(lp)
    keep = upload is not None and len(segs) > 1
    if raw.get("analysis"):  # cortes a partir do vídeo já enviado para o corte automático
        st = moments.get(raw["analysis"])
        if not st or st.get("user_id") != u["id"]:
            raise HTTPException(404, "Análise não encontrada")
        src = moments.source_of(raw["analysis"])
        if src:
            upload, keep = src, True
        elif st.get("url"):
            base["url"] = st["url"]
        else:
            raise HTTPException(410, "O vídeo dessa análise expirou. Envie de novo.")
    out = []
    try:
        for i, (a, b, ttl) in enumerate(segs):
            o = {**base, "start": a, "end": b}
            if ttl:
                o["title"] = ttl
            try:
                col = accounts.charge(accounts._user(u["id"]))
            except accounts.AuthError as e:
                if not out:
                    raise HTTPException(402, str(e))
                break  # cota acabou no meio do lote: gera os que deu
            try:
                job = video.create_job(o, upload=upload, priority=P["priority"], keep_upload=keep, cam=cam)
            except ValueError as e:
                accounts.refund(u["id"], col)
                raise HTTPException(400, str(e))
            accounts.add_job(job["id"], u["id"], (o["title"] or "Corte GTA") + (f" ({i + 1})" if len(segs) > 1 and not ttl else ""), col)
            out.append(job)
    finally:
        if upload is not None and len(segs) > 1 and not raw.get("analysis"):
            upload.unlink(missing_ok=True)
    return {**out[0], "jobs": [j["id"] for j in out], "user": accounts.public(accounts._user(u["id"]))}


def _refund_failed(jid: str):
    r = None
    for _ in range(25):  # o registro do corte pode chegar alguns ms depois
        r = accounts.job_owner(jid)
        if r:
            break
        time.sleep(0.2)
    if r and r["charged"]:
        accounts.refund(r["user_id"], r["charged"])


video.ON_FAIL = _refund_failed

AUTO_MAX = int(os.getenv("AUTO_CLIPS", "10"))  # quantos cortes a IA gera sozinha por vídeo


def _auto_cut(aid: str):
    """Fim da análise com "gerar sozinho": cria os melhores cortes direto, como no fluxo do Cut.Pro."""
    st = moments.get(aid)
    u = accounts._user(st["user_id"])
    left = accounts.public(u)["left"]
    ms = st.get("moments") or []
    n = min(int(st["auto"].get("n") or AUTO_MAX), AUTO_MAX, len(ms), left)
    if n <= 0:
        raise HTTPException(402, "Seus cortes do plano acabaram. Renove pra gerar os cortes desse vídeo.")
    raw = {k: v for k, v in st["auto"].items() if k != "n"}
    raw.update(analysis=aid, segments=[[m["start"], m["end"], m.get("title") or ""] for m in ms[:n]])
    res = _start_for(u, raw)
    for m, jid in zip(ms, res["jobs"]):
        m["job"] = jid
    note = None if len(res["jobs"]) == n else f"Gerei {len(res['jobs'])} cortes: seus cortes do plano acabaram."
    if n < min(len(ms), AUTO_MAX) and n == left:
        note = f"Gerei {n} cortes: era o que restava no seu plano."
    moments._set(aid, moments=ms, note=note)


moments.ON_READY = _auto_cut


def _my_video(aid: str, u) -> dict:
    st = moments.get(aid)
    if not st or st.get("user_id") != u["id"]:
        raise HTTPException(404, "Vídeo não encontrado")
    return st


@app.get("/api/my/videos")
def my_videos(request: Request):
    return moments.list_for(current_user(request)["id"])


@app.get("/api/videos/{aid}")
def video_page(aid: str, request: Request):
    """Página do vídeo: momentos achados pela IA e o estado de cada corte."""
    st = _my_video(aid, current_user(request))
    out = moments.public(st)
    out["moments"] = [{**m, "clip": ({"id": m["job"], **_job_state(m["job"])} if m.get("job") else None)}
                      for m in (st.get("moments") or []) if not m.get("hidden")]
    return out


class VidOpIn(BaseModel):
    jobs: list[str]
    action: str  # "visual" (troca o visual sem gastar corte), "apagar" ou "gerar" (momento ainda sem corte)
    template: str = ""
    caption: str = ""
    caption_color: str = "auto"
    starts: list[int] = []


@app.post("/api/videos/{aid}/batch")
def video_batch(aid: str, b: VidOpIn, request: Request):
    u = current_user(request)
    st = _my_video(aid, u)
    ms = st.get("moments") or []
    if b.action == "apagar":
        for m in ms:
            if m.get("job") in b.jobs:
                m["hidden"] = True
        moments._set(aid, moments=ms)
        return {"ok": True}
    P = accounts.plan_info(u)
    if b.action == "gerar":  # momentos que ficaram sem corte (gasta 1 corte cada)
        pick = [m for m in ms if not m.get("job") and m["start"] in b.starts][:12]
        if not pick:
            raise HTTPException(400, "Escolha os momentos")
        raw = {**{k: v for k, v in (st.get("auto") or {}).items() if k != "n"}, "analysis": aid,
               "segments": [[m["start"], m["end"], m.get("title") or ""] for m in pick]}
        res = _start_for(u, raw)
        for m, jid in zip(pick, res["jobs"]):
            m["job"] = jid
        moments._set(aid, moments=ms)
        return {"ok": True, "user": res["user"]}
    if b.action != "visual":
        raise HTTPException(400, "Ação inválida")
    # troca o visual dos cortes escolhidos: monta de novo sem descontar corte
    raw = {**(st.get("auto") or {}), "template": b.template, "caption": b.caption, "caption_color": b.caption_color}
    raw.pop("n", None)
    base = _opts(raw, P)
    lp = accounts.logo_path(u["id"])
    if P.get("own_logo") and lp.exists():
        base["_logo"] = str(lp)
    src = moments.source_of(aid)
    if not src and not st.get("url"):
        raise HTTPException(410, "O vídeo original expirou. Gere os cortes de novo.")
    if not src:
        base["url"] = st["url"]
    done = 0
    for m in ms:
        if m.get("job") not in b.jobs:
            continue
        o = {**base, "start": m["start"], "end": m["end"], "title": m.get("title") or ""}
        job = video.create_job(o, upload=src, priority=P["priority"], keep_upload=True)
        accounts.add_job(job["id"], u["id"], o["title"] or "Corte GTA", "")
        m["job"] = job["id"]
        done += 1
    st["auto"] = {**(st.get("auto") or {}), "template": b.template, "caption": b.caption, "caption_color": b.caption_color}
    moments._set(aid, moments=ms, auto=st["auto"])
    return {"ok": True, "n": done}


def _boot_asaas():
    import threading
    em = (os.getenv("ASAAS_WEBHOOK_EMAIL") or "").strip() or next((e.strip() for e in os.getenv("ADMIN_EMAILS", "").split(",") if "@" in e and "exemplo" not in e), "")
    threading.Thread(target=payments.ensure_asaas_webhook, args=(video.DATA, em), daemon=True).start()


_boot_asaas()


def _job_state(jid: str) -> dict:
    j = video.JOBS.get(jid)
    if j:
        out = {k: v for k, v in video.public(j).items() if k in ("status", "progress", "video", "thumb", "cover")
               or (k == "words" and j.get("status") == "revisar")}
        if j.get("status") == "na fila":
            out["position"] = video.queue_position(jid)
        return out
    d = video.JOBS_DIR / jid
    if (d / "corte.mp4").exists():
        return {"status": "pronto", "progress": 100, "video": f"/files/{jid}/corte.mp4", "thumb": f"/files/{jid}/thumb.jpg",
                "cover": f"/files/{jid}/capa.jpg" if (d / "capa.jpg").exists() else None}
    return {"status": "expirado"}


ALLOWED = ("https://www.twitch.tv/", "https://twitch.tv/", "https://m.twitch.tv/", "https://clips.twitch.tv/",
           "https://www.youtube.com/", "https://youtube.com/", "https://youtu.be/", "https://m.youtube.com/", "https://kick.com/")


@app.post("/api/jobs")
def new_job(j: JobIn, request: Request):
    if j.analysis:
        return _start(request, j.model_dump())
    if not j.url.startswith(ALLOWED):
        raise HTTPException(400, "Link precisa ser da Twitch, YouTube ou Kick")
    return _start(request, j.model_dump())


# ---------------- envio em partes (vídeos grandes, retoma se a conexão cair) ----------------
UPLOADS = video.SCRATCH / "envios"
UPLOADS.mkdir(parents=True, exist_ok=True)


class UpStartIn(BaseModel):
    name: str
    size: int


@app.post("/api/upload/start")
def upload_start(b: UpStartIn, request: Request):
    import secrets as _s
    u = current_user(request)
    lim = accounts.plan_info(u)["max_mb"]
    if b.size <= 0:
        raise HTTPException(400, "Arquivo vazio")
    if b.size > lim * 1048576:
        raise HTTPException(413, f"Vídeo acima de {lim // 1024} GB no seu plano. Cole o link da live e marque o trecho.")
    need = int(b.size * 1.15 + 150 * 1048576)  # espaço pro arquivo + a margem do corte
    free = video.free_space(need)
    if need > free:
        log.error("Disco de trabalho cheio: %s MB livres, envio de %s MB", free // 1048576, b.size // 1048576)
        raise HTTPException(507, "Estamos com muitos vídeos sendo processados agora. Tente em alguns minutos ou cole o link.")
    uid = _s.token_hex(10)
    ext = (Path(b.name).suffix or ".mp4")[:6].lower()
    (UPLOADS / f"{uid}.json").write_text(json.dumps({"user": u["id"], "size": b.size, "ext": ext, "created": time.time(), "name": b.name[:120]}))
    (UPLOADS / f"{uid}.name").write_text(b.name[:120])
    (UPLOADS / f"{uid}.part").touch()
    return {"id": uid, "received": 0}


def _up_meta(uid: str, u) -> dict:
    if not uid.isalnum():
        raise HTTPException(400, "Envio inválido")
    f = UPLOADS / f"{uid}.json"
    if not f.exists():
        raise HTTPException(404, "Envio não encontrado. Comece de novo.")
    m = json.loads(f.read_text())
    if m["user"] != u["id"]:
        raise HTTPException(404, "Envio não encontrado")
    return m


@app.get("/api/upload/{uid}")
def upload_status(uid: str, request: Request):
    m = _up_meta(uid, current_user(request))
    return {"id": uid, "received": (UPLOADS / f"{uid}.part").stat().st_size, "size": m["size"]}


@app.put("/api/upload/{uid}")
async def upload_chunk(uid: str, request: Request, offset: int = 0):
    m = _up_meta(uid, current_user(request))
    part = UPLOADS / f"{uid}.part"
    have = part.stat().st_size
    if offset != have:
        async for _ in request.stream():  # descarta o pedaço repetido sem derrubar a conexão
            pass
        return JSONResponse({"received": have}, status_code=409)
    n = 0
    with open(part, "ab") as f:
        async for chunk in request.stream():
            n += len(chunk)
            if have + n > m["size"] or n > 64 * 1048576:
                f.truncate(have)
                raise HTTPException(400, "Pedaço inválido")
            f.write(chunk)
    return {"received": have + n, "size": m["size"]}


def _up_name(uid: str) -> str:
    f = UPLOADS / f"{uid}.name"
    try:
        n = f.read_text()
        f.unlink(missing_ok=True)
        return n
    except Exception:
        return ""


def take_upload(uid: str, u) -> Path:
    """Entrega o arquivo completo de um envio em partes para virar corte ou análise."""
    m = _up_meta(uid, u)
    part = UPLOADS / f"{uid}.part"
    if part.stat().st_size != m["size"]:
        raise HTTPException(409, "O envio do vídeo não terminou. Tente de novo.")
    dst = UPLOADS / f"{uid}{m['ext']}"
    part.rename(dst)
    (UPLOADS / f"{uid}.json").unlink(missing_ok=True)
    return dst


def _upload_cleanup():
    while True:
        lim = time.time() - 86400
        for f in UPLOADS.iterdir():
            try:
                if f.stat().st_mtime < lim:
                    f.unlink(missing_ok=True)
            except Exception:
                pass
        time.sleep(3 * 3600)


import threading as _th
_th.Thread(target=_upload_cleanup, daemon=True).start()


@app.post("/api/jobs/upload")
async def new_upload(request: Request, file: UploadFile | None = File(None), url: str = Form(""), react_pos: str = Form("float"), analysis: str = Form(""), upload_id: str = Form(""), start: str = Form(""), end: str = Form(""), segments: str = Form(""),
                     template: str = Form("basico-legenda"), caption: str = Form("pesada-karaoke"), caption_color: str = Form("auto"),
                     corner: str = Form("top-left"), cam_size: float = Form(0.30), title: str = Form(""), captions: bool = Form(True),
                     watermark: bool = Form(True), cover: bool = Form(True), cam: UploadFile | None = File(None), logo: bool = Form(True), review: bool = Form(False)):
    u = current_user(request)
    max_mb = accounts.plan_info(u)["max_mb"]
    if (file is None or not file.filename) and not analysis and not upload_id:
        if not url.startswith(ALLOWED):
            raise HTTPException(400, "Envie um vídeo ou um link da Twitch, YouTube ou Kick")
        if cam is None or not cam.filename:
            raise HTTPException(400, "Use este envio só com o vídeo do react")
    tmp = take_upload(upload_id, u) if upload_id else None if file is None or not file.filename else Path(tempfile.mkstemp(suffix=Path(file.filename or "v.mp4").suffix or ".mp4", dir=video.SCRATCH)[1])
    size = 0
    with open(os.devnull if (tmp is None or upload_id) else tmp, "wb") as f:
        while tmp and not upload_id and (chunk := await file.read(1 << 20)):
            size += len(chunk)
            if size > max_mb * 1048576:
                f.close(); tmp.unlink(missing_ok=True)
                raise HTTPException(413, f"Vídeo acima de {max_mb // 1024} GB no seu plano. Cole o link da live e marque o trecho.")
            f.write(chunk)
    cam_tmp = None
    if cam is not None and cam.filename:
        cam_tmp = Path(tempfile.mkstemp(suffix=Path(cam.filename).suffix or ".mp4", dir=video.SCRATCH)[1])
        csize = 0
        with open(cam_tmp, "wb") as f:
            while chunk := await cam.read(1 << 20):
                csize += len(chunk)
                if csize > 1024 * 1048576:
                    f.close(); cam_tmp.unlink(missing_ok=True)
                    if tmp:
                        tmp.unlink(missing_ok=True)
                    raise HTTPException(413, "O vídeo do react pode ter até 1 GB.")
                f.write(chunk)
    try:
        return _start(request, {"url": url if tmp is None else "upload", "analysis": analysis, "react_pos": react_pos, "start": start, "end": end, "segments": segments, "template": template, "caption": caption,
                                "caption_color": caption_color, "corner": corner, "cam_size": cam_size, "title": title,
                                "captions": captions, "watermark": watermark, "cover": cover, "logo": logo, "review": review}, upload=tmp, cam=cam_tmp)
    except HTTPException:
        if tmp:
            tmp.unlink(missing_ok=True)
        raise
    finally:
        if cam_tmp:
            cam_tmp.unlink(missing_ok=True)


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    st = _job_state(jid)
    if st["status"] == "expirado" and not accounts.job_owner(jid):
        raise HTTPException(404, "Corte não encontrado")
    return {"id": jid, **st}


@app.post("/api/moments")
async def new_moments(request: Request, file: UploadFile | None = File(None), url: str = Form(""), upload_id: str = Form(""),
                      auto: str = Form(""), template: str = Form("basico-legenda"), caption: str = Form("pesada-karaoke"),
                      caption_color: str = Form("auto"), captions: str = Form("true"), cover: str = Form("true"), n: int = Form(10)):
    u = current_user(request)
    P = accounts.plan_info(u)
    ao = None
    if auto in ("1", "true"):  # gera os melhores cortes sozinho quando a análise terminar
        if accounts.public(u)["left"] <= 0:
            raise HTTPException(402, "Seus cortes do plano acabaram. Renove pra gerar mais.")
        ao = _opts({"template": template, "caption": caption, "caption_color": caption_color,
                    "captions": captions not in ("false", "0"), "cover": cover not in ("false", "0")}, P)
        ao["n"] = max(1, min(int(n or 10), AUTO_MAX))
    if not P.get("auto_moments"):
        raise HTTPException(402, "O corte automático com IA é dos planos Clipador e Pro.")
    if not video.GROQ_KEY:
        raise HTTPException(503, "O corte automático está em manutenção. Escolha o trecho na mão por enquanto.")
    busy = [s for s in moments.STATE.values() if s.get("user_id") == u["id"] and s.get("status") not in ("pronto", "erro")]
    if busy:
        return moments.public(busy[0])
    if upload_id:
        return moments.create(u["id"], upload=take_upload(upload_id, u), auto=ao, name=_up_name(upload_id))
    if file is not None and file.filename:
        tmp = Path(tempfile.mkstemp(suffix=Path(file.filename).suffix or ".mp4", dir=video.SCRATCH)[1])
        size = 0
        with open(tmp, "wb") as f:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > P["max_mb"] * 1048576:
                    f.close(); tmp.unlink(missing_ok=True)
                    raise HTTPException(413, f"Vídeo acima de {P['max_mb'] // 1024} GB no seu plano. Cole o link da live.")
                f.write(chunk)
        return moments.create(u["id"], upload=tmp, auto=ao, name=file.filename or "")
    if not url.startswith(ALLOWED):
        raise HTTPException(400, "Envie um vídeo ou um link da Twitch, YouTube ou Kick")
    return moments.create(u["id"], url=url, auto=ao)


@app.get("/api/moments/{aid}")
def get_moments(aid: str, request: Request):
    u = current_user(request)
    st = moments.get(aid)
    if not st or st.get("user_id") != u["id"]:
        raise HTTPException(404, "Análise não encontrada")
    return moments.public(st)


class CaptionsIn(BaseModel):
    words: list


@app.post("/api/jobs/{jid}/captions")
def save_captions(jid: str, b: CaptionsIn, request: Request):
    u = current_user(request)
    r = accounts.job_owner(jid)
    if not r or r["user_id"] != u["id"]:
        raise HTTPException(404, "Corte não encontrado")
    words = []
    for w in b.words[:3000]:
        try:
            t = str(w["word"])[:40]
            if t.strip():
                words.append({"word": t, "start": float(w["start"]), "end": float(w["end"])})
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "Legenda inválida")
    try:
        video.resume_with_words(jid, words)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.get("/api/my/stats")
def my_stats(request: Request):
    u = current_user(request)
    return {**accounts.public(u), **accounts.user_stats(u["id"]), "quota": plans.PLANS[accounts.active_plan(u)]["quota"], "logo": _logo_data(u["id"])}


@app.post("/api/my/logo")
async def upload_logo(request: Request, file: UploadFile = File(...)):
    u = current_user(request)
    if not accounts.plan_info(u).get("own_logo"):
        raise HTTPException(402, "Colocar a sua logo nos cortes é dos planos Clipador e Pro.")
    data = await file.read(5 * 1048576 + 1)
    if len(data) > 5 * 1048576:
        raise HTTPException(413, "A logo pode ter até 5 MB.")
    from PIL import Image
    import io
    try:
        im = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        raise HTTPException(400, "Envie uma imagem PNG ou JPG.")
    im.thumbnail((400, 400))
    im.save(accounts.logo_path(u["id"]), "PNG")
    return {"ok": True, "logo": _logo_data(u["id"])}


@app.delete("/api/my/logo")
def delete_logo(request: Request):
    u = current_user(request)
    accounts.logo_path(u["id"]).unlink(missing_ok=True)
    return {"ok": True}


def _logo_data(uid):
    import base64
    p = accounts.logo_path(uid)
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode() if p.exists() else None


@app.get("/api/my/jobs")
def my_jobs(request: Request):
    u = current_user(request)
    return [{**j, **_job_state(j["id"])} for j in accounts.user_jobs(u["id"])]


# ---------------- lista de espera ----------------
class WaitIn(BaseModel):
    name: str
    handle: str
    whatsapp: str


WAITLIST = video.DATA / "lista_espera_automatico.jsonl"


@app.post("/api/waitlist")
def waitlist(w: WaitIn):
    rec = {k: str(v)[:80] for k, v in w.model_dump().items()}
    rec["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(WAITLIST, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": True}


# ---------------- admin ----------------
@app.get("/api/admin/overview")
def admin_overview(request: Request):
    admin_user(request)
    wl = WAITLIST.read_text(encoding="utf-8").splitlines() if WAITLIST.exists() else []
    return {**accounts.admin_stats(), "fila_agora": video.queue_size(), "lista_espera": len(wl)}


@app.get("/api/admin/crm")
def admin_crm(request: Request):
    admin_user(request)
    rows, extra = accounts.crm_rows()
    wl = [json.loads(l) for l in WAITLIST.read_text(encoding="utf-8").splitlines()] if WAITLIST.exists() else []
    import hashlib as _hl
    for w in wl:
        key = "w:" + _hl.sha1((w.get("at", "") + w.get("whatsapp", "")).encode()).hexdigest()[:12]
        k = extra.get(key, {})
        rows.append({"key": key, "tipo": "automatico", "name": w.get("name"), "email": "", "phone": w.get("whatsapp"), "handle": w.get("handle"),
                     "created": time.mktime(time.strptime(w["at"], "%Y-%m-%d %H:%M:%S")) if w.get("at") else None,
                     "plan_name": "Interessado no Automático", "cortes": 0, "pago": 0, "stage": k.get("stage") or "novo", "notes": k.get("notes") or ""})
    rows.sort(key=lambda r: -(r.get("created") or 0))
    return {"stages": accounts.STAGES, "leads": rows}


class CrmIn(BaseModel):
    key: str
    stage: str | None = None
    notes: str | None = None


@app.post("/api/admin/crm")
def admin_crm_set(b: CrmIn, request: Request):
    admin_user(request)
    return _wrap(accounts.crm_set, b.key[:40], b.stage, b.notes)


@app.get("/api/admin/users")
def admin_users(request: Request, q: str = ""):
    admin_user(request)
    return accounts.admin_users(q)


@app.get("/api/admin/payments")
def admin_payments(request: Request):
    admin_user(request)
    return accounts.admin_payments()


@app.get("/api/admin/waitlist")
def admin_waitlist(request: Request):
    admin_user(request)
    return [json.loads(l) for l in WAITLIST.read_text(encoding="utf-8").splitlines()] if WAITLIST.exists() else []


class AdminPlanIn(BaseModel):
    user_id: int
    plan: str


@app.post("/api/admin/plan")
def admin_plan(b: AdminPlanIn, request: Request):
    admin_user(request)
    return _wrap(accounts.activate, b.user_id, b.plan, "admin")


class AdminCreditsIn(BaseModel):
    user_id: int
    amount: int


@app.post("/api/admin/credits")
def admin_credits(b: AdminCreditsIn, request: Request):
    admin_user(request)
    return accounts.admin_add_credits(b.user_id, b.amount)


# ---------------- páginas ----------------
app.mount("/files", StaticFiles(directory=video.JOBS_DIR), name="files")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
PREVIEWS = Path(__file__).parent / "previews"
PREVIEWS.mkdir(exist_ok=True)
app.mount("/previews", StaticFiles(directory=PREVIEWS), name="previews")


from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import HTMLResponse, JSONResponse


@app.exception_handler(StarletteHTTPException)
async def not_found(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404 and not request.url.path.startswith(("/api/", "/files/", "/previews/", "/static/")):
        return HTMLResponse(NOT_FOUND, status_code=404)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


NOT_FOUND = """<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Página não encontrada — GTA Clipadas</title><link rel="icon" href="/static/logo.jpg">
<style>@font-face{font-family:Inter;src:url(/static/inter-800.woff2) format("woff2");font-weight:800}@font-face{font-family:Inter;src:url(/static/inter-400.woff2) format("woff2");font-weight:400}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#09090b;color:#fafafa;font:16px/1.6 Inter,system-ui,sans-serif;text-align:center;padding:20px}
img{width:72px;height:72px;border-radius:50%}h1{font-size:30px;letter-spacing:-.03em;margin:18px 0 6px}p{color:#a1a1aa;margin:0 0 24px}
a{display:inline-block;background:#ff1fa8;color:#fff;text-decoration:none;font-weight:800;padding:14px 24px;border-radius:12px}</style></head>
<body><div><img src="/static/logo.jpg" alt=""><h1>Essa página não existe</h1><p>O link pode estar errado ou o conteúdo saiu do ar.</p><a href="/">Voltar pro GTA Clipadas</a></div></body></html>"""


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/admin")
def admin_page():
    return FileResponse(STATIC / "admin.html")


def _legal(name: str):
    from fastapi.responses import HTMLResponse
    emp = os.getenv("EMPRESA_NOME", "").strip()
    cnpj = os.getenv("EMPRESA_CNPJ", "").strip()
    full = f"{emp}, CNPJ {cnpj}" if emp and cnpj else (emp or "os responsáveis pelo GTA Clipadas")
    contato = os.getenv("CONTATO_EMAIL", "").strip()
    contato = f'<a href="mailto:{contato}">{contato}</a>' if contato else "pelos canais de suporte do site"
    h = (STATIC / name).read_text(encoding="utf-8").replace("{{EMPRESA}}", full).replace("{{EMPRESA_CAP}}", full[:1].upper() + full[1:]).replace("{{CONTATO}}", contato)
    return HTMLResponse(h)


@app.get("/termos")
def termos():
    return _legal("termos.html")


@app.get("/privacidade")
def privacidade():
    return _legal("privacidade.html")
