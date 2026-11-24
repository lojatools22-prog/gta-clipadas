"""Proxy local sem senha que repassa tudo pro proxy residencial (com a senha).
O ffmpeg manda a autenticação de um jeito que alguns provedores (Geonode) recusam no túnel HTTPS (erro 568).
Com este repasse, o ffmpeg e o yt-dlp falam com 127.0.0.1 e quem autentica é o Python, do jeito certo."""
import base64, logging, socket, threading
from urllib.parse import urlsplit, unquote

log = logging.getLogger("uvicorn.error")


def _pipe(a, b):
    try:
        while True:
            d = a.recv(65536)
            if not d:
                break
            b.sendall(d)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class Tunnel:
    def __init__(self, upstream: str):
        u = urlsplit(upstream)
        self.host, self.port = u.hostname, u.port or 80
        cred = f"{unquote(u.username or '')}:{unquote(u.password or '')}"
        self.auth = "Basic " + base64.b64encode(cred.encode()).decode()
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(64)
        self.url = f"http://127.0.0.1:{self.srv.getsockname()[1]}"
        self.alive = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self.alive:
            try:
                c, _ = self.srv.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(c,), daemon=True).start()

    def _handle(self, c):
        try:
            head = b""
            while b"\r\n\r\n" not in head and len(head) < 65536:
                d = c.recv(4096)
                if not d:
                    return
                head += d
            top, rest = head.split(b"\r\n\r\n", 1)
            lines = top.decode("latin-1").split("\r\n")
            lines = [l for l in lines if not l.lower().startswith("proxy-authorization:")]
            lines.insert(1, f"Proxy-Authorization: {self.auth}")
            up = socket.create_connection((self.host, self.port), timeout=30)
            up.settimeout(None)
            up.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + rest)
            threading.Thread(target=_pipe, args=(up, c), daemon=True).start()
            _pipe(c, up)
        except Exception as e:
            log.warning("Túnel do proxy: %s", type(e).__name__)
            try:
                c.close()
            except OSError:
                pass

    def close(self):
        self.alive = False
        try:
            self.srv.close()
        except OSError:
            pass
