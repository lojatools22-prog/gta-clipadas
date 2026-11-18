"""Planos do GTA Clipadas. Preço em reais, cota de cortes por período e o que cada um libera."""
import os

PLANS = {
    "gratis":    {"name": "Grátis",      "price": 0.0,   "quota": int(os.getenv("FREE_CLIPS", "3")), "days": 0,  "tier": 0, "max_mb": 1024, "priority": 0, "batch": False, "watermark_off": False},
    "iniciante": {"name": "Iniciante",   "price": 19.90, "quota": 30,  "days": 30, "tier": 1, "max_mb": 1024, "priority": 0, "batch": False, "watermark_off": True},
    "clipador":  {"name": "Clipador",    "price": 39.90, "quota": 150, "days": 30, "tier": 2, "max_mb": 1024, "priority": 0, "batch": False, "watermark_off": True},
    "pro":       {"name": "Pro",         "price": 79.90, "quota": 300, "days": 30, "tier": 2, "max_mb": 3072, "priority": 1, "batch": True,  "watermark_off": True},
    "passe":     {"name": "Passe GTA 6", "price": 9.90,  "quota": 30,  "days": 7,  "tier": 2, "max_mb": 1024, "priority": 0, "batch": False, "watermark_off": True},
}
PAID = [k for k in PLANS if k != "gratis"]


def public():
    return {k: {kk: v[kk] for kk in ("name", "price", "quota", "days", "tier", "max_mb", "batch")} for k, v in PLANS.items()}
