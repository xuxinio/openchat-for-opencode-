#!/usr/bin/env python3
"""OpenCode Chat v6: interfaz web tipo chat sobre `opencode serve`.

Sin dependencias (solo librería estándar). Uso:

    python3 opencode_chat.py --setup    # la primera vez: pregunta y guarda la configuración
    python3 opencode_chat.py            # arranca

- La configuración vive en ~/.config/opencode-chat/config.json (permisos 600) y
  se puede cambiar desde la web (botón ⚙). Las variables de entorno, si están,
  mandan sobre el archivo: HOST, PORT, OC_ROOT, OPENCODE_CHAT_TOKEN, NTFY_URL,
  NTFY_TOKEN, NTFY_MIN_SECS, NTFY_DETAIL=1, NTFY_CLICK, UPLOAD_MAX_MB, DOWNLOAD_MAX_MB.
- Si `opencode serve` no está corriendo, lo levanta solo (127.0.0.1:4096).
- La carpeta raíz limita qué carpetas se pueden explorar/usar como proyecto.

Subir/descargar solo funciona dentro de OC_ROOT y nunca toca archivos o
carpetas ocultos (.env, .git, .ssh, .config…) ni claves .pem/.key.
"""
import base64
import hmac
import http.client
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "6.0"
CONF_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "opencode-chat"
CONF_FILE = CONF_DIR / "config.json"
DEFAULTS = {"host": "127.0.0.1", "port": 8080, "root": str(Path.home()), "token": "",
            "ntfy_url": "", "ntfy_token": "", "ntfy_min_secs": 15, "ntfy_detail": False,
            "ntfy_click": "", "upload_max_mb": 25, "download_max_mb": 100}
ENV = {"host": "HOST", "port": "PORT", "root": "OC_ROOT", "token": "OPENCODE_CHAT_TOKEN",
       "ntfy_url": "NTFY_URL", "ntfy_token": "NTFY_TOKEN", "ntfy_min_secs": "NTFY_MIN_SECS",
       "ntfy_detail": "NTFY_DETAIL", "ntfy_click": "NTFY_CLICK",
       "upload_max_mb": "UPLOAD_MAX_MB", "download_max_mb": "DOWNLOAD_MAX_MB"}
RESTART_FIELDS = ("host", "port")     # se aplican al reiniciar; el resto, en el momento


def _typed(key, value):
    d = DEFAULTS[key]
    if isinstance(d, bool):
        return value in (True, 1, "1", "true", "True", "si", "sí")
    if isinstance(d, int):
        return int(value)
    return str(value).strip()


def read_config():
    """Archivo de configuración (si existe), sin las variables de entorno."""
    cfg = dict(DEFAULTS)
    try:
        cfg.update({k: _typed(k, v) for k, v in json.loads(CONF_FILE.read_text()).items() if k in DEFAULTS})
    except FileNotFoundError:
        pass
    return cfg


def env_overrides():
    return {k: _typed(k, os.environ[v]) for k, v in ENV.items() if os.environ.get(v)}


def save_config(cfg):
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONF_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: cfg[k] for k in DEFAULTS}, indent=2, ensure_ascii=False))
    os.chmod(tmp, 0o600)
    tmp.replace(CONF_FILE)


def apply_config(cfg):
    """Pasa la configuración a las variables que usa el resto del programa."""
    global HOST, PORT, TOKEN, ROOT, NTFY_URL, NTFY_TOKEN, NTFY_MIN_SECS, NTFY_DETAIL, NTFY_CLICK
    global MAX_UPLOAD, MAX_DOWNLOAD
    HOST, PORT = cfg["host"], cfg["port"]
    TOKEN = cfg["token"] or TOKEN_TEMPORAL
    ROOT = Path(cfg["root"]).expanduser().resolve()
    NTFY_URL, NTFY_TOKEN = cfg["ntfy_url"], cfg["ntfy_token"]
    NTFY_MIN_SECS, NTFY_DETAIL = cfg["ntfy_min_secs"], cfg["ntfy_detail"]
    NTFY_CLICK = cfg["ntfy_click"] or f"http://{HOST}:{PORT}/"
    MAX_UPLOAD = cfg["upload_max_mb"] * 1_000_000
    MAX_DOWNLOAD = cfg["download_max_mb"] * 1_000_000


def ntfy_normal(v):
    """"mi-tema" → "https://ntfy.sh/mi-tema"; una URL completa queda igual."""
    v = (v or "").strip()
    if v and not v.startswith(("http://", "https://")) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", v):
        return "https://ntfy.sh/" + v
    return v


def validate(cfg):
    if not Path(cfg["root"]).expanduser().is_dir():
        raise ValueError("La carpeta raíz no existe")
    if not 1 <= cfg["port"] <= 65535:
        raise ValueError("Puerto inválido")
    if cfg["ntfy_url"] and not re.match(r"^https?://[^/\s]+/.+", cfg["ntfy_url"]):
        raise ValueError("Poné el nombre del tema (ej. mi-tema-secreto) o una URL tipo https://ntfy.sh/mi-tema-secreto")
    if cfg["token"] and len(cfg["token"]) < 16:
        raise ValueError("El token tiene que tener al menos 16 caracteres")
    for k in ("ntfy_min_secs", "upload_max_mb", "download_max_mb"):
        if cfg[k] < 0:
            raise ValueError("Los números no pueden ser negativos")


def tailscale_ip():
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5).stdout.split()
        return out[0] if out else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def setup_wizard():
    """Pregunta lo básico en la terminal y guarda el archivo de configuración."""
    cfg = read_config()
    print(f"⚙  Configuración de OpenCode Chat ({CONF_FILE})\n   Enter deja el valor entre corchetes.\n")

    def ask(label, key, default):
        while True:
            v = input(f"{label} [{default}]: ").strip() or str(default)
            try:
                cfg[key] = _typed(key, v)
                if key == "root" and not Path(cfg[key]).expanduser().is_dir():
                    raise ValueError("esa carpeta no existe")
                return
            except ValueError as e:
                print(f"   ✗ {e}")

    ts = tailscale_ip()
    print("¿En qué dirección escucha? 127.0.0.1 = solo esta máquina." + (f" Tu IP de Tailscale es {ts}." if ts else ""))
    ask("Dirección", "host", cfg["host"] if cfg["host"] != DEFAULTS["host"] or not ts else ts)
    ask("Puerto", "port", cfg["port"])
    print("Carpeta raíz: OpenCode y la web solo ven lo que está adentro. Conviene la de tus proyectos, no todo tu usuario.")
    ask("Carpeta raíz", "root", cfg["root"])
    if not cfg["token"] or input("¿Generar un token nuevo? [n]: ").strip().lower() in ("s", "si", "sí", "y"):
        cfg["token"] = secrets.token_urlsafe(24)
        print(f"🔑 Token: {cfg['token']}   (guardalo: lo pide la web la primera vez)")
    print("Avisos por ntfy (opcional): el nombre del tema (ej. mi-tema-largo-y-secreto) o la URL completa.")
    print("   Enter = dejarlo como está, '-' = sin avisos.")
    while True:
        v = input(f"Tema o URL de ntfy [{cfg['ntfy_url'] or 'sin avisos'}]: ").strip()
        if not v:
            break
        cfg["ntfy_url"] = "" if v == "-" else ntfy_normal(v)
        try:
            validate(cfg)
            break
        except ValueError as e:
            print(f"   ✗ {e}")
    validate(cfg)
    save_config(cfg)
    print(f"\n✅ Guardado en {CONF_FILE}. Arrancá con: python3 {Path(sys.argv[0]).name}")


TOKEN_TEMPORAL = secrets.token_urlsafe(24)   # solo si no hay token configurado
CONFIG = {**read_config(), **env_overrides()}
HOST = PORT = TOKEN = ROOT = None

OC_HOST = "127.0.0.1"
OC_PORT = int(os.environ.get("OC_PORT", "4096"))
OC_USER = os.environ.get("OPENCODE_SERVER_USERNAME", "opencode")
OC_PASS = os.environ.get("OPENCODE_SERVER_PASSWORD", "")

MAX_BODY = 200_000
NTFY_URL = NTFY_TOKEN = NTFY_CLICK = ""
NTFY_MIN_SECS, NTFY_DETAIL, MAX_UPLOAD, MAX_DOWNLOAD = 15, False, 0, 0
apply_config(CONFIG)
WATCHER_ON = False
WATCH_DIRS = set()   # carpetas que la UI está usando (las vigila el aviso ntfy)
SEARCH_DEPTH = 4
SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "dist", "build"}

# Solo se reenvía a opencode lo que la UI necesita (método, ruta)
ID = r"[A-Za-z0-9_\-]+"
ALLOWED = [
    ("GET", r"provider"),
    ("GET", r"session"),
    ("POST", r"session"),
    ("GET", r"session/status"),
    ("GET|PATCH|DELETE", rf"session/{ID}"),
    ("GET", rf"session/{ID}/(message|diff|todo)"),
    ("POST", rf"session/{ID}/(prompt_async|abort)"),
    # permisos: ruta clásica y ruta v2 (OpenCode 1.18 tiene las dos)
    ("GET", r"permission"),
    ("POST", rf"permission/{ID}/reply"),
    ("GET", r"api/permission/request"),
    ("POST", rf"api/session/{ID}/permission/{ID}/reply"),
    # preguntas interactivas de OpenCode (herramienta `question`)
    ("GET", r"question"),
    ("POST", rf"question/{ID}/(reply|reject)"),
]
ALLOWED = [(re.compile(m), re.compile(p)) for m, p in ALLOWED]
FORWARD_QS = ("directory", "limit", "messageID")


# ---------------------------------------------------------------- opencode
def oc_request(method, path, body=None, timeout=60):
    conn = http.client.HTTPConnection(OC_HOST, OC_PORT, timeout=timeout)
    headers = {}
    if OC_PASS:
        cred = base64.b64encode(f"{OC_USER}:{OC_PASS}".encode()).decode()
        headers["Authorization"] = "Basic " + cred
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        conn.request(method, path, body=body, headers=headers)
        r = conn.getresponse()
        return r.status, r.getheader("Content-Type", "application/json"), r.read()
    finally:
        conn.close()


def oc_status():
    try:
        return oc_request("GET", "/global/health", timeout=2)[0]
    except Exception:
        return None


def ensure_opencode():
    global OC_PASS
    st = oc_status()
    if st == 200:
        print(f"✅ opencode serve ya está corriendo en {OC_HOST}:{OC_PORT}")
        return
    if st == 401:
        sys.exit(f"❌ Ya hay un `opencode serve` corriendo en {OC_HOST}:{OC_PORT} y pide contraseña\n"
                 "   (suele ser el que levantó otra copia de OpenCode Chat). Dos opciones:\n"
                 "   • Pararlo y volver a arrancar este:   pkill -f 'opencode serve'\n"
                 "   • Usar ese, pasándole su contraseña:  OPENCODE_SERVER_PASSWORD=la-clave python3 "
                 + Path(sys.argv[0]).name)
    exe = shutil.which("opencode")
    if not exe:
        sys.exit("❌ No encuentro el binario `opencode` en el PATH.")
    if not OC_PASS:
        OC_PASS = secrets.token_urlsafe(16)
    env = {**os.environ, "OPENCODE_SERVER_PASSWORD": OC_PASS}
    subprocess.Popen([exe, "serve", "--hostname", OC_HOST, "--port", str(OC_PORT)],
                     env=env, cwd=str(ROOT), stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    for _ in range(40):
        time.sleep(0.5)
        if oc_status() == 200:
            print(f"✅ opencode serve iniciado en {OC_HOST}:{OC_PORT}")
            return
    sys.exit("❌ opencode serve no respondió a tiempo.")


# ------------------------------------------------------------------- ntfy
def oc_json(path):
    try:
        status, _, data = oc_request("GET", path, timeout=10)
        return json.loads(data) if status == 200 and data else None
    except Exception:
        return None


def ntfy_send(title, message, priority=3, tags=None):
    if not NTFY_URL:
        return
    u = urllib.parse.urlparse(NTFY_URL)
    base_path, _, topic = u.path.rstrip("/").rpartition("/")
    if not (u.scheme and u.netloc and topic):
        return
    payload = {"topic": topic, "title": title, "message": message,
               "priority": priority, "tags": tags or [], "click": NTFY_CLICK}
    headers = {"Content-Type": "application/json"}
    if NTFY_TOKEN:
        headers["Authorization"] = "Bearer " + NTFY_TOKEN
    req = urllib.request.Request(f"{u.scheme}://{u.netloc}{base_path}",
                                 data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print(f"⚠️  ntfy: {e}")


def pending_perms(q):
    out = []
    for path in ("/permission", "/api/permission/request"):
        data = oc_json(path + q)
        if isinstance(data, dict):
            data = data.get("items") or data.get("data") or []
        if isinstance(data, list):
            out += [x for x in data if isinstance(x, dict)]
        if out:
            break
    return out


def start_watcher():
    global WATCHER_ON
    if NTFY_URL and not WATCHER_ON:
        WATCHER_ON = True
        threading.Thread(target=watcher, daemon=True).start()


def watcher():
    """Mira OpenCode cada 3 s y avisa por ntfy: tarea terminada / permiso pedido."""
    busy_since, seen, inited = {}, set(), set()
    while True:
        time.sleep(3)
        if not NTFY_URL:
            continue
        if len(seen) > 5000:   # que no crezca para siempre
            seen.clear()
        for d in list(WATCH_DIRS):
            q = "?" + urllib.parse.urlencode({"directory": d})
            st = oc_json("/session/status" + q)
            if not isinstance(st, dict):
                continue
            now = {sid for sid, s in st.items()
                   if isinstance(s, dict) and s.get("type") != "idle"}
            first = d not in inited
            inited.add(d)
            for sid in now:
                busy_since.setdefault((d, sid), time.time())
            for key in [k for k in busy_since if k[0] == d and k[1] not in now]:
                t0 = busy_since.pop(key)
                if time.time() - t0 < NTFY_MIN_SECS:
                    continue
                info = oc_json(f"/session/{key[1]}{q}")   # None si la borraron
                if isinstance(info, dict):
                    ntfy_send("OpenCode terminó", (info.get("title") or "Sesión")[:100],
                              3, ["white_check_mark"])
            for p in pending_perms(q):
                pid = p.get("id") or p.get("requestID")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                if first:
                    continue
                what = str(p.get("permission") or p.get("type") or "permiso")
                if NTFY_DETAIL:
                    pats = p.get("patterns") or p.get("pattern") or []
                    pats = pats if isinstance(pats, list) else [pats]
                    what += ": " + ", ".join(map(str, pats))[:150]
                ntfy_send("OpenCode pide permiso", what, 4, ["lock"])
            qdata = oc_json("/question" + q)
            for x in (qdata if isinstance(qdata, list) else []):
                qid = x.get("id") if isinstance(x, dict) else None
                if not qid or qid in seen:
                    continue
                seen.add(qid)
                if first:
                    continue
                text = "OpenCode necesita tu respuesta"
                if NTFY_DETAIL:
                    qs_ = x.get("questions") or [{}]
                    text = str((qs_[0] or {}).get("question") or text)[:150]
                ntfy_send("OpenCode te pregunta", text, 4, ["question"])


# ------------------------------------------------------------- filesystem
def safe_path(p):
    path = Path(p or ROOT).expanduser().resolve()
    if path != ROOT and ROOT not in path.parents:
        raise PermissionError("Fuera de la carpeta raíz permitida")
    if not path.is_dir():
        raise ValueError("No es una carpeta")
    return path


def dir_entry(path):
    return {"name": os.path.basename(path), "path": str(path),
            "git": os.path.isdir(os.path.join(path, ".git"))}


def list_dirs(path):
    dirs = []
    with os.scandir(path) as it:
        for e in sorted(it, key=lambda e: e.name.lower()):
            if e.name.startswith(".") or e.name in SKIP_DIRS:
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    dirs.append(dir_entry(e.path))
            except OSError:
                pass
    return {"path": str(path), "root": str(ROOT),
            "parent": str(path.parent) if path != ROOT else None, "dirs": dirs}


def search_dirs(q):
    q, res, t0 = q.lower(), [], time.time()
    base = len(ROOT.parts)
    for dp, dns, _ in os.walk(ROOT):
        dns[:] = [d for d in dns if not d.startswith(".") and d not in SKIP_DIRS]
        if len(Path(dp).parts) - base >= SEARCH_DEPTH:
            dns[:] = []
        res += [dir_entry(os.path.join(dp, d)) for d in dns if q in d.lower()]
        if len(res) >= 40 or time.time() - t0 > 3:
            break
    return {"results": res[:40]}


def hidden_rel(path):
    """True si la ruta (relativa a ROOT) toca algo oculto o una clave."""
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        return True
    return any(p.startswith(".") for p in rel.parts) or path.suffix.lower() in {".pem", ".key"}


def list_files(p):
    path = safe_path(p)
    if hidden_rel(path):
        raise PermissionError("Carpeta oculta o sensible")
    items = []
    with os.scandir(path) as it:
        for e in it:
            if e.name.startswith("."):
                continue
            try:
                isdir = e.is_dir(follow_symlinks=False)
                if isdir and e.name in SKIP_DIRS:
                    continue
                if not (isdir or e.is_file(follow_symlinks=False)):
                    continue
                items.append({"name": e.name, "path": e.path, "dir": isdir,
                              "size": 0 if isdir else e.stat().st_size})
            except OSError:
                pass
    items.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    return {"path": str(path), "parent": str(path.parent) if path != ROOT else None,
            "entries": items[:500], "max_upload": MAX_UPLOAD}


def safe_file(p):
    path = Path(p or "").expanduser().resolve()
    if ROOT not in path.parents:
        raise PermissionError("Fuera de la carpeta raíz permitida")
    if hidden_rel(path):
        raise PermissionError("Archivo oculto o sensible")
    if not path.is_file():
        raise ValueError("No es un archivo")
    return path


def clean_name(n):
    n = os.path.basename((n or "").replace("\\", "/"))
    n = re.sub(r'[\x00-\x1f<>:"|?*]', "_", n).strip(" .")
    if not n:
        raise ValueError("Nombre de archivo inválido")
    return n[:150]


# ------------------------------------------------------------------ server
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status, body=b"", ctype="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if status >= 400:   # puede quedar cuerpo de la petición sin leer
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if body and status != 204:
            self.wfile.write(body)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload).encode())

    def _authed(self):
        got = self.headers.get("Authorization", "").removeprefix("Bearer ")
        return hmac.compare_digest(got.encode(), TOKEN.encode())

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n == 0:   # POST sin cuerpo (abort, reject…): es válido
            return b""
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise ValueError("Se requiere application/json")
        if n > MAX_BODY:
            raise ValueError("Cuerpo demasiado grande")
        return self.rfile.read(n)

    def handle_any(self, method):
        try:
            u = urllib.parse.urlparse(self.path)
            if method == "GET" and u.path == "/":
                return self._send(200, HTML.encode(), "text/html; charset=utf-8")
            if not u.path.startswith("/api/"):
                return self._json(404, {"error": "No encontrado"})
            if not self._authed():
                return self._json(401, {"error": "No autorizado"})
            qs = urllib.parse.parse_qs(u.query)

            if u.path == "/api/fs" and method == "GET":
                return self._json(200, list_dirs(safe_path(qs.get("path", [""])[0])))
            if u.path == "/api/fs/search" and method == "GET":
                q = qs.get("q", [""])[0].strip()
                return self._json(200, search_dirs(q) if len(q) >= 2 else {"results": []})
            if u.path == "/api/files" and method == "GET":
                return self._json(200, list_files(qs.get("path", [""])[0]))
            if u.path == "/api/upload" and method == "POST":
                return self._upload(qs)
            if u.path == "/api/download" and method == "GET":
                return self._download(qs)
            if u.path == "/api/config" and method == "GET":
                return self._json(200, self._config_view())
            if u.path == "/api/config" and method == "PATCH":
                return self._config_save()
            if u.path.startswith("/api/oc/"):
                return self._proxy(method, u, qs)
            self._json(404, {"error": "No encontrado"})
        except PermissionError as e:
            self._json(403, {"error": str(e)})
        except ValueError as e:
            self._json(400, {"error": str(e)})
        except Exception as e:
            self._json(502, {"error": f"{type(e).__name__}: {e}"})

    def _proxy(self, method, u, qs):
        sub = u.path[len("/api/oc/"):].strip("/")
        if not any(m.fullmatch(method) and p.fullmatch(sub) for m, p in ALLOWED):
            return self._json(404, {"error": "Ruta no permitida"})
        out = {k: qs[k][0] for k in FORWARD_QS if k in qs}
        if "directory" in out:
            out["directory"] = str(safe_path(out["directory"]))
            WATCH_DIRS.add(out["directory"])
        body = self._read_body() if method in ("POST", "PATCH") else None
        path = "/" + sub + ("?" + urllib.parse.urlencode(out) if out else "")
        status, ctype, data = oc_request(method, path, body or None)
        self._send(status, data, ctype.split(";")[0] if status != 204 else "application/json")

    def _config_view(self, **extra):
        cfg = read_config()
        envs = sorted(k for k in env_overrides())
        return {**{k: v for k, v in cfg.items() if k not in ("token", "ntfy_token")},
                "has_ntfy_token": bool(cfg["ntfy_token"]), "file": str(CONF_FILE),
                "env_locked": envs, "restart_fields": list(RESTART_FIELDS),
                "running": {"host": HOST, "port": PORT}, "version": VERSION, **extra}

    def _config_save(self):
        data = json.loads(self._read_body() or b"{}")
        cfg = read_config()
        for k, v in data.items():
            if k in DEFAULTS and k != "token" and not (k == "ntfy_token" and v == ""):
                cfg[k] = _typed(k, v)
        cfg["ntfy_url"] = ntfy_normal(cfg["ntfy_url"])
        nuevo = None
        if data.get("new_token"):
            nuevo = cfg["token"] = secrets.token_urlsafe(24)
        validate(cfg)
        save_config(cfg)
        apply_config({**cfg, **env_overrides(), "host": HOST, "port": PORT})
        start_watcher()
        return self._json(200, self._config_view(new_token=nuevo))

    def _upload(self, qs):
        folder = safe_path(qs.get("dir", [""])[0])
        if hidden_rel(folder):
            raise PermissionError("Carpeta oculta o sensible")
        name = clean_name(qs.get("name", [""])[0])
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            raise ValueError("Archivo vacío")
        if n > MAX_UPLOAD:
            raise ValueError(f"Máximo {MAX_UPLOAD // 1_000_000} MB por archivo")
        stem, suf = os.path.splitext(name)
        target, i = folder / name, 1
        while True:   # nunca pisa un archivo existente
            try:
                f = open(target, "xb")
                break
            except FileExistsError:
                target = folder / f"{stem} ({i}){suf}"
                i += 1
        left = n
        with f:
            while left:
                chunk = self.rfile.read(min(65536, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left:
            target.unlink(missing_ok=True)
            raise ValueError("Subida incompleta")
        self._json(200, {"name": target.name, "path": str(target), "size": n})

    def _download(self, qs):
        p = safe_file(qs.get("path", [""])[0])
        size = p.stat().st_size
        if size > MAX_DOWNLOAD:
            raise ValueError(f"Máximo {MAX_DOWNLOAD // 1_000_000} MB por descarga")
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(p.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(p.name))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            with open(p, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def do_GET(self): self.handle_any("GET")
    def do_POST(self): self.handle_any("POST")
    def do_PATCH(self): self.handle_any("PATCH")
    def do_DELETE(self): self.handle_any("DELETE")

    def log_message(self, *args):
        pass


HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>OpenCode</title>
<style>
:root{--bg:#262624;--panel:#1f1e1d;--card:#30302e;--text:#ece9e1;--muted:#9a968c;--line:#3d3c39;--accent:#d97757;--danger:#e5484d}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--text);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
button{font:inherit;color:inherit;background:none;border:0;cursor:pointer}
button:disabled{opacity:.4;cursor:default}
code,pre{font-family:ui-monospace,Menlo,Consolas,monospace}
#app{display:flex;height:100dvh}
/* sidebar */
#side{width:270px;background:var(--panel);border-right:1px solid var(--line);display:flex;flex-direction:column;flex-shrink:0;
  padding-top:env(safe-area-inset-top)}
#side .top{padding:12px}
.newbtn{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:10px;text-align:left;background:var(--card)}
.newbtn:hover{border-color:var(--accent)}
#sessions{flex:1;overflow-y:auto;padding:0 8px 12px}
.sess{display:flex;align-items:center;border-radius:8px;padding:8px 10px;gap:6px}
.sess:hover,.sess.on{background:var(--card)}
.sess .t{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left;font-size:14px}
.sess .x{color:var(--muted);padding:0 4px}
.sess .x:hover{color:var(--danger)}
.lbl{padding:10px 14px 4px;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
#scrim{display:none}
@media(max-width:899px){
  #side{position:fixed;inset:0 auto 0 0;z-index:30;transform:translateX(-100%);transition:transform .25s}
  #side.open{transform:none}
  #scrim.open{display:block;position:fixed;inset:0;background:#0008;z-index:25}
}
@media(min-width:900px){#menu{display:none}}
/* main */
#main{flex:1;display:flex;flex-direction:column;min-width:0}
header{display:flex;align-items:center;gap:8px;padding:10px 12px;padding-top:max(10px,env(safe-area-inset-top));border-bottom:1px solid var(--line)}
.chip{padding:6px 12px;border:1px solid var(--line);border-radius:999px;background:var(--card);font-size:13px;max-width:60vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chip:hover{border-color:var(--accent)}
#menu{font-size:20px;padding:0 6px}
#chat{flex:1;overflow-y:auto;padding:18px 14px}
.col{max-width:760px;margin:0 auto;display:flex;flex-direction:column;gap:18px}
.empty{text-align:center;margin-top:18vh;color:var(--muted)}
.empty h2{font-weight:500;color:var(--text);font-size:26px;margin:0 0 6px}
.u{align-self:flex-end;max-width:85%;background:var(--card);padding:10px 14px;border-radius:18px 18px 4px 18px;white-space:pre-wrap;word-wrap:break-word}
.a{max-width:100%;word-wrap:break-word}
.a p{margin:.4em 0}.a ul{margin:.4em 0;padding-left:1.3em}.a h4{margin:.8em 0 .2em;font-size:1em}
.a code{background:#0003;padding:1px 5px;border-radius:5px;font-size:.9em}
.a pre{background:#161615;border:1px solid var(--line);border-radius:10px;padding:12px;overflow-x:auto;font-size:13px}
.a pre code{background:none;padding:0}
details.tool,details.think{border:1px solid var(--line);border-radius:10px;margin:6px 0;font-size:13px;background:#00000018}
details>summary{cursor:pointer;padding:7px 10px;color:var(--muted);list-style:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
details pre{margin:0;padding:10px;white-space:pre-wrap;word-break:break-word;border-top:1px solid var(--line);max-height:300px;overflow:auto;font-size:12px;color:var(--muted)}
.err{border:1px solid var(--danger);color:#ffb4b6;border-radius:10px;padding:8px 12px;font-size:13px}
.dots span{animation:b 1.2s infinite;display:inline-block;color:var(--accent)}
.dots span:nth-child(2){animation-delay:.2s}.dots span:nth-child(3){animation-delay:.4s}
@keyframes b{0%,80%,100%{opacity:.2}40%{opacity:1}}
/* composer */
#comp{padding:8px 12px;padding-bottom:max(12px,env(safe-area-inset-bottom))}
.box{max-width:760px;margin:0 auto;background:var(--card);border:1px solid var(--line);border-radius:18px;padding:10px 12px}
.box:focus-within{border-color:#6b6a65}
textarea{width:100%;background:none;border:0;outline:0;color:var(--text);font:inherit;resize:none;max-height:180px;display:block}
.row{display:flex;align-items:center;justify-content:space-between;margin-top:6px;gap:8px}
#send{width:34px;height:34px;border-radius:50%;background:var(--accent);color:#fff;font-size:16px;flex-shrink:0}
#send.stop{background:var(--text);color:var(--bg)}
.note{max-width:760px;margin:6px auto 0;color:var(--muted);font-size:11px;text-align:center}
/* modal */
#modal{display:none;position:fixed;inset:0;background:#000a;z-index:50;align-items:flex-end;justify-content:center}
#modal.open{display:flex}
.sheet{background:var(--panel);width:100%;max-width:560px;max-height:85dvh;border-radius:18px 18px 0 0;display:flex;flex-direction:column;border:1px solid var(--line)}
@media(min-width:700px){#modal{align-items:center}.sheet{border-radius:18px}}
.sheet h3{margin:0;padding:14px 16px 6px;font-size:16px}
.sheet input[type=text]{margin:6px 16px;padding:9px 12px;background:var(--card);border:1px solid var(--line);border-radius:10px;color:var(--text);font:inherit;outline:0}
.crumb{padding:2px 16px 6px;color:var(--muted);font-size:12px;word-break:break-all}
.list{overflow-y:auto;padding:4px 8px 8px;flex:1}
.item{display:flex;align-items:center;gap:8px;padding:9px 10px;border-radius:8px;text-align:left;width:100%}
.item:hover{background:var(--card)}
.item .n{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.item .m{color:var(--muted);font-size:12px}
.pick{border:1px solid var(--accent);color:var(--accent);border-radius:8px;padding:2px 10px;font-size:12px}
.foot{display:flex;gap:8px;padding:10px 16px;padding-bottom:max(10px,env(safe-area-inset-bottom));border-top:1px solid var(--line);align-items:center}
.btn{padding:8px 14px;border-radius:10px;background:var(--accent);color:#fff}
.btn.g{background:var(--card);color:var(--text)}
.sw{display:flex;align-items:center;gap:6px;font-size:13px;color:var(--muted);margin-left:auto}
.foot{flex-wrap:wrap}
.item .m{white-space:nowrap}
.meter{max-width:760px;margin:0 auto 6px;font-size:11px;color:var(--muted);display:flex;flex-direction:column;gap:3px}
.meter .l{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}
.bar{height:4px;border-radius:2px;background:var(--line);overflow:hidden}
.bar i{display:block;height:100%;background:var(--accent)}
.bar i.w{background:#e5a23b}.bar i.d{background:var(--danger)}
.tkf{margin-top:2px;font-size:11px;color:var(--muted)}
.pw{position:relative}
.pw>pre{padding-top:32px}
.cp{position:absolute;top:6px;right:6px;padding:2px 8px;font-size:12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--muted)}
.cp:hover{color:var(--text)}
.acts{display:flex;gap:8px;margin-top:2px}
.acts .cp{position:static}
.perm{border:1px solid var(--accent);border-radius:12px;padding:10px 12px;background:#d977571a}
.perm .pt{font-weight:600;margin-bottom:6px}
.perm pre{margin:0 0 8px;padding:8px;white-space:pre-wrap;word-break:break-word;max-height:160px;overflow:auto;font-size:12px;background:#0003;border-radius:8px}
.pr{display:flex;gap:8px;flex-wrap:wrap}
.qq{margin-bottom:10px}
.qo{text-align:left}
.qo small{display:block;color:var(--muted);font-size:12px}
.btn.on{background:var(--accent);color:#fff}
.btn.on small{color:#ffe}
.warn{border:1px solid #e5a23b;color:#f3cf8f;border-radius:10px;padding:8px 12px;font-size:12px;display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.warn button{border:1px solid #e5a23b;border-radius:8px;padding:2px 10px}
.cfg{overflow-y:auto;padding:4px 16px 8px;flex:1}
.cfg label{display:block;font-size:13px;color:var(--muted);margin:10px 0 3px}
.cfg input[type=text],.cfg input[type=number]{width:100%;padding:8px 10px;background:var(--card);border:1px solid var(--line);border-radius:10px;color:var(--text);font:inherit;outline:0}
.cfg input:disabled{opacity:.5}
.cfg .h{font-size:11px;color:var(--muted);margin-top:3px}
.cfg .ck{display:flex;gap:8px;align-items:center;color:var(--text)}
.perm input[type=text]{width:100%;margin-top:6px;padding:8px 10px;background:var(--card);border:1px solid var(--line);border-radius:10px;color:var(--text);font:inherit;outline:0}
</style>
</head>
<body>
<div id="app">
  <aside id="side">
    <div class="top"><button class="newbtn" id="newBtn">＋ Nueva conversación</button></div>
    <div class="lbl">Conversaciones</div>
    <div id="sessions"></div>
  </aside>
  <div id="scrim"></div>
  <div id="main">
    <header>
      <button id="menu">☰</button>
      <button class="chip" id="projBtn">📁 Elegir proyecto</button>
      <span style="flex:1"></span>
      <button class="chip" id="filesBtn" title="Archivos: subir y descargar">📎</button>
      <button class="chip" id="cpAll" title="Copiar toda la conversación">📋</button>
      <button class="chip" id="cfgBtn" title="Configuración">⚙</button>
    </header>
    <div id="chat"><div class="col" id="col"></div></div>
    <div id="comp">
      <div class="meter" id="meter"></div>
      <div class="box">
        <textarea id="input" rows="1" placeholder="Escribile a OpenCode…"></textarea>
        <div class="row">
          <button class="chip" id="modelBtn">⚡ Modelo</button>
          <button id="send" title="Enviar">↑</button>
        </div>
      </div>
      <div class="note">OpenCode puede editar archivos y ejecutar comandos en la carpeta elegida.</div>
    </div>
  </div>
</div>
<div id="modal"><div class="sheet" id="sheet"></div></div>

<script>
const $ = id => document.getElementById(id);
const ls = {get:(k,d='')=>localStorage.getItem(k)??d, set:(k,v)=>localStorage.setItem(k,v)};
let token = ls.get('oc_token'), dir = ls.get('oc_dir'), sid = null;
let models = [], model = ls.get('oc_model'), onlyFree = ls.get('oc_free','1')==='1';
let busy = false, sentAt = 0, seenBusy = false, timer = null, lastSig = '';
const openIds = new Set();
let perms = [], questions = [], lastMsgs = [];
const sidKey = () => 'oc_sid:' + dir;   // última charla de cada proyecto
const rememberSid = () => { if (dir) { sid ? ls.set(sidKey(), sid) : localStorage.removeItem(sidKey()); } };

/* ---------- API ---------- */
async function api(path, {method='GET', body, params={}} = {}) {
  const qs = new URLSearchParams(params).toString();
  const res = await fetch(path + (qs ? '?'+qs : ''), {
    method,
    headers: {Authorization:'Bearer '+token, ...(body ? {'Content-Type':'application/json'} : {})},
    body: body ? JSON.stringify(body) : undefined
  });
  if (res.status === 401) {
    const t = prompt('Token de acceso:');
    if (!t) throw new Error('Sin token');
    token = t; ls.set('oc_token', t);
    return api(path, {method, body, params});
  }
  if (res.status === 204) return null;
  const text = await res.text();
  let data; try { data = JSON.parse(text) } catch { data = text }
  if (!res.ok) throw new Error((data && (data.error || data.message || data.data?.message)) || text || res.status);
  return data;
}
const oc = (p, o={}) => api('/api/oc/'+p, {...o, params:{...(o.params||{}), ...(dir ? {directory:dir} : {})}});
const base = p => p.split('/').filter(Boolean).pop() || p;

/* ---------- markdown seguro ---------- */
const esc = s => s.replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function md(src) {
  if ((src.match(/```/g)||[]).length % 2) src += '\n```';   // bloque aún abierto (streaming)
  const blocks = [];
  let t = src.replace(/```[\w-]*\n?([\s\S]*?)```/g, (m, c) => {
    blocks.push('<pre><code>' + esc(c.replace(/\n$/, '')) + '</code></pre>');
    return '\u0000' + (blocks.length-1) + '\u0000';
  });
  t = esc(t).replace(/`([^`\n]+)`/g, '<code>$1</code>').replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  const out = []; let para = [], list = false, m;
  const flush = () => { if (para.length) { out.push('<p>' + para.join('<br>') + '</p>'); para = []; } };
  for (const ln of t.split('\n')) {
    if ((m = ln.match(/^\s*[-*]\s+(.*)/))) { flush(); if (!list) { out.push('<ul>'); list = true; } out.push('<li>' + m[1] + '</li>'); continue; }
    if (list) { out.push('</ul>'); list = false; }
    if ((m = ln.match(/^#{1,4}\s+(.*)/))) { flush(); out.push('<h4>' + m[1] + '</h4>'); continue; }
    if (/^\u0000\d+\u0000$/.test(ln.trim())) { flush(); out.push(ln.trim()); continue; }
    if (!ln.trim()) { flush(); continue; }
    para.push(ln);
  }
  flush(); if (list) out.push('</ul>');
  return out.join('').replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[i]);
}

const knownLimits = () => { try { return JSON.parse(ls.get('oc_limits', '{}')); } catch { return {}; } };

/* ---------- render del chat ---------- */
const mk = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };

function detailsEl(cls, id, summary, body) {
  const d = mk('details', cls); d.dataset.id = id;
  if (openIds.has(id)) d.open = true;
  d.addEventListener('toggle', () => d.open ? openIds.add(id) : openIds.delete(id));
  d.append(mk('summary', '', summary), withCopy(mk('pre', '', body), () => body));
  return d;
}

const ft = n => n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'k' : String(n);
function tkOf(info) {
  const t = info.tokens || {}, c = t.cache || {};
  const inp = (t.input || 0) + (c.read || 0) + (c.write || 0), out = (t.output || 0) + (t.reasoning || 0);
  return {inp, out, all: t.total || (inp + out)};
}
// Cupo del proveedor: OpenCode no lo informa; se reconoce por el error que devuelve el proveedor.
const QUOTA_RE = /rate.?limit|quota|cuota|429|too many requests|insufficient|credit|exceeded|limit reached|free tier/i;
const errText = e => String(e && (e.data?.message || e.message || e.name) || '');
// "context length exceeded" no es cupo: es la charla llena.
const isQuota = e => !!e && QUOTA_RE.test(errText(e) + ' ' + (e.name || '')) && !/context|contexto/i.test(errText(e));
function updateMeter(msgs) {
  const el = $('meter'); el.replaceChildren();
  const asst = msgs.filter(x => x.info.role === 'assistant' && tkOf(x.info).all > 0);
  if (!asst.length) return;
  let tin = 0, tout = 0, cost = 0;
  for (const x of asst) { const t = tkOf(x.info); tin += t.inp; tout += t.out; cost += x.info.cost || 0; }
  const last = asst[asst.length - 1].info;
  const used = tkOf(last).all;                       // tamaño actual del contexto
  const m = models.find(x => x.providerID === last.providerID && x.modelID === last.modelID);
  // Los modelos gratuitos rotan: si ya no está en la lista, se usa el límite que se vio antes.
  const lim = m?.context || knownLimits()[last.providerID + '/' + last.modelID] || 0;
  const pct = lim ? Math.min(100, Math.round(used / lim * 100)) : 0;
  const l = mk('div', 'l');
  l.append(mk('span', '', lim ? `Contexto ${ft(used)} de ${ft(lim)} · quedan ${ft(Math.max(0, lim - used))} (${100 - pct}%)`
                                 : `Contexto ${ft(used)} · límite desconocido (${last.modelID} ya no está en la lista de modelos)`),
           mk('span', '', `Procesado en la charla ↑ ${ft(tin)} ↓ ${ft(tout)}` + (cost ? ` · $${cost.toFixed(3)}` : '')));
  l.title = 'Contexto: lo que ocupa hoy la charla en la memoria del modelo. Procesado: la suma de cada pedido (cada uno reenvía el historial).';
  el.append(l);
  if (lim) {
    const bar = mk('div', 'bar'), i = mk('i', pct > 85 ? 'd' : pct > 60 ? 'w' : '');
    i.style.width = pct + '%'; bar.append(i); el.append(bar);
  }
  const lastErr = [...msgs].reverse().find(x => x.info.role === 'assistant')?.info.error;
  if (isQuota(lastErr)) {
    const w = mk('div', 'warn');
    w.append(mk('span', '', '⛽ El proveedor cortó por cupo o límite de uso de este modelo. Elegí otro modelo o esperá.'));
    const b = mk('button', '', 'Cambiar modelo'); b.onclick = openModels; w.append(b);
    el.append(w);
  } else if (lim && pct >= 75) {
    const w = mk('div', 'warn');
    w.append(mk('span', '', `⚠ Quedan ${ft(Math.max(0, lim - used))} de contexto: conviene seguir en una charla nueva antes de que se llene.`));
    const b = mk('button', '', 'Nueva charla'); b.onclick = newChat; w.append(b);
    el.append(w);
  }
}

function renderMsg(m) {
  const {info, parts} = m;
  if (info.role === 'user') {
    const txt = parts.filter(p => p.type === 'text' && !p.synthetic).map(p => p.text).join('\n');
    return txt ? [mk('div', 'u', txt)] : [];
  }
  const wrap = mk('div', 'a');
  for (const p of parts) {
    if (p.type === 'text' && p.text) {
      const d = mk('div'); d.innerHTML = md(p.text);
      d.querySelectorAll('pre').forEach(pre => withCopy(pre, () => pre.textContent));
      wrap.append(d);
    }
    else if (p.type === 'reasoning' && p.text) wrap.append(detailsEl('think', p.id, '💭 Razonamiento', p.text));
    else if (p.type === 'tool') {
      const st = p.state || {};
      const ico = st.status === 'completed' ? '✓' : st.status === 'error' ? '✗' : '⋯';
      let body = st.input ? JSON.stringify(st.input, null, 2) : '';
      if (st.output) body += '\n\n→ ' + String(st.output);
      if (st.error) body += '\n\n✗ ' + st.error;
      wrap.append(detailsEl('tool', p.id || p.callID, `${ico} ${p.tool}${st.title ? ' · ' + st.title : ''}`, body.slice(0, 3000)));
    }
  }
  const raw = parts.filter(p => p.type === 'text' && p.text).map(p => p.text).join('\n\n');
  if (raw) { const a = mk('div', 'acts'); a.append(copyBtn(() => raw, 'Copiar respuesta')); wrap.append(a); }
  const tt = tkOf(info);
  if (tt.all) wrap.append(mk('div', 'tkf', `↑ ${ft(tt.inp)} · ↓ ${ft(tt.out)}` + (info.cost ? ` · $${info.cost.toFixed(4)}` : '')));
  const e = info.error;
  if (e) wrap.append(mk('div', 'err', (isQuota(e) ? '⛽ Cupo del proveedor: ' : '⚠️ ') + (errText(e) || 'Error')));
  return wrap.children.length ? [wrap] : [];
}

function render(msgs) {
  lastMsgs = msgs;
  updateMeter(msgs);
  const chat = $('chat'), col = $('col');
  const near = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 80;
  const els = [];
  if (!msgs.length) {
    const e = mk('div', 'empty');
    e.append(mk('h2', '', dir ? '¿En qué trabajamos?' : 'Elegí un proyecto'),
             mk('div', '', dir ? '📁 ' + base(dir) : 'Tocá el botón de arriba para buscar la carpeta.'));
    els.push(e);
  }
  msgs.forEach(m => els.push(...renderMsg(m)));
  perms.forEach(p => els.push(permCard(p)));
  questions.forEach(x => els.push(questionCard(x)));
  if (busy) { const d = mk('div', 'dots'); d.innerHTML = '<span>●</span><span>●</span><span>●</span>'; els.push(d); }
  col.replaceChildren(...els);
  if (near) chat.scrollTop = chat.scrollHeight;
}

/* ---------- copiar ---------- */
// navigator.clipboard solo existe en HTTPS/localhost; por http://IP-de-Tailscale hace falta el plan B.
async function copyText(t) {
  try { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(t); return true; } } catch {}
  const ta = document.createElement('textarea');
  ta.value = t; ta.setAttribute('readonly', '');
  ta.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;opacity:0';
  document.body.append(ta); ta.select(); ta.setSelectionRange(0, t.length);
  let ok = false; try { ok = document.execCommand('copy'); } catch {}
  ta.remove(); return ok;
}
function copyBtn(getText, label = 'Copiar') {
  const b = mk('button', 'cp', label);
  b.onclick = async e => {
    e.preventDefault(); e.stopPropagation();
    const ok = await copyText(getText());
    b.textContent = ok ? '✓ Copiado' : '✗ No se pudo';
    setTimeout(() => { b.textContent = label; }, 1500);
  };
  return b;
}
function withCopy(pre, getText) {
  const w = mk('div', 'pw');
  if (pre.parentNode) pre.replaceWith(w);
  w.append(pre, copyBtn(getText));
  return w;
}
function chatText() {
  return lastMsgs.map(m => {
    const txt = m.parts.filter(p => p.type === 'text' && p.text && !p.synthetic).map(p => p.text).join('\n');
    return txt ? (m.info.role === 'user' ? '## Yo\n' : '## OpenCode\n') + txt : '';
  }).filter(Boolean).join('\n\n');
}

/* ---------- permisos ---------- */
// OpenCode 1.18 expone dos rutas de permisos (clásica y v2): se prueba la clásica y, si no hay nada, la v2.
async function fetchPerms() {
  const out = [];
  for (const [path, src] of [['permission', 'v1'], ['api/permission/request', 'v2']]) {
    try {
      const d = await oc(path);
      const arr = Array.isArray(d) ? d : (d && (d.items || d.data)) || [];
      for (const x of arr) {
        const id = x && (x.id || x.requestID);
        if (id && !out.some(o => o.id === id)) out.push({...x, id, src});
      }
    } catch {}
    if (out.length) break;
  }
  return out.filter(x => !x.sessionID || x.sessionID === sid);
}
function replyPerm(p, reply) {
  const body = {reply};
  if (p.src === 'v2') return oc(`api/session/${p.sessionID || sid}/permission/${p.id}/reply`, {method: 'POST', body});
  return oc(`permission/${p.id}/reply`, {method: 'POST', body});
}
function permCard(p) {
  const what = String(p.permission || p.type || p.title || 'permiso');
  const pats = [].concat(p.patterns || p.pattern || []).filter(Boolean).join('\n');
  const c = mk('div', 'perm');
  c.append(mk('div', 'pt', '🔐 OpenCode pide permiso: ' + what));
  if (pats) c.append(mk('pre', '', pats.slice(0, 600)));
  const row = mk('div', 'pr');
  const btn = (label, cls, reply) => {
    const b = mk('button', 'btn ' + cls, label);
    b.onclick = async () => {
      if (reply === 'always' && !confirm(`¿Permitir "${what}" siempre? Queda como regla permanente.`)) return;
      row.querySelectorAll('button').forEach(x => { x.disabled = true; });
      try { await replyPerm(p, reply); } catch (e) { toast('❌ ' + e.message); }
      tick();
    };
    return b;
  };
  row.append(btn('Permitir una vez', '', 'once'), btn('Rechazar', 'g', 'reject'), btn('Siempre', 'g', 'always'));
  c.append(row);
  return c;
}

/* ---------- preguntas interactivas y botón detener ---------- */
async function fetchQuestions() {
  try {
    const d = await oc('question');
    return (Array.isArray(d) ? d : []).filter(x => x && x.id && (!x.sessionID || x.sessionID === sid));
  } catch { return []; }
}
function questionCard(q) {
  const c = mk('div', 'perm');
  const st = (q.questions || []).map(qq => ({multi: !!qq.multiple, sel: new Set(), txt: null}));
  (q.questions || []).forEach((qq, i) => {
    const box = mk('div', 'qq'), s = st[i];
    if (qq.header) box.append(mk('div', 'lbl', qq.header));
    box.append(mk('div', 'pt', qq.question || ''));
    const row = mk('div', 'pr');
    for (const o of qq.options || []) {
      const b = mk('button', 'btn g qo'); b.dataset.l = o.label;
      b.append(mk('span', '', o.label));
      if (o.description) b.append(mk('small', '', o.description));
      b.onclick = () => {
        if (s.multi) { s.sel.has(o.label) ? s.sel.delete(o.label) : s.sel.add(o.label); }
        else { s.sel.clear(); s.sel.add(o.label); if (s.txt) s.txt.value = ''; }
        row.querySelectorAll('button').forEach(x => x.classList.toggle('on', s.sel.has(x.dataset.l)));
      };
      row.append(b);
    }
    box.append(row);
    if (qq.custom !== false) {
      s.txt = mk('input'); s.txt.type = 'text'; s.txt.placeholder = 'Otra respuesta…';
      s.txt.oninput = () => { if (!s.multi && s.txt.value) { s.sel.clear(); row.querySelectorAll('button').forEach(x => x.classList.remove('on')); } };
      box.append(s.txt);
    }
    c.append(box);
  });
  const foot = mk('div', 'pr');
  const lock = () => foot.querySelectorAll('button').forEach(x => { x.disabled = true; });
  const ok = mk('button', 'btn', 'Enviar respuesta');
  ok.onclick = async () => {
    const answers = st.map(s => {
      const a = [...s.sel], t = s.txt && s.txt.value.trim();
      if (t) { if (!s.multi) a.length = 0; a.push(t); }
      return a;
    });
    if (answers.some(a => !a.length)) { toast('Elegí o escribí una respuesta en cada pregunta.'); return; }
    lock();
    try { await oc(`question/${q.id}/reply`, {method: 'POST', body: {answers}}); } catch (e) { toast('❌ ' + e.message); }
    tick();
  };
  const no = mk('button', 'btn g', 'Descartar');
  no.onclick = async () => {
    if (!confirm('¿Descartar la pregunta? OpenCode puede detener la tarea.')) return;
    lock();
    try { await oc(`question/${q.id}/reject`, {method: 'POST'}); } catch (e) { toast('❌ ' + e.message); }
    tick();
  };
  foot.append(ok, no); c.append(foot);
  return c;
}
// Detener: primero se descartan preguntas/permisos pendientes (si no, la tarea sigue esperando) y luego se aborta.
async function stopRun() {
  if (!sid) return;
  const errs = [];
  for (const x of questions) { try { await oc(`question/${x.id}/reject`, {method: 'POST'}); } catch (e) { errs.push(e.message); } }
  for (const p of perms) { try { await replyPerm(p, 'reject'); } catch (e) { errs.push(e.message); } }
  try { await oc(`session/${sid}/abort`, {method: 'POST'}); } catch (e) { errs.push(e.message); }
  if (errs.length) toast('❌ ' + errs.join(' · '));
  tick();
}

/* ---------- archivos: subir y descargar ---------- */
const fsz = n => n >= 1e6 ? (n / 1e6).toFixed(1) + ' MB' : n >= 1e3 ? Math.round(n / 1e3) + ' KB' : n + ' B';
async function uploadFile(file, folder) {
  const res = await fetch('/api/upload?' + new URLSearchParams({dir: folder, name: file.name}), {
    method: 'POST', headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/octet-stream'}, body: file});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.status);
  return data;
}
async function download(path, name) {
  const res = await fetch('/api/download?' + new URLSearchParams({path}), {headers: {Authorization: 'Bearer ' + token}});
  if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error || res.status); }
  const url = URL.createObjectURL(await res.blob());
  const a = mk('a'); a.href = url; a.download = name; document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}
function openFiles(start = '') {
  if (!dir) return openProjects();
  const sheet = $('sheet'); sheet.replaceChildren();
  const crumb = mk('div', 'crumb'), info = mk('div', 'crumb'), list = mk('div', 'list'), foot = mk('div', 'foot');
  const close = mk('button', 'btn g', 'Cerrar'); close.onclick = closeModal;
  const upBtn = mk('button', 'btn', '⬆ Subir aquí');
  const fi = mk('input'); fi.type = 'file'; fi.multiple = true; fi.style.display = 'none';
  upBtn.onclick = () => fi.click();
  foot.append(close, upBtn, fi);
  sheet.append(mk('h3', '', 'Archivos'), crumb, info, list, foot);
  $('modal').classList.add('open');
  let cur = start || dir, maxUp = 0;
  const rel = p => p === dir ? '' : p.startsWith(dir + '/') ? p.slice(dir.length + 1) : p;
  async function go(p) {
    try {
      const d = await api('/api/files', {params: {path: p}});
      maxUp = d.max_upload || 0; cur = d.path; crumb.textContent = '📁 ' + base(dir) + (rel(cur) ? '/' + rel(cur) : '');
      list.replaceChildren();
      if (cur !== dir && d.parent) { const u = mk('button', 'item', '⬆️ ..'); u.onclick = () => go(d.parent); list.append(u); }
      for (const e of d.entries) {
        const b = mk('button', 'item');
        b.append(mk('span', 'n', (e.dir ? '📁 ' : '📄 ') + e.name), mk('span', 'm', e.dir ? '' : fsz(e.size)));
        b.onclick = e.dir ? () => go(e.path) : async () => {
          info.textContent = 'Descargando ' + e.name + '…';
          try { await download(e.path, e.name); info.textContent = '✓ ' + e.name; }
          catch (err) { info.textContent = '❌ ' + err.message; }
        };
        list.append(b);
      }
      if (!d.entries.length) list.append(mk('div', 'crumb', 'Carpeta vacía.'));
    } catch (e) { list.textContent = '❌ ' + e.message; }
  }
  fi.onchange = async () => {
    const added = [];
    for (const f of fi.files) {
      if (maxUp && f.size > maxUp) { info.textContent = `❌ ${f.name} pesa ${fsz(f.size)}; el máximo es ${fsz(maxUp)}.`; continue; }
      info.textContent = `Subiendo ${f.name} (${fsz(f.size)})…`;
      try { const r = await uploadFile(f, cur); added.push(rel(r.path)); }
      catch (err) { info.textContent = '❌ ' + f.name + ': ' + err.message; fi.value = ''; return go(cur); }
    }
    fi.value = '';
    if (added.length) {
      const t = $('input'); t.value += (t.value && !t.value.endsWith(' ') ? ' ' : '') + added.join(' ') + ' '; autosize();
      info.textContent = '✓ Subido. La ruta quedó escrita en tu mensaje.';
    }
    go(cur);
  };
  go(cur);
}

/* ---------- polling ---------- */
async function tick() {
  clearTimeout(timer);
  if (!sid) { render([]); return; }
  try {
    const [msgs, st, pr, qn] = await Promise.all([oc(`session/${sid}/message`), oc('session/status'), fetchPerms(), fetchQuestions()]);
    perms = pr; questions = qn;
    const s = st && st[sid];
    const serverBusy = !!s && s.type !== 'idle';
    if (serverBusy) seenBusy = true;
    busy = serverBusy || (sentAt && !seenBusy && Date.now() - sentAt < 4000);
    if (!busy) { sentAt = 0; seenBusy = false; }
    const sig = JSON.stringify(msgs) + busy + JSON.stringify(perms) + JSON.stringify(questions);
    if (sig !== lastSig) { lastSig = sig; render(msgs); }
    updateSend();
  } catch (e) {
    if (/not ?found|no encontr|404/i.test(e.message) && sid === ls.get(sidKey())) { newChat(); return; }
    toast('❌ ' + e.message);
  }
  if (busy || perms.length || questions.length) timer = setTimeout(tick, 1000);
}
function toast(t) { const col = $('col'); col.append(mk('div', 'err', t)); }

/* ---------- sesiones ---------- */
async function loadSessions() {
  const box = $('sessions'); box.replaceChildren();
  if (!dir) return;
  try {
    const list = (await oc('session')) || [];
    list.sort((a, b) => (b.time?.updated || 0) - (a.time?.updated || 0));
    for (const s of list) {
      const row = mk('div', 'sess' + (s.id === sid ? ' on' : ''));
      const t = mk('button', 't', s.title || 'Sin título'); t.onclick = () => openSession(s.id);
      const x = mk('button', 'x', '✕'); x.title = 'Borrar';
      x.onclick = async () => {
        if (!confirm('¿Borrar esta conversación?')) return;
        await oc('session/' + s.id, {method: 'DELETE'});
        if (s.id === sid) newChat(); else loadSessions();
      };
      row.append(t, x); box.append(row);
    }
  } catch (e) { box.textContent = '❌ ' + e.message; }
}
function openSession(id) {
  sid = id; rememberSid(); lastSig = ''; busy = false; perms = []; questions = []; sentAt = 0; seenBusy = false;
  closeSide(); tick(); loadSessions();
}
function newChat() { sid = null; rememberSid(); lastSig = ''; busy = false; perms = []; questions = []; closeSide(); render([]); loadSessions(); updateSend(); $('input').focus(); }

/* ---------- envío ---------- */
function updateSend() {
  const b = $('send');
  b.classList.toggle('stop', busy); b.textContent = busy ? '■' : '↑'; b.title = busy ? 'Detener' : 'Enviar';
}
async function send() {
  if (busy) { await stopRun(); return; }
  const text = $('input').value.trim();
  if (!text) return;
  if (!dir) return openProjects();
  const m = models.find(x => x.key === model);
  if (!m) return openModels();
  $('input').value = ''; autosize();
  try {
    if (!sid) { const s = await oc('session', {method: 'POST', body: {}}); sid = s.id; rememberSid(); loadSessions(); }
    sentAt = Date.now(); seenBusy = false; busy = true; updateSend();
    await oc(`session/${sid}/prompt_async`, {method: 'POST',
      body: {model: {providerID: m.providerID, modelID: m.modelID}, parts: [{type: 'text', text}]}});
    tick();
  } catch (e) { busy = false; sentAt = 0; updateSend(); toast('❌ ' + e.message); }
}

/* ---------- modelos ---------- */
async function loadModels() {
  const d = await api('/api/oc/provider');
  const connected = new Set(d.connected || []);
  models = [];
  for (const p of d.all || []) {
    if (!connected.has(p.id)) continue;
    for (const [id, m] of Object.entries(p.models || {})) {
      const free = !!m.cost && m.cost.input === 0 && m.cost.output === 0;
      models.push({key: p.id + '/' + id, providerID: p.id, modelID: id, name: m.name || id, provider: p.name || p.id, free, context: m.limit?.context || 0});
    }
  }
  const lims = knownLimits();
  for (const m of models) if (m.context) lims[m.key] = m.context;
  ls.set('oc_limits', JSON.stringify(lims));
  if (!models.some(m => m.key === model)) {
    const f = models.find(m => m.free) || models[0];
    model = f ? f.key : '';
  }
  paintModel();
}
function paintModel() {
  const m = models.find(x => x.key === model);
  $('modelBtn').textContent = '⚡ ' + (m ? m.name : 'Elegir modelo') + ' ▾';
}
/* ---------- test de velocidad ---------- */
let speed = {}; try { speed = JSON.parse(ls.get('oc_speed', '{}')); } catch {}
let testRun = null;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const fmt = ms => (ms / 1000).toFixed(1);
function spdLabel(m) {
  const s = speed[m.key];
  if (!s) return m.free ? 'gratis' : '';
  if (!s.ok) return '✗ ' + String(s.err || 'error').slice(0, 26);
  return `⏱ ${fmt(s.ttft)} / ${fmt(s.total)} s`;
}
// Crea una sesión descartable, manda un prompt mínimo y mide:
//   ttft  = tiempo hasta que aparece el primer texto/razonamiento
//   total = tiempo hasta que la sesión queda libre
async function testModel(m, run) {
  let id = null;
  try {
    const s = await oc('session', {method: 'POST', body: {title: '⏱ test de velocidad'}});
    id = s.id;
    const t0 = performance.now();
    await oc(`session/${id}/prompt_async`, {method: 'POST', body: {
      model: {providerID: m.providerID, modelID: m.modelID},
      parts: [{type: 'text', text: 'Respondé solo con la palabra: ok'}]}});
    let ttft = null;
    while (performance.now() - t0 < 60000) {
      if (run.stop) { await oc(`session/${id}/abort`, {method: 'POST'}).catch(() => {}); return {cancelled: true}; }
      await sleep(350);
      const [msgs, st] = await Promise.all([oc(`session/${id}/message`), oc('session/status')]);
      const asst = msgs.filter(x => x.info.role === 'assistant');
      const bad = asst.find(x => x.info.error);
      if (bad) return {ok: false, err: bad.info.error.data?.message || bad.info.error.name || 'error'};
      const hasText = asst.some(x => x.parts.some(p => (p.type === 'text' || p.type === 'reasoning') && p.text));
      if (hasText && ttft === null) ttft = performance.now() - t0;
      const cur = st && st[id], busy = !!cur && cur.type !== 'idle';
      if (!busy && asst.length && (hasText || asst.some(x => x.info.time?.completed))) {
        const total = performance.now() - t0;
        return {ok: true, ttft: ttft ?? total, total};
      }
    }
    await oc(`session/${id}/abort`, {method: 'POST'}).catch(() => {});
    return {ok: false, err: 'Timeout (60 s)'};
  } catch (e) { return {ok: false, err: e.message}; }
  finally { if (id) oc('session/' + id, {method: 'DELETE'}).catch(() => {}); }
}
const rank = m => { const s = speed[m.key]; return !s ? 1e8 : s.ok ? s.ttft : 1e9; };

function openModels() {
  const sheet = $('sheet'); sheet.replaceChildren();
  const q = mk('input'); q.type = 'text'; q.placeholder = 'Buscar modelo…';
  const info = mk('div', 'crumb');
  const list = mk('div', 'list');
  const sw = mk('label', 'sw'); const cb = mk('input'); cb.type = 'checkbox'; cb.checked = onlyFree;
  sw.append(cb, 'Solo gratuitos');
  let metas = {};
  const paint = () => {
    metas = {};
    const term = q.value.toLowerCase();
    let arr = models.filter(m => (m.name + m.provider).toLowerCase().includes(term));
    const free = arr.filter(m => m.free);
    const note = cb.checked && !free.length && arr.length;
    if (cb.checked && free.length) arr = free;
    const tested = arr.some(m => speed[m.key]);
    if (tested) arr = [...arr].sort((a, b) => rank(a) - rank(b));
    list.replaceChildren();
    if (!testRun) info.textContent = note ? 'No pude detectar cuáles son gratuitos; muestro todos (y no puedo probarlos sin riesgo de costo).'
      : tested ? '⏱ primer texto / total. Incluye el trabajo propio de OpenCode, no solo el modelo.' : '';
    let last = '';
    for (const m of arr) {
      if (!tested && m.provider !== last) { list.append(mk('div', 'lbl', m.provider)); last = m.provider; }
      const b = mk('button', 'item');
      const meta = mk('span', 'm', spdLabel(m)); metas[m.key] = meta;
      b.append(mk('span', 'n', (m.key === model ? '✓ ' : '') + m.name + (tested ? ' · ' + m.provider : '')), meta);
      b.onclick = () => { model = m.key; ls.set('oc_model', model); paintModel(); closeModal(); };
      list.append(b);
    }
    if (!arr.length) list.append(mk('div', 'crumb', 'Sin resultados.'));
  };
  q.oninput = paint;
  cb.onchange = () => { onlyFree = cb.checked; ls.set('oc_free', onlyFree ? '1' : '0'); paint(); };

  const c = mk('button', 'btn g', 'Cerrar'); c.onclick = closeModal;
  const tb = mk('button', 'btn', testRun ? '■ Detener' : '⏱ Probar');
  tb.onclick = async () => {
    if (testRun) { testRun.stop = true; return; }
    const term = q.value.toLowerCase();
    // Solo se prueban modelos gratuitos: probar uno de pago costaría plata
    const targets = models.filter(m => m.free && (m.name + m.provider).toLowerCase().includes(term));
    if (!targets.length) { info.textContent = 'No detecto modelos gratuitos para probar.'; return; }
    const run = testRun = {stop: false};
    tb.textContent = '■ Detener';
    for (let i = 0; i < targets.length && !run.stop; i++) {
      const m = targets[i];
      info.textContent = `Probando ${i + 1}/${targets.length}: ${m.name}…`;
      if (metas[m.key]) metas[m.key].textContent = '⏳';
      const r = await testModel(m, run);
      if (r.cancelled) break;
      speed[m.key] = {...r, at: Date.now()};
      ls.set('oc_speed', JSON.stringify(speed));
      if (metas[m.key]) metas[m.key].textContent = spdLabel(m);
    }
    const stopped = run.stop;
    testRun = null;
    if (sheet.contains(tb)) {
      tb.textContent = '⏱ Probar'; paint();
      info.textContent = stopped ? 'Prueba detenida.' : 'Listo: ordenados del más rápido al más lento.';
    }
  };
  const foot = mk('div', 'foot'); foot.append(c, tb, sw);
  sheet.append(mk('h3', '', 'Elegir modelo'), q, info, list, foot);
  $('modal').classList.add('open'); paint();
}

/* ---------- selector de carpetas ---------- */
async function openProjects(path = '') {
  const sheet = $('sheet'); sheet.replaceChildren();
  const q = mk('input'); q.type = 'text'; q.placeholder = 'Buscar carpeta por nombre…';
  const crumb = mk('div', 'crumb'); const list = mk('div', 'list');
  const foot = mk('div', 'foot');
  const close = mk('button', 'btn g', 'Cerrar'); close.onclick = closeModal;
  const choose = mk('button', 'btn', 'Usar esta carpeta');
  foot.append(close, choose);
  sheet.append(mk('h3', '', 'Elegir proyecto'), q, crumb, list, foot);
  $('modal').classList.add('open');
  let cur = path;

  const row = (e, label) => {
    const r = mk('div', 'item');
    const n = mk('button', 'n', (e.git ? '🌿 ' : '📁 ') + (label || e.name)); n.style.textAlign = 'left';
    n.onclick = () => { q.value = ''; go(e.path); };
    const p = mk('button', 'pick', 'Elegir'); p.onclick = () => pick(e.path);
    r.append(n, p); return r;
  };
  async function go(p) {
    try {
      const d = await api('/api/fs', {params: p ? {path: p} : {}});
      cur = d.path; crumb.textContent = d.path; list.replaceChildren();
      if (d.parent) { const up = mk('button', 'item', '⬆️ ..'); up.onclick = () => go(d.parent); list.append(up); }
      if (!p && recents().length) {
        list.append(mk('div', 'lbl', 'Recientes'));
        recents().forEach(r => list.append(row({path: r, name: base(r), git: false})));
        list.append(mk('div', 'lbl', 'Carpetas'));
      }
      d.dirs.forEach(e => list.append(row(e)));
      if (!d.dirs.length) list.append(mk('div', 'crumb', 'Sin subcarpetas.'));
    } catch (e) { list.textContent = '❌ ' + e.message; }
  }
  let deb;
  q.oninput = () => {
    clearTimeout(deb);
    deb = setTimeout(async () => {
      const t = q.value.trim();
      if (t.length < 2) return go(cur);
      try {
        const d = await api('/api/fs/search', {params: {q: t}});
        list.replaceChildren();
        d.results.forEach(e => list.append(row(e, e.path)));
        if (!d.results.length) list.append(mk('div', 'crumb', 'Sin resultados.'));
      } catch (e) { list.textContent = '❌ ' + e.message; }
    }, 250);
  };
  choose.onclick = () => pick(cur);
  go(path || (dir ? dir : ''));
}
const recents = () => { try { return JSON.parse(ls.get('oc_recent', '[]')); } catch { return []; } };
function pick(path) {
  dir = path; ls.set('oc_dir', dir);
  ls.set('oc_recent', JSON.stringify([path, ...recents().filter(r => r !== path)].slice(0, 6)));
  $('projBtn').textContent = '📁 ' + base(dir);
  closeModal();
  const last = ls.get(sidKey());
  last ? openSession(last) : newChat();
}

/* ---------- configuración ---------- */
async function openSettings() {
  const sheet = $('sheet'); sheet.replaceChildren();
  const info = mk('div', 'crumb'), box = mk('div', 'cfg'), foot = mk('div', 'foot');
  sheet.append(mk('h3', '', '⚙ Configuración'), info, box, foot);
  $('modal').classList.add('open');
  let c;
  try { c = await api('/api/config'); } catch (e) { info.textContent = '❌ ' + e.message; return; }
  info.textContent = 'Se guarda en ' + c.file + ' · versión ' + c.version;
  const f = {};
  const field = (key, label, help, type = 'text') => {
    box.append(mk('label', '', label));
    const i = mk('input'); i.type = type; i.value = c[key] ?? '';
    if (c.env_locked.includes(key)) { i.disabled = true; help = 'Lo fija una variable de entorno. ' + (help || ''); }
    box.append(i); if (help) box.append(mk('div', 'h', help)); f[key] = i;
  };
  const check = (key, label) => {
    const l = mk('label', 'ck'), i = mk('input'); i.type = 'checkbox'; i.checked = !!c[key];
    i.disabled = c.env_locked.includes(key); l.append(i, label); box.append(l); f[key] = i;
  };
  field('root', 'Carpeta raíz', 'OpenCode y la web solo ven lo que está adentro.');
  field('host', 'Dirección', `127.0.0.1 = solo esta máquina. Ahora escucha en ${c.running.host}. Se aplica al reiniciar.`);
  field('port', 'Puerto', `Ahora: ${c.running.port}. Se aplica al reiniciar.`, 'number');
  field('ntfy_url', 'Avisos por ntfy', 'El nombre del tema (ej. mi-tema-largo-y-secreto) o la URL completa. Vacío = sin avisos.');
  field('ntfy_token', 'Token de ntfy (si tu servidor lo pide)', c.has_ntfy_token ? 'Hay uno guardado: dejalo vacío para no cambiarlo.' : '');
  f.ntfy_token.value = '';
  field('ntfy_min_secs', 'Avisar solo si la tarea duró al menos (segundos)', '', 'number');
  check('ntfy_detail', 'Incluir en el aviso el comando o la ruta del permiso');
  field('upload_max_mb', 'Máximo por subida (MB)', '', 'number');
  field('download_max_mb', 'Máximo por descarga (MB)', '', 'number');
  const nt = mk('label', 'ck'), ntc = mk('input'); ntc.type = 'checkbox';
  nt.append(ntc, 'Generar un token de acceso nuevo (los otros dispositivos lo van a pedir)'); box.append(nt);
  const close = mk('button', 'btn g', 'Cerrar'); close.onclick = closeModal;
  const save = mk('button', 'btn', 'Guardar');
  save.onclick = async () => {
    const body = {};
    for (const [k, i] of Object.entries(f)) {
      if (i.disabled) continue;
      body[k] = i.type === 'checkbox' ? i.checked : i.type === 'number' ? Number(i.value) : i.value.trim();
    }
    if (!body.ntfy_token) delete body.ntfy_token;
    if (ntc.checked) body.new_token = true;
    save.disabled = true;
    try {
      const r = await api('/api/config', {method: 'PATCH', body});
      if (r.new_token) { token = r.new_token; ls.set('oc_token', token); }
      const restart = r.restart_fields.some(k => String(r[k]) !== String(r.running[k]));
      info.textContent = '✓ Guardado.' + (r.new_token ? ' Token nuevo: ' + r.new_token + ' (anotalo para los otros dispositivos).' : '')
        + (restart ? ' La dirección o el puerto cambian al reiniciar el servidor.' : '');
    } catch (e) { info.textContent = '❌ ' + e.message; }
    save.disabled = false;
  };
  foot.append(close, save);
}

/* ---------- UI varia ---------- */
function closeModal() { if (testRun) testRun.stop = true; $('modal').classList.remove('open'); }
function closeSide() { $('side').classList.remove('open'); $('scrim').classList.remove('open'); }
$('menu').onclick = () => { $('side').classList.add('open'); $('scrim').classList.add('open'); };
$('scrim').onclick = closeSide;
$('modal').onclick = e => { if (e.target.id === 'modal') closeModal(); };
$('newBtn').onclick = newChat;
$('projBtn').onclick = () => openProjects();
$('modelBtn').onclick = openModels;
$('filesBtn').onclick = () => openFiles();
$('cfgBtn').onclick = () => openSettings();
$('cpAll').onclick = async e => {
  const b = e.currentTarget, t = chatText();
  if (!t) return;
  b.textContent = (await copyText(t)) ? '✓' : '✗';
  setTimeout(() => { b.textContent = '📋'; }, 1200);
};
$('send').onclick = send;
const autosize = () => { const t = $('input'); t.style.height = 'auto'; t.style.height = t.scrollHeight + 'px'; };
$('input').addEventListener('input', autosize);
$('input').addEventListener('keydown', e => {
  const touch = matchMedia('(pointer:coarse)').matches;   // en el celu Enter = salto de línea
  if (e.key === 'Enter' && !e.shiftKey && !touch && !e.isComposing) { e.preventDefault(); send(); }
});
document.addEventListener('visibilitychange', () => { if (!document.hidden && sid) tick(); });

(async function init() {
  if (dir) $('projBtn').textContent = '📁 ' + base(dir);
  render([]); updateSend();
  try { await loadModels(); } catch (e) { toast('❌ ' + e.message); }
  if (!dir) return openProjects();
  const last = ls.get(sidKey());
  last ? openSession(last) : loadSessions();
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    if "--setup" in sys.argv or (not CONF_FILE.exists() and not os.environ.get("OPENCODE_CHAT_TOKEN") and sys.stdin.isatty()):
        setup_wizard()
        if "--setup" in sys.argv:
            sys.exit(0)
        CONFIG = {**read_config(), **env_overrides()}
        apply_config(CONFIG)
    ensure_opencode()
    print(f"🚀 OpenCode Chat {VERSION} en http://{HOST}:{PORT}")
    print(f"📂 Carpeta raíz: {ROOT}")
    if CONFIG["token"]:
        print(f"🔑 Token: el de {CONF_FILE}")
    else:
        print(f"🔑 Token (temporal, cambia en cada arranque): {TOKEN}")
    start_watcher()
    print("🔔 Avisos ntfy activados" if NTFY_URL else "🔕 Avisos ntfy apagados (se prenden desde ⚙)")
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Servidor detenido.")
