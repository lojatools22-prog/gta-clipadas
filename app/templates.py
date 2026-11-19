"""Catálogo dos 36 templates (6 famílias x 6 variações) e 36 estilos de legenda (6 fontes x 6 animações)."""

# Cores no formato ASS (&HAABBGGRR) — paleta GTA6 Clips
C = {
    "rosa": "&H00A81FFF", "ciano": "&H00FFE13C", "amarelo": "&H0028C4FF", "laranja": "&H003278FF",
    "roxo": "&H00FF46BE", "verde": "&H0078E63C", "vermelho": "&H003C32FF", "branco": "&H00FFFFFF",
    "dourado": "&H0037AFD4", "preto": "&H00000000",
}
NEON_ASS = {"rosa": C["rosa"], "ciano": C["ciano"], "roxo": C["roxo"], "verde": C["verde"], "laranja": C["laranja"], "amarelo": C["amarelo"]}

FAMILIES = [
    ("basico", "Básicos"), ("fundo", "Fundo GTA"), ("vice", "Vice Beach"), ("neon", "Neon Night"), ("procurado", "Procurado"),
    ("missao", "Missão"), ("celular", "Celular"), ("retro", "Rádio / VHS"),
]


def _t(id, name, family, layout, overlays=(), title=(540, 608), caption=(540, 1400), active="amarelo", shadow="rosa",
       title_color="branco", title_shadow="rosa", cards=(), flash=(), fx=(), pro=True, title_prefix="", title_size=70):
    return {"id": id, "name": name, "family": family, "layout": layout, "overlays": list(overlays), "title": title,
            "caption": caption, "active": active, "shadow": shadow, "title_color": title_color,
            "title_shadow": title_shadow, "cards": list(cards), "flash": list(flash), "fx": list(fx), "pro": pro,
            "title_prefix": title_prefix, "title_size": title_size}


TEMPLATES = []
# ---- Básicos: corte limpo, só a legenda (sem arte por cima) ----
TEMPLATES += [
    {**_t("viral", "Viral: facecam + jogo", "basico", {"kind": "viral", "cam_h": 740}, title=(540, 740), caption=(540, 1500), shadow="preto", title_shadow="preto", pro=False, title_size=96), "title_style": "viral", "no_caps": True},
    {**_t("viral-legenda", "Viral + legenda", "basico", {"kind": "viral", "cam_h": 740}, title=(540, 740), caption=(540, 1520), shadow="preto", title_shadow="preto", pro=False, title_size=96), "title_style": "viral"},
    _t("capa-praia", "Capa GTA: praia", "basico", {"kind": "gtacapa", "bg": "praia", "cam": False}, title=(540, 500), caption=(540, 1430), shadow="preto", title_shadow="rosa", pro=False),
    _t("capa-cidade", "Capa GTA: cidade", "basico", {"kind": "gtacapa", "bg": "cidade", "cam": False}, title=(540, 500), caption=(540, 1430), shadow="preto", title_shadow="rosa", pro=False),
    _t("capa-estrada", "Capa GTA: estrada", "basico", {"kind": "gtacapa", "bg": "estrada", "cam": False}, title=(540, 500), caption=(540, 1430), shadow="preto", title_shadow="rosa", pro=False),
    _t("capa-cam-praia", "Capa GTA + facecam: praia", "basico", {"kind": "gtacapa", "bg": "praia"}, title=(540, 175), caption=(540, 1430), shadow="preto", title_shadow="rosa", pro=False),
    _t("capa-cam-cidade", "Capa GTA + facecam: cidade", "basico", {"kind": "gtacapa", "bg": "cidade"}, title=(540, 175), caption=(540, 1430), shadow="preto", title_shadow="rosa", pro=False),
    _t("capa-cam-estrada", "Capa GTA + facecam: estrada", "basico", {"kind": "gtacapa", "bg": "estrada"}, title=(540, 175), caption=(540, 1430), shadow="preto", title_shadow="rosa", pro=False),
    _t("basico-legenda", "Só legenda", "basico", {"kind": "crop"}, title=(540, 300), caption=(540, 1400), shadow="preto", title_shadow="preto", pro=False),
    _t("basico-desfocado", "Fundo desfocado", "basico", {"kind": "blur_plain"}, title=(540, 470), caption=(540, 1440), shadow="preto", title_shadow="preto", pro=False),
    _t("basico-facecam", "Facecam + jogo", "basico", {"kind": "cam_top"}, title=(540, 608), caption=(540, 1400), shadow="preto", title_shadow="preto", pro=False),
    _t("basico-faixas", "Faixas pretas", "basico", {"kind": "letterbox"}, title=(540, 560), caption=(540, 1390), shadow="preto", title_shadow="preto", pro=False),
    _t("basico-fundo-orla", "Fundo GTA: orla", "basico", {"kind": "gtabg", "bg": "aereo"}, ["gtabg_aereo"], title=(540, 470), caption=(540, 1390), shadow="preto", title_shadow="preto", pro=False),
    _t("basico-fundo-cidade", "Fundo GTA: cidade", "basico", {"kind": "gtabg", "bg": "skyline"}, ["gtabg_skyline"], title=(540, 470), caption=(540, 1390), shadow="preto", title_shadow="preto", pro=False),
    _t("basico-fundo-asfalto", "Fundo GTA: asfalto", "basico", {"kind": "gtabg", "bg": "asfalto"}, ["gtabg_asfalto"], title=(540, 470), caption=(540, 1390), shadow="preto", title_shadow="preto", pro=False),
]
# ---- Fundo GTA: o vídeo numa janela neon sobre uma cena da cidade ----
_g = dict(layout=None, title=(540, 470), caption=(540, 1390), shadow="preto", title_shadow="rosa")
for i, (k, label) in enumerate([("aereo", "Orla neon"), ("skyline", "Cidade à noite"), ("timelapse", "Avenida"),
                                ("carro", "Esportivo"), ("perseguicao", "Perseguição"), ("asfalto", "Asfalto molhado")]):
    TEMPLATES.append(_t(f"fundo-{k}", label, "fundo", {"kind": "gtabg", "bg": k}, [f"gtabg_{k}"],
                        **{kk: v for kk, v in _g.items() if kk != "layout"}, pro=i >= 2))
# ---- Vice Beach: varia o layout ----
TEMPLATES += [
    _t("vice-cam-cima", "Facecam em cima", "vice", {"kind": "cam_top"}, ["vb_top"], pro=False),
    _t("vice-cam-baixo", "Facecam embaixo", "vice", {"kind": "cam_bottom"}, ["vb_bottom"], title=(540, 1312), caption=(540, 760), pro=False),
    _t("vice-cam-flutuante", "Facecam flutuante", "vice", {"kind": "float"}, ["vb_float"], title=(540, 1560), caption=(540, 1220)),
    _t("vice-sem-cam", "Sem facecam (fundo desfocado)", "vice", {"kind": "blur"}, ["vb_blur"], title=(540, 445), caption=(540, 1450)),
    _t("vice-dividido", "Tela dividida 50/50", "vice", {"kind": "split"}, ["vb_split"], title=(540, 960), caption=(540, 1500)),
    _t("vice-moldura", "Moldura pôr do sol", "vice", {"kind": "moldura"}, ["vb_moldura"], title=(540, 250), caption=(540, 1450), title_size=64),
]
# ---- Neon Night: varia a cor ----
for i, (k, label) in enumerate([("rosa", "Rosa"), ("ciano", "Ciano"), ("roxo", "Roxo"), ("verde", "Verde"), ("laranja", "Laranja"), ("amarelo", "Amarelo")]):
    TEMPLATES.append(_t(f"neon-{k}", f"Neon {label}", "neon", {"kind": "cam_top"}, [f"neon_{k}"],
                        active=k if k != "amarelo" else "ciano", shadow="preto", title_shadow=k, pro=i >= 2))
# ---- Procurado: estrelas + sirene ----
for n in range(1, 6):
    TEMPLATES.append(_t(f"procurado-{n}", f"{n} estrela{'s' if n > 1 else ''}", "procurado", {"kind": "cam_top"}, [f"wanted_{n}"],
                        title=(540, 830), caption=(540, 1420), active="amarelo", shadow="vermelho", title_shadow="vermelho",
                        flash=[["siren_r", "siren_b", 0.8]] if n >= 3 else [], pro=n > 2))
TEMPLATES.append(_t("procurado-preso", "Preso! (grades no final)", "procurado", {"kind": "cam_top"}, ["wanted_busted"],
                    title=(540, 830), caption=(540, 1420), shadow="vermelho", title_shadow="vermelho",
                    flash=[["siren_r", "siren_b", 0.6]], cards=[{"at": "end", "text": "PRESO", "sub": "", "color": "vermelho"}], fx=["gray_end"]))
# ---- Missão: cartelas de início/fim ----
_m = dict(layout={"kind": "cam_top"}, title=(560, 625), caption=(540, 1400), title_size=50)
TEMPLATES += [
    _t("missao-cumprida", "Missão cumprida", "missao", overlays=["mission"], cards=[{"at": "end", "text": "MISSÃO CUMPRIDA", "sub": "+ RESPEITO", "color": "dourado"}], pro=False, **_m),
    _t("missao-falhou", "Missão falhou", "missao", overlays=["mission_red"], active="vermelho", title_shadow="vermelho", cards=[{"at": "end", "text": "MISSÃO FALHOU", "sub": "tente de novo", "color": "vermelho"}], fx=["gray_end"], **_m),
    _t("missao-nova", "Nova missão", "missao", overlays=["mission"], cards=[{"at": "start", "text": "NOVA MISSÃO", "sub": "", "color": "amarelo"}], **_m),
    _t("missao-chefao", "Chefão", "missao", overlays=["mission_red"], active="vermelho", title_shadow="vermelho", cards=[{"at": "start", "text": "CHEFÃO", "sub": "", "color": "vermelho"}], **_m),
    _t("missao-dinheiro", "Dinheiro na conta", "missao", overlays=["mission_green"], active="verde", title_shadow="verde", cards=[{"at": "end", "text": "+ R$ 1.000.000", "sub": "", "color": "verde"}], **_m),
    _t("missao-respeito", "Respeito +", "missao", overlays=["mission"], cards=[{"at": "end", "text": "RESPEITO +", "sub": "", "color": "rosa"}], **_m),
]
# ---- Celular: o corte dentro de um celular ----
_p = dict(layout={"kind": "inset", "rect": [150, 300, 930, 1680], "bg": "none"})
TEMPLATES += [
    _t("celular-chat", "Mensagem", "celular", overlays=["phone_chat"], title=(540, 1455), caption=(540, 1050), title_color="preto", title_shadow="branco", title_size=48, pro=False, **_p),
    _t("celular-chamada", "Chamada", "celular", overlays=["phone_chamada"], title=(540, 640), caption=(540, 1150), title_size=48, **_p),
    _t("celular-rede", "Rede social", "celular", overlays=["phone_social"], title=(500, 1560), caption=(540, 1050), title_size=44, **_p),
    _t("celular-gps", "GPS", "celular", overlays=["phone_gps"], title=(540, 500), caption=(600, 1050), title_size=48, **_p),
    _t("celular-foto", "Câmera", "celular", overlays=["phone_foto"], title=(540, 1360), caption=(540, 1000), title_size=48, **_p),
    _t("celular-pix", "Pix recebido", "celular", overlays=["phone_banco"], title=(540, 1450), caption=(540, 1050), active="verde", title_size=48, **_p),
]
# ---- Rádio / VHS ----
TEMPLATES += [
    _t("retro-vhs", "Fita VHS", "retro", {"kind": "crop"}, ["vhs"], title=(540, 300), caption=(540, 1400), fx=["vhs"], pro=False),
    _t("retro-radio", "Rádio do carro", "retro", {"kind": "cam_top"}, ["radio"], title=(690, 590), caption=(540, 1400), active="laranja", title_shadow="laranja", title_size=44),
    _t("retro-fita", "Fita cassete", "retro", {"kind": "blur"}, ["fita"], title=(540, 300), caption=(540, 1460), title_color="preto", title_shadow="branco", title_size=50),
    _t("retro-tv", "TV antiga", "retro", {"kind": "inset", "rect": [110, 640, 970, 1126], "bg": "none"}, ["tv"], title=(540, 300), caption=(540, 1560), fx=["crt"], title_size=64),
    _t("retro-outdoor", "Outdoor", "retro", {"kind": "inset", "rect": [80, 560, 1000, 1078], "bg": "none"}, ["outdoor"], title=(540, 330), caption=(540, 1300), title_size=64),
    _t("retro-revista", "Capa de revista", "retro", {"kind": "crop"}, ["revista"], title=(540, 1520), caption=(540, 1100), title_size=70),
]
assert len(TEMPLATES) == 57, len(TEMPLATES)
# Níveis: 0 = grátis, 1 = Iniciante, 2 = Clipador/Pro/Passe
TIER1_T = {"vice-cam-flutuante", "vice-sem-cam", "neon-roxo", "neon-verde", "procurado-3", "missao-falhou", "missao-nova", "celular-chamada", "retro-tv"}
for t in TEMPLATES:
    t["tier"] = 0 if not t["pro"] else (1 if t["id"] in TIER1_T else 2)
LAYOUT_DESC = {"cam_top": "Facecam em cima, jogo embaixo", "cam_bottom": "Jogo em cima, facecam embaixo",
               "float": "Jogo em tela cheia, facecam no canto", "blur": "Vídeo inteiro, fundo desfocado",
               "blur_plain": "Vídeo inteiro, fundo desfocado", "split": "Tela dividida 50/50", "crop": "Tela cheia",
               "moldura": "Vídeo com moldura", "inset": "Vídeo dentro de um objeto", "letterbox": "Vídeo inteiro, faixas pretas", "gtabg": "Vídeo inteiro numa janela neon", "gtacapa": "Arte estilo GTA com o jogo de ponta a ponta", "viral": "Facecam grande em cima, jogo embaixo, título em faixa vermelha"}
FAMILY_FX = {"basico": "só legenda, sem efeitos", "fundo": "fundo de cidade estilo GTA", "vice": "arte de praia Vice City", "neon": "borda neon",
             "procurado": "estrelas de procurado", "missao": "cartela de missão", "celular": "dentro de um celular",
             "retro": "efeito retrô"}
for t in TEMPLATES:
    fx = FAMILY_FX[t["family"]]
    if t["family"] == "procurado" and t["flash"]:
        fx += " + sirene"
    if t["family"] == "missao" and t["cards"]:
        fx = f"cartela \"{t['cards'][0]['text'].title()}\" no {'início' if t['cards'][0]['at'] == 'start' else 'final'}"
    if t["family"] in ("celular", "retro"):
        fx = {"celular": "dentro da tela de um celular", "retro": "efeito retrô"}[t["family"]]
    t["desc"] = f"{LAYOUT_DESC[t['layout']['kind']]} · {fx}"
    t["effects"] = t["family"] != "basico"
BY_ID = {t["id"]: t for t in TEMPLATES}

# ---------------- Legendas ----------------
FONTS = [
    ("pesada", "Pesada (estilo logo)", "Bowlby One", 92, True),
    ("neon", "Neon cursiva", "Kaushan Script", 116, False),
    ("condensada", "Condensada", "Anton", 128, True),
    ("redonda", "Redonda", "Fredoka One", 108, True),
    ("maquina", "Máquina de escrever", "Special Elite", 98, True),
    ("grafite", "Grafite", "Permanent Marker", 106, True),
]
ANIMS = [
    ("karaoke", "Palavra acende"), ("pop", "Pop"), ("quique", "Uma por vez (quique)"),
    ("tremida", "Tremida"), ("digitando", "Digitando"), ("lateral", "Entra de lado"),
]
CAPTION_STYLES = []
for fi, (fk, fl, family, size, upper) in enumerate(FONTS):
    for ai, (ak, al) in enumerate(ANIMS):
        CAPTION_STYLES.append({"id": f"{fk}-{ak}", "name": f"{fl} · {al}", "font": family, "size": size, "upper": upper,
                               "anim": ak, "font_key": fk, "pro": not (fi < 2 and ai < 2)})
TIER1_C = {"pesada-quique", "pesada-tremida", "neon-quique", "neon-tremida", "condensada-karaoke", "condensada-pop", "redonda-karaoke", "redonda-pop"}
for c in CAPTION_STYLES:
    c["tier"] = 0 if not c["pro"] else (1 if c["id"] in TIER1_C else 2)
CAPTION_BY_ID = {c["id"]: c for c in CAPTION_STYLES}
CAPTION_COLORS = {"auto": "Cor do template", "amarelo": "Amarelo", "rosa": "Rosa", "ciano": "Ciano", "verde": "Verde", "branco": "Branco"}


def public_catalog():
    return {
        "families": [{"id": f, "name": n} for f, n in FAMILIES],
        "templates": [{**{k: t[k] for k in ("id", "name", "family", "pro", "tier", "desc", "effects")},
                       "cam": t["layout"]["kind"] in ("cam_top", "cam_bottom", "float", "split")} for t in TEMPLATES],
        "captions": [{k: c[k] for k in ("id", "name", "font_key", "anim", "pro", "tier")} for c in CAPTION_STYLES],
        "caption_fonts": [{"id": f[0], "name": f[1]} for f in FONTS],
        "caption_anims": [{"id": a[0], "name": a[1]} for a in ANIMS],
        "caption_colors": CAPTION_COLORS,
    }
