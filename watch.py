"""Vigila GrandJGames: avisa por ntfy cuando una categoria de singles que contiene
el texto buscado (por defecto 'hbp07') ya tiene productos listados."""
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

SITEMAP = "https://grandjgames.com/sitemap/categories"
# Set a vigilar: input manual (BUSCAR) > variable del repo (BUSCAR_VAR) > valor por defecto
BUSCAR = (os.environ.get("BUSCAR") or os.environ.get("BUSCAR_VAR") or "hbp07").strip().lower()
FILTRO_URL = (os.environ.get("FILTRO_URL") or "singles").lower()  # solo categorias cuya URL contenga esto
TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
MENSAJE_VACIO = "there are no products listed under this category"
SANIDAD = "hbp01"  # texto que siempre deberia estar en el sitemap (detecta bloqueos)
ESTADO_PATH = os.environ.get("ESTADO_PATH", "").strip()  # archivo de estado (persistido con actions/cache)
MOTIVO_SIN_MENSAJE = "la pagina ya no muestra el mensaje de categoria vacia"

# Codigo de carta del set: hbp07-001 ... hbp07-099 (el primero que aparezca basta)
PATRON_CODIGO = re.compile(re.escape(BUSCAR) + r"-0(?:0[1-9]|[1-9]\d)(?!\d)", re.I)


def descargar(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "set-stock-watcher (uso personal, 1 consulta cada 15 min)",
        "Accept": "text/html",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def buscar_categorias(html):
    """Devuelve {url: nombre} de los enlaces del sitemap que contienen BUSCAR (sin duplicados)."""
    cats = {}
    for href, inner in re.findall(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        texto = re.sub(r"<[^>]+>", "", inner).strip()
        if BUSCAR in (href + " " + texto).lower():
            cats.setdefault(urllib.parse.urljoin(SITEMAP, href), texto or href)
    return cats


def revisar_categoria(url):
    """Devuelve el motivo si la categoria ya tiene productos; None si sigue vacia o no se pudo verificar."""
    html = descargar(url)
    bajo = html.lower()
    m = PATRON_CODIGO.search(html)
    if m:
        return f"codigo encontrado: {m.group(0)}"
    # Solo se confia en la ausencia del mensaje si la pagina cargo de verdad (menciona el set)
    if BUSCAR in bajo and MENSAJE_VACIO not in bajo:
        return MOTIVO_SIN_MENSAJE
    return None


def ntfy(titulo, cuerpo, extra=None):
    headers = {"Title": titulo}
    headers.update(extra or {})
    req = urllib.request.Request(f"https://ntfy.sh/{TOPIC}", data=cuerpo.encode("utf-8"),
                                 method="POST", headers=headers)
    urllib.request.urlopen(req, timeout=30).read()


def en_ventana():
    """True solo en los minutos 0-14 de las horas 0, 6, 12 y 18 UTC."""
    ahora = datetime.datetime.now(datetime.timezone.utc)
    return ahora.hour % 6 == 0 and ahora.minute < 15


def alerta(msg):
    """Aviso de problemas, como maximo una vez cada ~6 horas."""
    if TOPIC and en_ventana():
        try:
            ntfy(f"{BUSCAR.upper()} bot: problema", msg, {"Tags": "warning"})
        except Exception as e:
            print("No se pudo enviar la alerta:", e)


def cargar_estado():
    """Lee el estado de ejecuciones anteriores; {} si no hay archivo o esta corrupto."""
    if not ESTADO_PATH:
        return {}
    try:
        with open(ESTADO_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def guardar_estado(estado):
    if not ESTADO_PATH:
        return
    try:
        carpeta = os.path.dirname(ESTADO_PATH)
        if carpeta:
            os.makedirs(carpeta, exist_ok=True)
        with open(ESTADO_PATH, "w", encoding="utf-8") as f:
            json.dump(estado, f, indent=2, sort_keys=True)
        salida = os.environ.get("GITHUB_OUTPUT")
        if salida:  # le indica al workflow que guarde el cache solo cuando hubo cambios
            with open(salida, "a", encoding="utf-8") as f:
                f.write("estado_cambio=true\n")
    except OSError as e:
        print("No se pudo guardar el estado:", e)


def avisar_una_vez(estado, clave, titulo, cuerpo, extra=None):
    """Envia un aviso informativo una sola vez por set (con estado) o cada ~6 h (sin estado).
    Devuelve True si el estado cambio."""
    if not TOPIC:
        return False
    vistos = estado.setdefault(BUSCAR, {})
    if ESTADO_PATH:
        if clave in vistos:
            return False
    elif not en_ventana():
        return False
    try:
        ntfy(titulo, cuerpo, extra)
    except Exception as e:  # se reintenta en la proxima ronda
        print(f"No se pudo enviar el aviso '{clave}':", e)
        return False
    vistos[clave] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    return True


def main():
    try:
        sitemap = descargar(SITEMAP)
    except Exception as e:  # sitio caido o bloqueo: se reintenta en la proxima ronda
        print("No se pudo descargar el sitemap:", e)
        alerta(f"No se pudo descargar el sitemap: {e}")
        return 0
    if SANIDAD not in sitemap.lower():
        print("El sitemap no trae el contenido esperado (posible bloqueo)")
        alerta("El sitemap no trae el contenido esperado (posible bloqueo).")
        return 0

    categorias = buscar_categorias(sitemap)
    if not categorias:
        print(f"La categoria '{BUSCAR}' aun no existe en el sitemap")
        return 0

    estado = cargar_estado()
    cambio = False
    singles = {u: n for u, n in categorias.items() if FILTRO_URL in u.lower()}

    if singles and ESTADO_PATH:  # aviso de categoria creada: solo tiene sentido con estado
        url, nombre = next(iter(singles.items()))
        cambio |= avisar_una_vez(estado, "categoria", f"Se creo la categoria {BUSCAR.upper()}",
                                 f"{nombre}\n{url}", {"Tags": "eyes", "Click": url})

    encontrado = None   # condicion 2: codigo de carta (confiable)
    posible = None      # condicion 1: falta el mensaje de vacio (puede ser falso positivo)
    for url, nombre in singles.items():
        try:
            motivo = revisar_categoria(url)
        except Exception as e:
            print(f"No se pudo revisar {url}: {e}")
            alerta(f"No se pudo revisar la categoria: {e}")
            continue
        print(f"{nombre} -> {motivo or 'sin productos aun'}")
        if motivo == MOTIVO_SIN_MENSAJE:
            posible = posible or (nombre, url, motivo)
        elif motivo:
            encontrado = (nombre, url, motivo)
            break
        time.sleep(1)

    if not encontrado:
        if posible:
            nombre, url, motivo = posible
            cambio |= avisar_una_vez(estado, "posible", f"Posibles productos del set {BUSCAR.upper()} (verificar)",
                                     f"{nombre}\n{motivo}\nNo se encontro codigo de carta; el bot sigue activo.\n{url}",
                                     {"Tags": "mag", "Click": url})
        if cambio:
            guardar_estado(estado)
        return 0

    if not TOPIC:
        print("Falta el secret NTFY_TOPIC")
        return 1
    nombre, url, motivo = encontrado
    ntfy("Ya hay productos del set en GrandJGames", f"{nombre}\n{motivo}\n{url}",
         {"Priority": "high", "Tags": "tada", "Click": url})
    estado.setdefault(BUSCAR, {})["productos"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    guardar_estado(estado)

    # Solo en ejecuciones automaticas y solo con codigo encontrado: se apaga para no avisar mas
    evento = os.environ.get("GITHUB_EVENT_NAME")
    if evento == "schedule" or (evento == "workflow_dispatch" and not os.environ.get("BUSCAR")):
        r = subprocess.run(["gh", "workflow", "disable", "watch.yml"], check=False)
        if r.returncode != 0:
            print("No se pudo desactivar el workflow (revisar GH_TOKEN y permiso actions: write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
