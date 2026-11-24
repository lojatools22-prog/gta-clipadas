"""Envio de SMS (Twilio) e e-mail (Resend). Sem chaves configuradas, roda em modo de teste e só registra no log."""
import logging, os
import httpx

log = logging.getLogger("notify")
TW_SID, TW_TOKEN, TW_FROM = os.getenv("TWILIO_ACCOUNT_SID", ""), os.getenv("TWILIO_AUTH_TOKEN", ""), os.getenv("TWILIO_FROM", "")
RESEND_KEY = os.getenv("RESEND_API_KEY", "").strip().strip("\"'")
# Só o domínio verificado no Resend pode enviar; um MAIL_FROM antigo de outro domínio é ignorado.
MAIL_FROM = os.getenv("MAIL_FROM", "").strip()
if "@gta6clipadas.com" not in MAIL_FROM:
    MAIL_FROM = "GTA Clipadas <nao-responda@gta6clipadas.com>"


def sms_ready() -> bool:
    return bool(TW_SID and TW_TOKEN and TW_FROM)


def email_ready() -> bool:
    return bool(RESEND_KEY)


def send_sms(phone: str, text: str) -> bool:
    if not sms_ready():
        log.warning("[SMS modo teste] %s: %s", phone, text)
        return False
    r = httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{TW_SID}/Messages.json", auth=(TW_SID, TW_TOKEN),
                   data={"To": phone, "From": TW_FROM, "Body": text}, timeout=15)
    if r.status_code >= 300:
        log.error("Twilio falhou %s: %s", r.status_code, r.text[:300])
        raise RuntimeError("Não deu pra enviar o SMS agora. Tente de novo em instantes.")
    return True


def send_email(to: str, subject: str, html: str, text: str = "") -> bool:
    if not email_ready():
        log.warning("[E-mail modo teste] %s | %s | %s", to, subject, html)
        return False
    r = httpx.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {RESEND_KEY}"},
                   json={"from": MAIL_FROM, "to": [to], "subject": subject, "html": html, **({"text": text} if text else {})}, timeout=15)
    if r.status_code >= 300:
        log.error("Resend falhou %s: %s", r.status_code, r.text[:300])
        raise RuntimeError("Não deu pra enviar o e-mail agora. Tente de novo em instantes.")
    return True
