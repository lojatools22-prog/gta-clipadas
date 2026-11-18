"""Gera o arquivo de legenda .ass: título-gancho, legenda animada e cartelas de início/fim."""
from .templates import C

W, H = 1080, 1920


def ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def esc(s: str) -> str:
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _groups(words, n):
    out, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) == n or w["word"].rstrip().endswith((".", "!", "?", ",")):
            out.append(cur); cur = []
    if cur:
        out.append(cur)
    return out


def _end_of(groups, gi, i):
    g = groups[gi]
    if i + 1 < len(g):
        return g[i + 1]["start"]
    nxt = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else g[i]["end"] + 0.4
    return max(g[i]["start"] + 0.08, min(g[i]["end"] + 0.35, nxt))


def build(words, duration, tpl, cap, color_key="auto", title="", wm="") -> str:
    active = C[tpl["active"]] if color_key in ("auto", None, "") else C.get(color_key, C["amarelo"])
    shadow = C[tpl["shadow"]]
    tcolor, tshadow = C[tpl["title_color"]], C[tpl["title_shadow"]]
    tbox = C["rosa"] if tpl["title_shadow"] in ("preto", "branco") else tshadow  # manchete: caixa colorida atrás do título
    (tx, ty), (cx, cy) = tpl["title"], tpl["caption"]
    fax = "\\fax-0.12" if cap["font_key"] in ("pesada", "condensada", "redonda") else ""
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{cap['font']},{cap['size']},&H00FFFFFF,&H000000FF,&H00000000,{shadow},0,0,0,0,100,100,0,0,1,7,6,5,70,70,0,1
Style: Title,Bowlby One,{int(tpl['title_size'] * 1.12)},&H00FFFFFF,&H000000FF,{tbox},&H00000000,0,0,0,0,100,100,0,0,3,16,0,5,70,70,0,1
Style: Card,Bowlby One,110,&H00FFFFFF,&H000000FF,&H00000000,&H00A81FFF,0,0,0,0,100,100,1,0,1,9,8,5,40,40,0,1
Style: Wm,Anton,58,&H22FFFFFF,&H000000FF,&H00000000,&H66000000,1,0,0,0,100,100,1,0,1,3,2,9,40,40,48,1
Style: Sub,Bowlby One,56,&H00FFFFFF,&H000000FF,&H00000000,&H00A81FFF,0,0,0,0,100,100,0,0,1,6,5,5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if wm.strip():  # marca d'água em texto (@ do clipador), em cima à direita o vídeo todo
        ev.append(f"Dialogue: 3,{ts(0)},{ts(duration)},Wm,,0,0,0,,{esc(wm.strip()[:30])}")
    if title.strip():
        txt = esc((tpl.get("title_prefix", "") + title).upper())
        ev.append(f"Dialogue: 2,{ts(0)},{ts(duration)},Title,,0,0,0,,{{\\pos({tx},{ty})\\fad(200,0)\\fax-0.10}}{txt}")

    up = (lambda s: s.upper()) if cap["upper"] else (lambda s: s)
    anim = cap["anim"]
    groups = _groups(words, 1 if anim == "quique" else 3)
    for gi, g in enumerate(groups):
        for i, w in enumerate(g):
            st, en = w["start"], _end_of(groups, gi, i)
            words_txt = [esc(up(x["word"].strip())) for x in g]
            hl = lambda j, t: f"{{\\c{active}\\fscx112\\fscy112}}{t}{{\\r}}" if j == i else t
            pos = f"\\pos({cx},{cy}){fax}"
            if anim == "karaoke":
                body = " ".join(hl(j, t) for j, t in enumerate(words_txt))
            elif anim == "pop":
                parts = [t if j < i else (f"{{\\c{active}\\fscx140\\fscy140\\t(0,130,\\fscx100\\fscy100)}}{t}{{\\r}}" if j == i else "") for j, t in enumerate(words_txt)]
                body = " ".join(p for p in parts if p)
            elif anim == "quique":
                pos = f"\\move({cx},{cy + 60},{cx},{cy},0,140){fax}\\fscx125\\fscy125\\t(0,160,\\fscx100\\fscy100)"
                body = f"{{\\c{active}}}{words_txt[i]}"
            elif anim == "tremida":
                pos += "\\t(0,50,\\frz-5)\\t(50,100,\\frz4)\\t(100,150,\\frz-3)\\t(150,200,\\frz0)"
                body = " ".join(hl(j, t) for j, t in enumerate(words_txt))
            elif anim == "digitando":
                body = " ".join(t for t in words_txt[:i + 1])
                body = body[: -len(words_txt[i])] + f"{{\\c{active}}}{words_txt[i]}{{\\r}}" if words_txt[i] else body
            else:  # lateral
                if i == 0:
                    pos = f"\\move({cx - 420},{cy},{cx},{cy},0,160){fax}"
                body = " ".join(hl(j, t) for j, t in enumerate(words_txt))
            ev.append(f"Dialogue: 1,{ts(st)},{ts(en)},Cap,,0,0,0,,{{{pos}}}{body}")

    for card in tpl["cards"]:
        col = C.get(card["color"], C["amarelo"])
        if card["at"] == "end":
            s, e = max(0.0, duration - 1.8), duration
        else:
            s, e = 0.0, min(1.6, duration)
        ev.append(f"Dialogue: 3,{ts(s)},{ts(e)},Card,,0,0,0,,{{\\pos(540,900)\\c{col}\\fax-0.10\\fad(120,120)\\fscx135\\fscy135\\t(0,220,\\fscx100\\fscy100)}}{card['text']}")
        if card.get("sub"):
            ev.append(f"Dialogue: 3,{ts(s + 0.25)},{ts(e)},Sub,,0,0,0,,{{\\pos(540,1050)\\fax-0.10\\fad(120,120)}}{card['sub'].upper()}")
    return head + "\n".join(ev) + "\n"
