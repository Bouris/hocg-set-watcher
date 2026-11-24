"""Revisa el sitemap de categorias de GrandJGames y avisa por ntfy si aparece 'hBP07'."""
import os, re, subprocess, sys, urllib.parse, urllib.request

URL = "https://grandjgames.com/sitemap/categories"
BUSCAR = (os.environ.get("BUSCAR") or "hbp07").lower()
TOPIC = os.environ.get("NTFY_TOPIC", "").strip()


def descargar():
    req = urllib.request.Request(URL, headers={
        "User-Agent": "hBP07-set-watcher (uso personal, 1 consulta cada 15 min)",
        "Accept": "text/html",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def buscar(html):
    hallados = []
    for href, inner in re.findall(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        texto = re.sub(r"<[^>]+>", "", inner).strip()
        if BUSCAR in (href + " " + texto).lower():
            hallados.append((texto or href, urllib.parse.urljoin(URL, href)))
    return hallados


def avisar(hallados):
    cuerpo = "\n".join(f"{n}: {u}" for n, u in hallados)
    req = urllib.request.Request(
        f"https://ntfy.sh/{TOPIC}", data=cuerpo.encode("utf-8"), method="POST",
        headers={"Title": "hBP07 disponible en GrandJGames", "Priority": "high",
                 "Tags": "tada", "Click": hallados[0][1]})
    urllib.request.urlopen(req, timeout=30).read()


def main():
    try:
        html = descargar()
    except Exception as e:  # sitio caido o bloqueo: se reintenta en la proxima ronda
        print("No se pudo descargar:", e)
        return 0
    hallados = buscar(html)
    print(f"Coincidencias para '{BUSCAR}':", hallados or "ninguna")
    if not hallados:
        return 0
    if not TOPIC:
        print("Falta el secret NTFY_TOPIC")
        return 1
    avisar(hallados)
    # Solo en ejecuciones programadas: se apaga solo para no avisar mas
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        subprocess.run(["gh", "workflow", "disable", "watch.yml"], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
