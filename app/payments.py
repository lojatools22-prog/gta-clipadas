"""Mercado Pago (Checkout Pro: Pix, cartão e boleto). O plano é liberado pelo webhook, depois de conferir o pagamento na API."""
import hashlib, hmac, logging, os
import httpx

from .plans import PLANS

log = logging.getLogger("payments")
MP_TOKEN = os.getenv("MP_ACCESS_TOKEN", "")
MP_SECRET = os.getenv("MP_WEBHOOK_SECRET", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
API = "https://api.mercadopago.com"


# ---------- Asaas (Pix, cartão e boleto na fatura do Asaas) ----------
ASAAS_KEY = os.getenv("ASAAS_API_KEY", "")
ASAAS_TOKEN = os.getenv("ASAAS_WEBHOOK_TOKEN", "")
ASAAS_API = "https://api-sandbox.asaas.com/v3" if os.getenv("ASAAS_ENV", "production") == "sandbox" else "https://api.asaas.com/v3"
PROVIDER = os.getenv("PAYMENT_PROVIDER") or ("asaas" if ASAAS_KEY else "mercadopago")


def ready() -> bool:
    if PROVIDER == "asaas":
        return bool(ASAAS_KEY and PUBLIC_URL)
    return bool(MP_TOKEN and PUBLIC_URL)


def valid_cpf(cpf: str) -> str:
    d = "".join(ch for ch in cpf or "" if ch.isdigit())
    if len(d) == 14:  # CNPJ: confere só o tamanho
        return d
    if len(d) != 11 or d == d[0] * 11:
        raise ValueError("CPF inválido")
    for n in (9, 10):
        s = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        if (s * 10 % 11) % 10 != int(d[n]):
            raise ValueError("CPF inválido")
    return d


def _asaas(method, path, **kw):
    r = httpx.request(method, ASAAS_API + path, headers={"access_token": ASAAS_KEY, "User-Agent": "gtaclipadas"}, timeout=20, **kw)
    if r.status_code >= 300:
        log.error("Asaas %s %s falhou: %s %s", method, path, r.status_code, r.text[:400])
        raise RuntimeError("Não deu pra abrir o pagamento agora. Tente de novo.")
    return r.json()


def asaas_customer(user, cpf: str) -> str:
    c = _asaas("POST", "/customers", json={"name": user["name"], "email": user["email"], "cpfCnpj": valid_cpf(cpf),
                                            "mobilePhone": user["phone"][-11:], "externalReference": str(user["id"]),
                                            "notificationDisabled": True})
    return c["id"]


def asaas_checkout(user, plan: str, customer_id: str) -> str:
    import datetime as _dt
    P = PLANS[plan]
    body = {"customer": customer_id, "billingType": "UNDEFINED", "value": P["price"],
            "dueDate": (_dt.date.today() + _dt.timedelta(days=1)).isoformat(),
            "description": f"GTA Clipadas — plano {P['name']}", "externalReference": f"{user['id']}:{plan}",
            "callback": {"successUrl": f"{PUBLIC_URL}/?pagamento=success#planos", "autoRedirect": True}}
    try:
        pay = _asaas("POST", "/payments", json=body)
    except RuntimeError:
        # o retorno automático exige o domínio cadastrado no Asaas; sem ele, cria a cobrança sem retorno
        body.pop("callback")
        pay = _asaas("POST", "/payments", json=body)
    return pay["invoiceUrl"]


def asaas_payment(pid: str) -> dict:
    return _asaas("GET", f"/payments/{pid}")


def create_checkout(user, plan: str) -> str:
    P = PLANS[plan]
    body = {
        "items": [{"id": plan, "title": f"GTA Clipadas — plano {P['name']}", "quantity": 1, "currency_id": "BRL", "unit_price": P["price"]}],
        "payer": {"email": user["email"], "name": user["name"]},
        "external_reference": f"{user['id']}:{plan}",
        "notification_url": f"{PUBLIC_URL}/api/pay/webhook",
        "back_urls": {k: f"{PUBLIC_URL}/?pagamento={k}#planos" for k in ("success", "pending", "failure")},
        "auto_return": "approved",
        "statement_descriptor": "GTACLIPADAS",
    }
    r = httpx.post(f"{API}/checkout/preferences", json=body, headers={"Authorization": f"Bearer {MP_TOKEN}"}, timeout=20)
    if r.status_code >= 300:
        log.error("MP preferência falhou: %s %s", r.status_code, r.text[:400])
        raise RuntimeError("Não deu pra abrir o pagamento agora. Tente de novo.")
    return r.json()["init_point"]


def valid_signature(headers: dict, data_id: str) -> bool:
    """Confere a assinatura x-signature do Mercado Pago (quando o segredo está configurado)."""
    if not MP_SECRET:
        return True  # sem segredo, a segurança vem de reconsultar o pagamento na API
    sig = headers.get("x-signature", "")
    parts = dict(p.split("=", 1) for p in sig.split(",") if "=" in p)
    ts, v1 = parts.get("ts", ""), parts.get("v1", "")
    manifest = f"id:{data_id.lower()};request-id:{headers.get('x-request-id', '')};ts:{ts};"
    expected = hmac.new(MP_SECRET.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return bool(v1) and hmac.compare_digest(expected, v1)


def fetch_payment(pid: str) -> dict:
    r = httpx.get(f"{API}/v1/payments/{pid}", headers={"Authorization": f"Bearer {MP_TOKEN}"}, timeout=20)
    r.raise_for_status()
    return r.json()
