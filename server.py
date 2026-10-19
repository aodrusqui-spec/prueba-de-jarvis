#!/usr/bin/env python3
"""HOLO — hand-gesture control deck (Jarvis V7 prototype). Stdlib only, port 4890.

Serves the deck page + the note cards it manipulates. Hand tracking is Google
MediaPipe (Apache-2.0) loaded from CDN in the page; every gesture on top is ours,
written clean — no third-party gesture code. Camera frames never leave the page.

Endpoints:
  GET  /               → holo.html
  GET  /api/notes      → [{name, title, body}] from the folder in holo.json
                         (falls back to ./sample-notes so it runs instantly)
  POST /api/state      → future Jarvis hook: writes state/holo-state.json so the
                         big brain can react to what the hands did ("Card pinned,
                         sir"). Nothing reads it yet by design — prototype stays
                         standalone until proven.
"""
import json, os, time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("HOLO_PORT", "4890"))

# the page, cached at boot: long-lived processes on macOS can silently lose file
# access (TCC) hours in — serving the boot-time copy beats a 500 "missing" page
try:
    PAGE_CACHE = [open(os.path.join(ROOT, "holo.html"), "rb").read()]
except OSError:
    PAGE_CACHE = [None]

def notes_dir():
    try:
        cfg = json.load(open(os.path.join(ROOT, "holo.json")))
        d = os.path.expanduser(cfg.get("folder", ""))
        if d and os.path.isdir(d):
            return d
    except Exception:
        pass
    return os.path.join(ROOT, "sample-notes")

def _note(path, n):
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return None
    lines = [l for l in text.splitlines() if l.strip()]
    title = (lines[0].lstrip("# ").strip() if lines else n)[:48] or n
    rest = [l for l in lines[1:] if not l.startswith("#")]
    return {"name": n, "title": title, "body": "\n".join(rest)[:420],
            "full": "\n".join(lines[1:])[:4000]}

def load_notes(limit=18):
    out = []
    d = notes_dir()
    try:
        for n in sorted(x for x in os.listdir(d) if x.endswith((".md", ".txt")))[:limit]:
            note = _note(os.path.join(d, n), n)
            if note: out.append(note)
    except OSError:
        pass
    return out

def load_tree(limit_files=14):
    """One level deep: subfolders become ORBS; loose root files gather under 'NOTES'."""
    d = notes_dir()
    tree = []
    try:
        entries = sorted(os.listdir(d))
        loose = []
        for e in entries:
            p = os.path.join(d, e)
            if os.path.isdir(p) and not e.startswith("."):
                files = []
                for n in sorted(x for x in os.listdir(p) if x.endswith((".md", ".txt")))[:limit_files]:
                    note = _note(os.path.join(p, n), n)
                    if note: files.append(note)
                if files:
                    tree.append({"kind": "folder", "name": e.upper()[:22], "files": files})
            elif e.endswith((".md", ".txt")):
                note = _note(p, e)
                if note: loose.append(note)
        if loose:
            tree.append({"kind": "folder", "name": "NOTES", "files": loose[:limit_files]})
    except OSError:
        pass
    return tree


def ask_ollama(event_data):
    payload = {
        "model": "qwen2.5-coder:3b",
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Eres JARVIS, el asistente personal del usuario. "
"Responde siempre en español, de forma natural, clara y educada. "
"Dirígete al usuario como 'señor' en todas tus respuestas. "
"Utiliza expresiones naturales como 'Sí, señor', "
"'Por supuesto, señor' o 'Ahora mismo, señor', "
"cuando encajen con la respuesta. "
"No repitas 'señor' en todas las frases ni de forma forzada. "
"Responde directamente a lo que te pregunten. "
"No afirmes haber realizado acciones que no has hecho."
                )
            },
            {
                "role": "user",
                "content": json.dumps(event_data, ensure_ascii=False)
            }
        ]
    }

    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))

    return result.get("message", {}).get(
        "content", "No se recibió respuesta de Ollama."
    )

def get_time_or_date(question):
    from datetime import datetime

    q = question.lower()
    ahora = datetime.now().astimezone()

    palabras_hora = ["hora", "qué hora", "que hora"]
    palabras_fecha = ["fecha", "qué día", "que dia", "día de hoy", "dia de hoy"]

    if any(p in q for p in palabras_hora):
        return f"Son las {ahora.strftime('%H:%M')}, señor."

    if any(p in q for p in palabras_fecha):
        return f"Hoy es {ahora.strftime('%d/%m/%Y')}, señor."

    return None


def inicializar_memoria():
    import sqlite3
    import os

    ruta_db = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "jarvis.db"
    )

    with sqlite3.connect(ruta_db) as conexion:
        conexion.execute("""
            CREATE TABLE IF NOT EXISTS recuerdos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contenido TEXT NOT NULL,
                fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

def guardar_recuerdo(contenido):
    import sqlite3
    import os

    ruta_db = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "jarvis.db"
    )

    with sqlite3.connect(ruta_db) as conexion:
        conexion.execute(
            "INSERT INTO recuerdos (contenido) VALUES (?)",
            (contenido,)
        )

    print("Recuerdo guardado:", contenido)

def obtener_recuerdos():
    import sqlite3
    import os

    ruta_db = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "jarvis.db"
    )

    with sqlite3.connect(ruta_db) as conexion:
        return conexion.execute(
            "SELECT id, contenido FROM recuerdos ORDER BY id DESC"
        ).fetchall()




def buscar_wikipedia(pregunta):
    import json
    import sqlite3
    import os
    import urllib.parse
    import urllib.request
    

    consulta = urllib.parse.urlencode({
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": pregunta,
        "gsrlimit": 3,
        "prop": "extracts",
        "exintro": 1,
        "explaintext": 1,
        "exchars": 1200
    })

    url = (
        "https://es.wikipedia.org/w/api.php?"
        + consulta
    )

    peticion = urllib.request.Request(
        url,
        headers={"User-Agent": "HOLOHands/1.0"}
    )

    with urllib.request.urlopen(peticion, timeout=10) as respuesta:
        datos = json.loads(respuesta.read().decode("utf-8"))

    paginas = datos.get("query", {}).get("pages", {})

    resultados = []
    for pagina in paginas.values():
        titulo = pagina.get("title", "")
        texto = pagina.get("extract", "").strip()

        if texto:
            resultados.append(f"{titulo}: {texto}")

    return "\n\n".join(resultados)



def buscar_noticias(tema="actualidad"):
    import urllib.request
    import urllib.parse
    import xml.etree.ElementTree as ET
    import html

    consulta = urllib.parse.quote_plus(tema)
    url = (
        "https://news.google.com/rss/search?q="
        + consulta
        + "&hl=es&gl=ES&ceid=ES:es"
    )

    peticion = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
    )

    try:
        with urllib.request.urlopen(peticion, timeout=30) as respuesta:
            datos = respuesta.read()

        raiz = ET.fromstring(datos)
        noticias = []

        for item in raiz.findall(".//item")[:5]:
            titulo = html.unescape(
                item.findtext("title", "Sin título")
            )
            enlace = item.findtext("link", "")
            fecha = item.findtext("pubDate", "")

            noticias.append(
                f"- {titulo}\n"
                f"  Fecha: {fecha}\n"
                f"  Enlace: {enlace}"
            )

        return "\n\n".join(noticias)

    except Exception as e:
        return "ERROR AL BUSCAR NOTICIAS: " + str(e)


        raiz = ET.fromstring(datos)
        noticias = []

        for item in raiz.findall(".//item")[:5]:
            titulo = html.unescape(
                item.findtext("title", "Sin título")
            )
            enlace = item.findtext("link", "")
            fecha = item.findtext("pubDate", "")

            noticias.append(
                f"- {titulo}\n"
                f"  Fecha: {fecha}\n"
                f"  Enlace: {enlace}"
            )

        return "\n\n".join(noticias)

    except Exception as e:
        return "ERROR AL BUSCAR NOTICIAS: " + str(e)


def ask_ollama_question(question):
    respuesta_tiempo = get_time_or_date(question)
    if respuesta_tiempo:
        return respuesta_tiempo

    q = question.lower().strip()

    if q.startswith("recuerda que "):
        recuerdo = question.strip()[len("recuerda que "):].strip()
        if recuerdo:
            guardar_recuerdo(recuerdo)
            return "Entendido, señor. Lo recordaré."

    if q.startswith("recuerda "):
        recuerdo = question.strip()[len("recuerda "):].strip()
        if recuerdo:
            guardar_recuerdo(recuerdo)
            return "Entendido, señor. Lo recordaré."    

    if "recuerdas" in q or "recuerdo" in q:
        recuerdos = obtener_recuerdos()

        if recuerdos:
            lista = "\n".join(
                f"- {contenido}" for _, contenido in recuerdos
            )
            return "Esto es lo que recuerdo, señor:\n" + lista

        return "Todavía no tengo recuerdos guardados, señor."


   
    palabras_noticias = (
        "noticias", "novedades", "actualidad",
        "qué ha pasado hoy", "que ha pasado hoy",
        "últimas noticias", "ultimas noticias"
    )

    if any(palabra in q for palabra in palabras_noticias):
        tema = question.strip()

        for palabra in palabras_noticias:
            tema = tema.replace(palabra, "").strip()

        tema = tema or "actualidad"
        informacion = buscar_noticias(tema)
        print("DEBUG NOTICIAS:", tema, informacion[:500])

        if informacion:
            question = (
                "Responde en español y llama al usuario señor. "
                "Resume las noticias de forma sencilla en un máximo "
                "de 3 frases. No inventes datos e incluye los enlaces.\n\n"
                f"Pregunta: {question}\n\n"
                f"Noticias encontradas:\n{informacion}"
            )
        else:
            question = (
                "Responde en español y llama al usuario señor. "
                "Explica brevemente que no se pudieron consultar "
                "las noticias en este momento. No inventes noticias."
            )

    else:
        try:
            informacion = buscar_wikipedia(question)
        except Exception:
            informacion = ""

        if informacion:
            question = (
                "Responde en español y llama al usuario señor. "
                "Utiliza esta información de Wikipedia para contestar "
                "a la pregunta. Si no contiene la respuesta, dilo "
                "claramente y no inventes datos.\n\n"
                f"Pregunta: {question}\n\n"
                f"Información de Wikipedia:\n{informacion}"
            )


    payload = {
        "model": "qwen2.5-coder:3b",
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Eres JARVIS, el asistente de H.O.L.O Hands. "
                    "Responde en español, de forma clara y sencilla. "
                    "Contesta a las preguntas del usuario. "
                    "No afirmes haber realizado acciones que no has hecho."
                )
            },
            {
                "role": "user",
                "content": question
            }
        ]
    }

    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))

    return result.get("message", {}).get(
        "content", "No se recibió respuesta de Ollama."
    )

inicializar_memoria()
print("Memoria de JARVIS preparada.")

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if ctype.startswith("text/html"):
            self.send_header("Cache-Control", "no-store")   # a stale cached page hid real fixes once
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    MIME = {".mjs": "text/javascript", ".js": "text/javascript",
            ".wasm": "application/wasm", ".task": "application/octet-stream",
            ".glb": "model/gltf-binary"}

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/holo.html"):
            try:
                body = open(os.path.join(ROOT, "holo.html"), "rb").read()
                PAGE_CACHE[0] = body
            except OSError:
                body = PAGE_CACHE[0]          # disk access lost (TCC) — serve the boot copy
            if body is None:
                return self._send(500, {"error": "holo.html missing"})
            return self._send(200, body, "text/html; charset=utf-8")
        if p == "/api/props":
            # PROPS: any .glb dropped into props/ becomes a grabbable 3D object
            try:
                names = sorted(x for x in os.listdir(os.path.join(ROOT, "props"))
                               if x.endswith(".glb"))[:6]
            except OSError:
                names = []
            return self._send(200, names)
        if p.startswith(("/vendor/", "/props/")):
            # self-hosted tracking libs: no CDN in the path, so ad-block extensions
            # and offline machines can't kill the hand tracking
            safe = os.path.normpath(p.lstrip("/"))
            if safe.startswith(("vendor", "props")) and ".." not in safe:
                full = os.path.join(ROOT, safe)
                if os.path.isfile(full):
                    ext = os.path.splitext(full)[1]
                    try:
                        return self._send(200, open(full, "rb").read(),
                                          self.MIME.get(ext, "application/octet-stream"))
                    except OSError:
                        pass
            return self._send(404, {"error": "not found"})
        if p == "/api/notes":
            return self._send(200, load_notes())
       
        if p == "/api/tree":
            return self._send(200, load_tree())

        if p == "/api/jarvis":
            try:
                state_file = os.path.join(ROOT, "state", "holo-state.json")
                with open(state_file, encoding="utf-8") as f:
                    event_data = json.load(f)

                answer = ask_ollama(event_data)

                return self._send(200, {
                    "event": event_data.get("event"),
                    "card": event_data.get("card"),
                    "answer": answer
                })
            except FileNotFoundError:
                return self._send(404, {
                    "error": "Todavia no hay eventos guardados"
                })
            except Exception as e:
                return self._send(500, {"error": str(e)})

        return self._send(404, {"error": "not found"})

    def do_POST(self):

        p = self.path.split("?")[0]
        if p == "/api/diag":              # the page phones home its own crash report
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                data = {}
            data["ts"] = time.time()
            try:
                os.makedirs(os.path.join(ROOT, "state"), exist_ok=True)
                with open(os.path.join(ROOT, "state", "holo-diag.json"), "w") as f:
                    json.dump(data, f, indent=2)
            except OSError:
                pass
            return self._send(200, {"ok": True})


        if p == "/api/ask":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}")
                question = str(data.get("question", "")).strip()

                if not question:
                    return self._send(400, {"error": "Escribe una pregunta"})

                answer = ask_ollama_question(question)
                return self._send(200, {"answer": answer})

            except Exception as e:
                return self._send(500, {"error": str(e)})


        if p != "/api/state":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            data = {}
        data["ts"] = time.time()
        try:
            os.makedirs(os.path.join(ROOT, "state"), exist_ok=True)
            tmp = os.path.join(ROOT, "state", ".holo-state.tmp")
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, os.path.join(ROOT, "state", "holo-state.json"))
        except OSError:
            pass
        return self._send(200, {"ok": True})

if __name__ == "__main__":
    print(f"HOLO deck on http://localhost:{PORT}  ·  notes: {notes_dir()}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
