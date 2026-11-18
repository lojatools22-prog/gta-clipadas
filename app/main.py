import json, logging, os, tempfile, time
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx

from . import twitch, video, templates, accounts, plans, payments, notify

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


def admin_user(request: Request):
    u = current_user(request)
    if not accounts.is_admin(u):
        raise HTTPException(403, "Só para administradores")
    return u


# ---------------- básico ----------------
@app.get("/api/health")
def health():
    return {"ok": True, "fila": video.queue_size()}


@app.get("/api/status")
def status():
    return {"demo": twitch.demo_mode(), "captions": bool(video.GROQ_KEY or video.OPENAI_KEY),
            "corners": video.CAM_CORNERS, "max_len": video.MAX_LEN, "free_clips": plans.PLANS["gratis"]["quota"],
            "plans": plans.public(), "checkout": payments.ready(), "provider": payments.PROVIDER, "sms": accounts.sms_on(),
            "max_upload_mb": max(p["max_mb"] for p in plans.PLANS.values())}


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
    if p.get("status") not in ("RECEIVED", "CONFIRMED", "RECEIVED_IN_CASH"):
        return {"ok": True}
    try:
        uid, plan = (p.get("externalReference") or "").split(":")
        uid = int(uid)
    except ValueError:
        return {"ok": True}
    if plan not in plans.PAID:
        return {"ok": True}
    amount = float(p.get("value") or 0)
    if accounts.payment_upsert("asaas:" + p["id"], uid, plan, amount, "approved", "asaas"):
        if amount + 0.01 < plans.PLANS[plan]["price"]:
            log.error("Pagamento Asaas %s com valor menor que o plano (%s)", p["id"], amount)
        else:
            accounts.activate(uid, plan, "asaas")
            log.info("Plano %s liberado para usuário %s (Asaas %s)", plan, uid, p["id"])
    return {"ok": True}


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
    url: str
    start: str | float | None = None
    end: str | float | None = None
    segments: list = []
    template: str = "vice-cam-cima"
    caption: str = "pesada-karaoke"
    caption_color: str = "auto"
    watermark: bool = True
    cover: bool = True
    corner: str = "top-left"
    cam_size: float = 0.30
    title: str = ""
    captions: bool = True


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
    d.pop("segments", None)
    return d


def _segments(raw: dict, P: dict) -> list:
    segs = raw.get("segments") or []
    if isinstance(segs, str):
        try:
            segs = json.loads(segs or "[]")
        except ValueError:
            raise HTTPException(400, "Trechos inválidos")
    segs = [(secs(a), secs(b)) for a, b in segs] if segs else [(secs(raw.get("start")), secs(raw.get("end")))]
    if len(segs) > 1 and not P["batch"]:
        raise HTTPException(402, "Corte em lote é do plano Pro.")
    if len(segs) > 10:
        raise HTTPException(400, "No máximo 10 trechos por vez")
    return segs


def _start(request: Request, raw: dict, upload: Path | None = None):
    u = current_user(request)
    P = accounts.plan_info(u)
    segs = _segments(raw, P)
    base = _opts(raw, P)
    out = []
    try:
        for i, (a, b) in enumerate(segs):
            o = {**base, "start": a, "end": b}
            if len(segs) > 1 and base["title"]:
                o["title"] = base["title"]
            try:
                col = accounts.charge(accounts._user(u["id"]))
            except accounts.AuthError as e:
                if not out:
                    raise HTTPException(402, str(e))
                break  # cota acabou no meio do lote: gera os que deu
            try:
                job = video.create_job(o, upload=upload, priority=P["priority"], keep_upload=upload is not None and len(segs) > 1)
            except ValueError as e:
                accounts.refund(u["id"], col)
                raise HTTPException(400, str(e))
            accounts.add_job(job["id"], u["id"], (o["title"] or "Corte GTA") + (f" ({i + 1})" if len(segs) > 1 else ""), col)
            out.append(job)
    finally:
        if upload is not None and len(segs) > 1:
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


def _job_state(jid: str) -> dict:
    j = video.JOBS.get(jid)
    if j:
        return {k: v for k, v in video.public(j).items() if k in ("status", "progress", "video", "thumb", "cover")}
    d = video.JOBS_DIR / jid
    if (d / "corte.mp4").exists():
        return {"status": "pronto", "progress": 100, "video": f"/files/{jid}/corte.mp4", "thumb": f"/files/{jid}/thumb.jpg",
                "cover": f"/files/{jid}/capa.jpg" if (d / "capa.jpg").exists() else None}
    return {"status": "expirado"}


ALLOWED = ("https://www.twitch.tv/", "https://twitch.tv/", "https://m.twitch.tv/", "https://clips.twitch.tv/",
           "https://www.youtube.com/", "https://youtube.com/", "https://youtu.be/", "https://m.youtube.com/", "https://kick.com/")


@app.post("/api/jobs")
def new_job(j: JobIn, request: Request):
    if not j.url.startswith(ALLOWED):
        raise HTTPException(400, "Link precisa ser da Twitch, YouTube ou Kick")
    return _start(request, j.model_dump())


@app.post("/api/jobs/upload")
async def new_upload(request: Request, file: UploadFile = File(...), start: str = Form(""), end: str = Form(""), segments: str = Form(""),
                     template: str = Form("vice-cam-cima"), caption: str = Form("pesada-karaoke"), caption_color: str = Form("auto"),
                     corner: str = Form("top-left"), cam_size: float = Form(0.30), title: str = Form(""), captions: bool = Form(True),
                     watermark: bool = Form(True), cover: bool = Form(True)):
    u = current_user(request)
    max_mb = accounts.plan_info(u)["max_mb"]
    tmp = Path(tempfile.mkstemp(suffix=Path(file.filename or "v.mp4").suffix or ".mp4", dir=video.DATA)[1])
    size = 0
    with open(tmp, "wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > max_mb * 1048576:
                f.close(); tmp.unlink(missing_ok=True)
                raise HTTPException(413, f"Vídeo acima de {max_mb // 1024} GB no seu plano. Cole o link da live e marque o trecho.")
            f.write(chunk)
    try:
        return _start(request, {"url": "upload", "start": start, "end": end, "segments": segments, "template": template, "caption": caption,
                                "caption_color": caption_color, "corner": corner, "cam_size": cam_size, "title": title,
                                "captions": captions, "watermark": watermark, "cover": cover}, upload=tmp)
    except HTTPException:
        tmp.unlink(missing_ok=True)
        raise


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    st = _job_state(jid)
    if st["status"] == "expirado" and not accounts.job_owner(jid):
        raise HTTPException(404, "Corte não encontrado")
    return {"id": jid, **st}


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


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/admin")
def admin_page():
    return FileResponse(STATIC / "admin.html")


@app.get("/termos")
def termos():
    return FileResponse(STATIC / "termos.html")


@app.get("/privacidade")
def privacidade():
    return FileResponse(STATIC / "privacidade.html")
