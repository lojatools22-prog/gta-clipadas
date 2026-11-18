"""Contas: cadastro, login, verificação por SMS, recuperação de senha, planos/cotas, pagamentos e cortes (SQLite)."""
import hashlib, hmac, os, re, secrets, sqlite3, threading, time
from pathlib import Path

from . import notify
from .plans import PLANS

DATA = Path(os.getenv("DATA_DIR", Path(__file__).parent.parent / "data"))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / "contas.db"
DATA_LOGOS = DATA / "logos"
DATA_LOGOS.mkdir(parents=True, exist_ok=True)
LOGIN_SMS = os.getenv("LOGIN_SMS", "1") == "1"          # pede código por SMS ao entrar num aparelho novo
DEV_SHOW_CODES = os.getenv("DEV_SHOW_CODES", "0") == "1"  # só para testes: devolve o código na resposta
ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(DB, check_same_thread=False, timeout=15)
    c.row_factory = sqlite3.Row
    return c


with _conn() as c:
    c.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE, phone TEXT, phone_verified INTEGER DEFAULT 0,
        pw TEXT, salt TEXT, credits INTEGER DEFAULT 0, plan TEXT DEFAULT 'gratis', plan_until REAL DEFAULT 0, quota_left INTEGER DEFAULT 0,
        created REAL, last_login REAL);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER, created REAL);
    CREATE TABLE IF NOT EXISTS devices(user_id INTEGER, device TEXT, created REAL, PRIMARY KEY(user_id, device));
    CREATE TABLE IF NOT EXISTS otps(id TEXT PRIMARY KEY, user_id INTEGER, purpose TEXT, code_hash TEXT, expires REAL, attempts INTEGER DEFAULT 0, extra TEXT);
    CREATE TABLE IF NOT EXISTS otp_sends(target TEXT, at REAL);
    CREATE TABLE IF NOT EXISTS codes_used(code TEXT PRIMARY KEY, user_id INTEGER, at REAL);
    CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, user_id INTEGER, title TEXT, created REAL, charged TEXT);
    CREATE TABLE IF NOT EXISTS crm(key TEXT PRIMARY KEY, stage TEXT, notes TEXT, updated REAL);
    CREATE TABLE IF NOT EXISTS payments(id TEXT PRIMARY KEY, user_id INTEGER, plan TEXT, amount REAL, status TEXT, provider TEXT, created REAL, updated REAL);
    """)


with _conn() as c:
    try:
        c.execute("ALTER TABLE users ADD COLUMN asaas_customer TEXT")
    except sqlite3.OperationalError:
        pass


def set_asaas_customer(user_id: int, cid: str):
    with _lock, _conn() as c:
        c.execute("UPDATE users SET asaas_customer=? WHERE id=?", (cid, user_id))


class AuthError(Exception):
    pass


# ---------------- utilidades ----------------
def _hash(pw: str, salt: str) -> str:
    return hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1).hex()


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def norm_email(e: str) -> str:
    e = (e or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e):
        raise AuthError("E-mail inválido")
    return e


def norm_phone(p: str) -> str:
    d = re.sub(r"\D", "", p or "")
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    if len(d) not in (10, 11):
        raise AuthError("Celular inválido. Use DDD + número, ex.: (11) 99999-9999")
    return "+55" + d


def _user(uid):
    with _conn() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def _session(user_id: int) -> str:
    tok = secrets.token_urlsafe(32)
    with _lock, _conn() as c:
        c.execute("INSERT INTO sessions VALUES(?,?,?)", (tok, user_id, time.time()))
        c.execute("UPDATE users SET last_login=? WHERE id=?", (time.time(), user_id))
    return tok


def _trust(user_id: int, device: str):
    if device:
        with _lock, _conn() as c:
            c.execute("INSERT OR IGNORE INTO devices VALUES(?,?,?)", (user_id, device[:80], time.time()))


def _trusted(user_id: int, device: str) -> bool:
    if not device:
        return False
    with _conn() as c:
        return bool(c.execute("SELECT 1 FROM devices WHERE user_id=? AND device=?", (user_id, device[:80])).fetchone())


# ---------------- códigos (SMS / e-mail) ----------------
def _rate(target: str, limit=5, window=3600):
    now = time.time()
    with _lock, _conn() as c:
        n = c.execute("SELECT COUNT(*) FROM otp_sends WHERE target=? AND at>?", (target, now - window)).fetchone()[0]
        if n >= limit:
            raise AuthError("Muitos códigos enviados. Espere alguns minutos e tente de novo.")
        c.execute("INSERT INTO otp_sends VALUES(?,?)", (target, now))


def _new_otp(user_id: int, purpose: str, extra: str = "") -> tuple[str, str]:
    code = f"{secrets.randbelow(1000000):06d}"
    oid = secrets.token_urlsafe(16)
    with _lock, _conn() as c:
        c.execute("DELETE FROM otps WHERE user_id=? AND purpose=?", (user_id, purpose))
        c.execute("INSERT INTO otps VALUES(?,?,?,?,?,0,?)", (oid, user_id, purpose, _code_hash(code), time.time() + 600, extra))
    return oid, code


def _check_otp(oid: str, code: str, purpose: str):
    with _lock, _conn() as c:
        o = c.execute("SELECT * FROM otps WHERE id=? AND purpose=?", (oid, purpose)).fetchone()
        if not o or o["expires"] < time.time():
            raise AuthError("O código expirou. Peça um novo.")
        if o["attempts"] >= 5:
            raise AuthError("Muitas tentativas. Peça um novo código.")
        if not hmac.compare_digest(o["code_hash"], _code_hash(re.sub(r"\D", "", code or ""))):
            c.execute("UPDATE otps SET attempts=attempts+1 WHERE id=?", (oid,))
            raise AuthError("Código errado")
        c.execute("DELETE FROM otps WHERE id=?", (oid,))
        return o


def sms_on() -> bool:
    """Sem Twilio configurada, o site funciona sem SMS (cadastro só com e-mail e senha)."""
    return notify.sms_ready() or DEV_SHOW_CODES


def _finish_phone(uid: int, device: str) -> dict:
    u = _user(uid)
    with _lock, _conn() as c:
        dup = c.execute("SELECT 1 FROM users WHERE phone=? AND phone_verified=1 AND id<>?", (u["phone"], u["id"])).fetchone()
        c.execute("UPDATE users SET phone_verified=1, credits=? WHERE id=?", (0 if dup else PLANS["gratis"]["quota"], u["id"]))
    _trust(uid, device)
    return {"step": "done", "token": _session(uid)}


def _send_sms_code(user_id: int, phone: str, purpose: str, extra: str = "") -> dict:
    _rate(phone)
    oid, code = _new_otp(user_id, purpose, extra)
    notify.send_sms(phone, f"GTA6 Clips: seu código é {code}. Vale por 10 minutos. Não passe pra ninguém.")
    out = {"challenge": oid, "phone_hint": phone[:-4].replace(phone[3:-4], "•" * len(phone[3:-4])) + phone[-4:]}
    if DEV_SHOW_CODES:
        out["dev_code"] = code
    return out


# ---------------- cadastro e login ----------------
def signup(name, email, phone, password, device="") -> dict:
    email, phone = norm_email(email), norm_phone(phone)
    name = (name or "").strip()[:60]
    if len(name) < 2:
        raise AuthError("Coloque seu nome")
    if len(password or "") < 6:
        raise AuthError("A senha precisa ter pelo menos 6 caracteres")
    salt = secrets.token_hex(16)
    try:
        with _lock, _conn() as c:
            uid = c.execute("INSERT INTO users(name,email,phone,pw,salt,created) VALUES(?,?,?,?,?,?)",
                            (name, email, phone, _hash(password, salt), salt, time.time())).lastrowid
    except sqlite3.IntegrityError:
        raise AuthError("Esse e-mail já tem conta. Entre com sua senha.")
    if not sms_on():
        return _finish_phone(uid, device)
    return {"step": "phone", **_send_sms_code(uid, phone, "phone", device)}


def verify_phone(challenge: str, code: str) -> str:
    o = _check_otp(challenge, code, "phone")
    u = _user(o["user_id"])
    with _lock, _conn() as c:
        # cortes grátis só uma vez por número de celular
        dup = c.execute("SELECT 1 FROM users WHERE phone=? AND phone_verified=1 AND id<>?", (u["phone"], u["id"])).fetchone()
        c.execute("UPDATE users SET phone_verified=1, credits=? WHERE id=?", (0 if dup else PLANS["gratis"]["quota"], u["id"]))
    _trust(u["id"], o["extra"])
    return _session(u["id"])


def resend(challenge: str) -> dict:
    with _conn() as c:
        o = c.execute("SELECT * FROM otps WHERE id=?", (challenge,)).fetchone()
    if not o:
        raise AuthError("Comece de novo")
    u = _user(o["user_id"])
    return _send_sms_code(u["id"], u["phone"], o["purpose"], o["extra"])


def login(email, password, device="") -> dict:
    email = norm_email(email)
    with _conn() as c:
        u = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not u or not hmac.compare_digest(u["pw"], _hash(password or "", u["salt"])):
        raise AuthError("E-mail ou senha errados")
    if not u["phone_verified"]:
        if not sms_on():
            return _finish_phone(u["id"], device)
        return {"step": "phone", **_send_sms_code(u["id"], u["phone"], "phone", device)}
    if LOGIN_SMS and sms_on() and not _trusted(u["id"], device):
        return {"step": "login_sms", **_send_sms_code(u["id"], u["phone"], "login", device)}
    return {"step": "done", "token": _session(u["id"])}


def verify_login(challenge: str, code: str) -> str:
    o = _check_otp(challenge, code, "login")
    _trust(o["user_id"], o["extra"])
    return _session(o["user_id"])


def forgot(email: str) -> dict:
    email = norm_email(email)
    with _conn() as c:
        u = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not u:  # não revela se o e-mail existe
        return {"challenge": secrets.token_urlsafe(16), "via": "sms"}
    if u["phone_verified"] and sms_on():
        return {"via": "sms", **_send_sms_code(u["id"], u["phone"], "reset")}
    if not notify.email_ready() and not DEV_SHOW_CODES:
        raise AuthError("Recuperação de senha indisponível agora. Fale com a gente no WhatsApp.")
    _rate(email)
    oid, code = _new_otp(u["id"], "reset")
    notify.send_email(email, "Seu código do GTA6 Clips", f"<p>Seu código para criar uma nova senha é <b>{code}</b>. Vale por 10 minutos.</p>")
    out = {"via": "email", "challenge": oid}
    if DEV_SHOW_CODES:
        out["dev_code"] = code
    return out


def reset(challenge: str, code: str, password: str, device="") -> str:
    if len(password or "") < 6:
        raise AuthError("A senha precisa ter pelo menos 6 caracteres")
    o = _check_otp(challenge, code, "reset")
    salt = secrets.token_hex(16)
    with _lock, _conn() as c:
        c.execute("UPDATE users SET pw=?, salt=? WHERE id=?", (_hash(password, salt), salt, o["user_id"]))
        c.execute("DELETE FROM sessions WHERE user_id=?", (o["user_id"],))
    _trust(o["user_id"], device)
    return _session(o["user_id"])


def logout(token: str):
    with _lock, _conn() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (token,))


def user_by_token(token):
    if not token:
        return None
    with _conn() as c:
        u = c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (token,)).fetchone()
    return u if u and u["phone_verified"] else None


# ---------------- planos e cotas ----------------
def active_plan(u) -> str:
    if is_admin(u):  # dono da plataforma: tudo liberado (Pro) para testar
        return "pro"
    return u["plan"] if u and u["plan"] != "gratis" and (u["plan_until"] or 0) > time.time() else "gratis"


def plan_info(u) -> dict:
    return PLANS[active_plan(u)]


def is_admin(u) -> bool:
    return bool(u) and (u["email"] or "").lower() in ADMIN_EMAILS


def logo_path(user_id) -> Path:
    return DATA_LOGOS / f"{int(user_id)}.png"


def public(u) -> dict:
    p = active_plan(u)
    left = 999 if is_admin(u) else u["credits"] if p == "gratis" else u["quota_left"]
    return {"name": u["name"], "email": u["email"], "phone": u["phone"][:-4].replace(u["phone"][3:-4], "•" * len(u["phone"][3:-4])) + u["phone"][-4:],
            "plan": p, "plan_name": PLANS[p]["name"], "plan_until": u["plan_until"] if p != "gratis" else None,
            "left": left, "tier": PLANS[p]["tier"], "max_mb": PLANS[p]["max_mb"], "batch": PLANS[p]["batch"],
            "watermark_off": PLANS[p]["watermark_off"], "own_logo": PLANS[p].get("own_logo", False), "auto_moments": PLANS[p].get("auto_moments", False), "has_logo": logo_path(u["id"]).exists(),
            "admin": is_admin(u), "has_billing": bool(u["asaas_customer"]),
            # compatibilidade com a interface
            "credits": left, "pro": p != "gratis"}


def activate(user_id: int, plan: str, source: str = "") -> dict:
    if plan not in PLANS or plan == "gratis":
        raise AuthError("Plano inválido")
    P = PLANS[plan]
    with _lock, _conn() as c:
        u = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        now = time.time()
        if u["plan"] == plan and (u["plan_until"] or 0) > now:   # renovação: soma tempo e cota
            until, quota = u["plan_until"] + P["days"] * 86400, u["quota_left"] + P["quota"]
        else:
            until, quota = now + P["days"] * 86400, P["quota"]
        c.execute("UPDATE users SET plan=?, plan_until=?, quota_left=? WHERE id=?", (plan, until, quota, user_id))
    return public(_user(user_id))


def redeem(u, code: str, codes_by_plan: dict) -> dict:
    code = (code or "").strip().upper()
    plan = next((p for p, cs in codes_by_plan.items() if code in cs), None)
    if not plan:
        raise AuthError("Código inválido")
    with _lock, _conn() as c:
        if c.execute("SELECT 1 FROM codes_used WHERE code=?", (code,)).fetchone():
            raise AuthError("Esse código já foi usado")
        c.execute("INSERT INTO codes_used VALUES(?,?,?)", (code, u["id"], time.time()))
    return activate(u["id"], plan, "codigo")


def charge(u) -> str:
    """Desconta 1 corte da cota do plano (ou dos grátis). Devolve de onde saiu."""
    if is_admin(u):
        return "admin"
    p = active_plan(u)
    col = "credits" if p == "gratis" else "quota_left"
    with _lock, _conn() as c:
        if c.execute(f"UPDATE users SET {col}={col}-1 WHERE id=? AND {col}>0", (u["id"],)).rowcount == 0:
            raise AuthError("Seus cortes grátis acabaram. Escolha um plano pra continuar." if p == "gratis"
                            else f"Você usou todos os cortes do plano {PLANS[p]['name']}. Renove ou suba de plano.")
    return col


def refund(user_id: int, col: str):
    if col in ("credits", "quota_left"):
        with _lock, _conn() as c:
            c.execute(f"UPDATE users SET {col}={col}+1 WHERE id=?", (user_id,))


def add_job(job_id, user_id, title, charged_col):
    with _lock, _conn() as c:
        c.execute("INSERT INTO jobs VALUES(?,?,?,?,?)", (job_id, user_id, title, time.time(), charged_col or ""))


def job_owner(job_id):
    with _conn() as c:
        return c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def user_jobs(user_id, limit=80):
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT id,title,created FROM jobs WHERE user_id=? ORDER BY created DESC LIMIT ?", (user_id, limit))]


def user_stats(user_id):
    month = time.time() - 30 * 86400
    with _conn() as c:
        tot = c.execute("SELECT COUNT(*) FROM jobs WHERE user_id=?", (user_id,)).fetchone()[0]
        mon = c.execute("SELECT COUNT(*) FROM jobs WHERE user_id=? AND created>=?", (user_id, month)).fetchone()[0]
        pays = [dict(r) for r in c.execute("SELECT plan,amount,status,created FROM payments WHERE user_id=? ORDER BY created DESC LIMIT 24", (user_id,))]
    for p in pays:
        p["plan_name"] = PLANS.get(p["plan"], {}).get("name", p["plan"])
    return {"total": tot, "month": mon, "payments": pays}


# ---------------- pagamentos ----------------
def payment_upsert(pid: str, user_id: int, plan: str, amount: float, status: str, provider="mercadopago") -> bool:
    """Grava o pagamento. Devolve True se ele acabou de virar 'approved' (para liberar o plano uma vez só)."""
    with _lock, _conn() as c:
        old = c.execute("SELECT status FROM payments WHERE id=?", (pid,)).fetchone()
        if old:
            c.execute("UPDATE payments SET status=?, updated=? WHERE id=?", (status, time.time(), pid))
        else:
            c.execute("INSERT INTO payments VALUES(?,?,?,?,?,?,?,?)", (pid, user_id, plan, amount, status, provider, time.time(), time.time()))
        return status == "approved" and (not old or old["status"] != "approved")


# ---------------- admin ----------------
def admin_stats():
    now = time.time()
    with _conn() as c:
        q = lambda s, *a: c.execute(s, a).fetchone()[0]
        return {"usuarios": q("SELECT COUNT(*) FROM users WHERE phone_verified=1"),
                "novos_24h": q("SELECT COUNT(*) FROM users WHERE created>?", now - 86400),
                "assinantes_ativos": q("SELECT COUNT(*) FROM users WHERE plan<>'gratis' AND plan_until>?", now),
                "cortes_total": q("SELECT COUNT(*) FROM jobs"), "cortes_24h": q("SELECT COUNT(*) FROM jobs WHERE created>?", now - 86400),
                "receita_total": round(q("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='approved'"), 2),
                "receita_30d": round(q("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='approved' AND created>?", now - 30 * 86400), 2)}


def admin_users(search="", limit=100):
    with _conn() as c:
        rows = c.execute("""SELECT u.*, (SELECT COUNT(*) FROM jobs j WHERE j.user_id=u.id) AS cortes FROM users u
                            WHERE u.email LIKE ? OR u.name LIKE ? OR u.phone LIKE ? ORDER BY u.created DESC LIMIT ?""",
                         (f"%{search}%", f"%{search}%", f"%{search}%", limit)).fetchall()
    return [{**public(r), "id": r["id"], "phone_full": r["phone"], "verified": bool(r["phone_verified"]), "cortes": r["cortes"],
             "created": r["created"], "last_login": r["last_login"]} for r in rows]


def admin_payments(limit=100):
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT p.*, u.email FROM payments p LEFT JOIN users u ON u.id=p.user_id ORDER BY p.created DESC LIMIT ?", (limit,))]


def admin_add_credits(user_id: int, n: int):
    with _lock, _conn() as c:
        u = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        col = "credits" if active_plan(u) == "gratis" else "quota_left"
        c.execute(f"UPDATE users SET {col}=MAX(0,{col}+?) WHERE id=?", (int(n), user_id))
    return public(_user(user_id))


# ---------------- CRM do painel ----------------
STAGES = ["novo", "contatado", "negociando", "cliente", "perdido"]


def crm_rows():
    with _conn() as c:
        rows = c.execute("""SELECT u.*,
            (SELECT COUNT(*) FROM jobs j WHERE j.user_id=u.id) AS cortes,
            (SELECT MAX(created) FROM jobs j WHERE j.user_id=u.id) AS ultimo_corte,
            (SELECT COALESCE(SUM(amount),0) FROM payments p WHERE p.user_id=u.id AND p.status='approved') AS pago,
            (SELECT MAX(created) FROM payments p WHERE p.user_id=u.id AND p.status='approved') AS ultimo_pagamento,
            k.stage AS stage, k.notes AS notes
            FROM users u LEFT JOIN crm k ON k.key='u:'||u.id ORDER BY u.created DESC""").fetchall()
        extra = {r["key"]: dict(r) for r in c.execute("SELECT * FROM crm WHERE key LIKE 'w:%'")}
    out = []
    now = time.time()
    for r in rows:
        p = active_plan(r)
        auto = "cliente" if (r["pago"] or 0) > 0 and p != "gratis" else "novo"
        out.append({"key": f"u:{r['id']}", "tipo": "cadastro", "user_id": r["id"], "name": r["name"], "email": r["email"], "phone": r["phone"],
                    "plan": p, "plan_name": PLANS[p]["name"], "plan_until": r["plan_until"] if p != "gratis" else None,
                    "left": r["credits"] if p == "gratis" else r["quota_left"], "cortes": r["cortes"], "ultimo_corte": r["ultimo_corte"],
                    "pago": round(r["pago"] or 0, 2), "ultimo_pagamento": r["ultimo_pagamento"], "created": r["created"],
                    "last_login": r["last_login"], "stage": r["stage"] or auto, "notes": r["notes"] or "",
                    "quente": p == "gratis" and (r["cortes"] or 0) >= 2 and (r["created"] or 0) > now - 14 * 86400})
    return out, extra


def crm_set(key: str, stage: str | None, notes: str | None):
    if stage is not None and stage not in STAGES:
        raise AuthError("Etapa inválida")
    with _lock, _conn() as c:
        cur = c.execute("SELECT * FROM crm WHERE key=?", (key,)).fetchone()
        st = stage if stage is not None else (cur["stage"] if cur else "novo")
        nt = (notes if notes is not None else (cur["notes"] if cur else ""))[:2000]
        c.execute("INSERT INTO crm(key,stage,notes,updated) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET stage=excluded.stage, notes=excluded.notes, updated=excluded.updated",
                  (key, st, nt, time.time()))
    return {"ok": True}
