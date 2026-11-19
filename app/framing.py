"""Enquadramento automático: acha a facecam do streamer e o ponto de ação do vídeo.

Como funciona:
- Tira alguns quadros espalhados pelo trecho do corte.
- Um detector de rosto (YuNet, roda no próprio servidor) procura rostos em cada quadro.
- O rosto da facecam fica parado no mesmo lugar o tempo todo; rostos de personagens do jogo se mexem.
  Por isso fica valendo o rosto que aparece no mesmo ponto na maioria dos quadros.
- Com o rosto achado, a área da câmera é montada em volta dele, já no formato do espaço do template.
"""
import logging
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("uvicorn.error")
MODEL = Path(__file__).parent / "models" / "face_yunet.onnx"
N_FRAMES = 9
SCAN_W = 960  # largura usada na detecção (mais leve que o vídeo original)

try:
    import cv2
    import numpy as np
except Exception:  # sem OpenCV o corte segue com o enquadramento padrão
    cv2 = None


def _frames(src: Path, start: float, dur: float, out: Path) -> list[Path]:
    files = []
    for i in range(N_FRAMES):
        t = start + dur * (i + 0.5) / N_FRAMES
        f = out / f"q{i}.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
                        "-vf", f"scale={SCAN_W}:-2", "-q:v", "3", str(f)],
                       capture_output=True, timeout=60)
        if f.exists() and f.stat().st_size > 0:
            files.append(f)
    return files


def _faces(img) -> list[tuple]:
    h, w = img.shape[:2]
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (w, h), 0.72, 0.3, 50)
    _, faces = det.detect(img)
    if faces is None:
        return []
    # (x, y, w, h, nota) normalizados pelo tamanho do quadro
    return [(f[0] / w, f[1] / h, f[2] / w, f[3] / h, float(f[14])) for f in faces if f[2] / w > 0.018]


def analyze(src: Path, start: float, end: float) -> dict:
    """Devolve {"cam": (x, y, w, h) do rosto da facecam ou None, "focus": x do centro da ação (0..1), "aspect": w/h}."""
    out = {"cam": None, "focus": 0.5}
    if cv2 is None or not MODEL.exists():
        return out
    dur = max(1.0, (end or start + 10) - (start or 0))
    with tempfile.TemporaryDirectory() as td:
        files = _frames(src, start or 0.0, dur, Path(td))
        if not files:
            return out
        dets = []
        for i, f in enumerate(files):
            img = cv2.imread(str(f))
            if img is None:
                continue
            out["aspect"] = img.shape[1] / img.shape[0]
            dets += [(i, *d) for d in _faces(img)]
    if not dets:
        return out
    n = len(files)
    # agrupa rostos que aparecem no mesmo lugar em quadros diferentes
    groups = []
    for d in dets:
        cx, cy = d[1] + d[3] / 2, d[2] + d[4] / 2
        for g in groups:
            if abs(g["cx"] - cx) < 0.04 and abs(g["cy"] - cy) < 0.06 and 0.6 < d[3] / g["w"] < 1.6:
                g["items"].append(d)
                g["frames"].add(d[0])
                break
        else:
            groups.append({"cx": cx, "cy": cy, "w": d[3], "items": [d], "frames": {d[0]}})
    best = max(groups, key=lambda g: (len(g["frames"]), sum(x[5] for x in g["items"])))
    hits = len(best["frames"])
    xs = [x[1] for x in best["items"]]; ys = [x[2] for x in best["items"]]
    ws = [x[3] for x in best["items"]]; hs = [x[4] for x in best["items"]]
    face = (float(np.median(xs)), float(np.median(ys)), float(np.median(ws)), float(np.median(hs)))
    big = face[2] > 0.16  # rosto grande: é uma live de câmera (IRL), não facecam no canto
    if hits >= max(3, int(n * 0.5)) and not big:
        out["cam"] = face
    if big and hits >= max(2, int(n * 0.4)):  # live de câmera: o corte vertical segue o rosto
        out["focus"] = min(max(face[0] + face[2] / 2, 0.0), 1.0)
        out["face_big"] = True
    log.info("Enquadramento: %s rosto(s), facecam=%s (em %s de %s quadros)", len(dets),
             "sim" if out["cam"] else "não", hits, n)
    return out


def cam_crop(face: tuple, slot_w: int, slot_h: int, src_aspect: float = 16 / 9, faces: float = 3.2) -> str:
    """Filtro ffmpeg de corte em volta do rosto da facecam, no formato exato do espaço do template."""
    fx, fy, fw, fh = face
    target = slot_w / slot_h  # formato do espaço (ex.: 1080x608)
    # largura do corte = ~3,2 rostos; altura sai do formato do espaço (em proporção do quadro)
    cw = min(1.0, fw * faces)
    ch = cw * src_aspect / target
    if ch > 1.0:
        ch = 1.0
        cw = ch * target / src_aspect
    cx = fx + fw / 2
    cy = fy + fh * 0.62  # deixa um pouco de espaço acima da cabeça
    x = min(max(cx - cw / 2, 0.0), 1.0 - cw)
    y = min(max(cy - ch / 2, 0.0), 1.0 - ch)
    return f"crop=iw*{cw:.4f}:ih*{ch:.4f}:iw*{x:.4f}:ih*{y:.4f},scale={slot_w}:{slot_h}"


def focus_crop(focus: float, w: int, h: int) -> str:
    """Corte vertical que segue o ponto de ação (em vez de sempre o meio da tela)."""
    return (f"scale=-2:{h},crop={w}:{h}:"
            f"'min(max(iw*{focus:.4f}-{w}/2,0),iw-{w})':0")
