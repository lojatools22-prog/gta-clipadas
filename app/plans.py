"""Planos do GTA6 Clips. Preço em reais, cota de cortes por período e o que cada um libera."""
import os

MB_BASE = int(os.getenv("MAX_UPLOAD_MB", "1024"))      # limite de vídeo enviado nos planos comuns
MB_PRO = int(os.getenv("MAX_UPLOAD_MB_PRO", "3072"))   # limite no Pro

PLANS = {
    "gratis":    {"name": "Grátis",      "price": 0.0,   "quota": int(os.getenv("FREE_CLIPS", "3")), "days": 0,  "tier": 0, "max_mb": MB_BASE, "priority": 0, "batch": False, "watermark_off": False, "own_logo": False, "auto_moments": False},
    "iniciante": {"name": "Iniciante",   "price": 19.90, "quota": 30,  "days": 30, "tier": 1, "max_mb": MB_BASE, "priority": 1, "batch": False, "watermark_off": True, "own_logo": False, "auto_moments": False},
    "clipador":  {"name": "Clipador",    "price": 39.90, "quota": 150, "days": 30, "tier": 2, "max_mb": MB_BASE, "priority": 2, "batch": False, "watermark_off": True, "own_logo": True, "auto_moments": True},
    "pro":       {"name": "Pro",         "price": 79.90, "quota": 300, "days": 30, "tier": 2, "max_mb": MB_PRO, "priority": 3, "batch": True,  "watermark_off": True, "own_logo": True, "auto_moments": True},
    "passe":     {"name": "Passe GTA 6", "price": 9.90,  "quota": 10,  "days": 7,  "tier": 2, "max_mb": MB_BASE, "priority": 1, "batch": False, "watermark_off": True, "own_logo": False, "auto_moments": False},
}
PAID = [k for k in PLANS if k != "gratis"]


def public():
    return {k: {kk: v[kk] for kk in ("name", "price", "quota", "days", "tier", "max_mb", "batch")} for k, v in PLANS.items()}
