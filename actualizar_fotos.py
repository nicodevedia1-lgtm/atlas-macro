"""
actualizar_fotos.py — Atlas Macro

Busca una foto de cada país en Wikimedia Commons (el banco de imágenes libres de
Wikipedia) y genera fotos.json con el link, el autor y la licencia de cada una.

  1. Wikidata: la "imagen principal" de cada país y la de su capital.
  2. Se descartan mapas, banderas, escudos y dibujos (solo fotos .jpg).
  3. Commons: la versión de 960 px de ancho, el autor y la licencia (para el crédito).

Las fotos no se guardan en el repositorio: la página las carga desde Wikimedia.
En ELEGIDAS se puede forzar a mano la foto de un país (el nombre del archivo en Commons).

Uso:
    python3 actualizar_fotos.py
    python3 actualizar_fotos.py -v
"""

import html
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from curl_cffi import requests

CARPETA = Path(__file__).parent
ARCHIVO_SALIDA = CARPETA / "fotos.json"
VER = "-v" in sys.argv
# Wikimedia pide identificarse con un User-Agent que diga quién hace los pedidos
AGENTE = {"User-Agent": "AtlasMacro/1.0 (https://github.com/nicodevedia1-lgtm/atlas-macro)"}
sesion = requests.Session()

SPARQL = "https://query.wikidata.org/sparql"
COMMONS = "https://commons.wikimedia.org/w/api.php"
CONSULTA = """
SELECT ?iso ?img ?capLabel ?capImg WHERE {
  ?c wdt:P298 ?iso .
  FILTER NOT EXISTS { ?c wdt:P576 ?fin }
  OPTIONAL { ?c wdt:P18 ?img }
  OPTIONAL { ?c wdt:P36 ?cap . ?cap wdt:P18 ?capImg . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "es,en". }
}
"""
# fotos elegidas a mano (archivo de Commons, sin "File:"), para los países donde la automática no convence
ELEGIDAS = {}

# fuera: mapas, banderas, escudos, imágenes satelitales y fotos de noticias (cuarentenas, protestas...)
NO_FOTO = re.compile(r"map|mapa|flag|bandera|coat|escudo|emblem|seal|locator|location|orthographic|logo|diagram|"
                     r"satellite|satelit|nasa|blue marble|globe|cuarentena|quarantine|covid|protest|riot|\.svg$|\.png$|\.gif$|\.tif", re.I)


def archivo_de(url):
    """'http://commons.wikimedia.org/wiki/Special:FilePath/Buenos%20Aires.jpg' → 'Buenos Aires.jpg'"""
    return unquote(url.rsplit("/", 1)[-1]).replace("_", " ")


def candidatos():
    """{ISO3: [(archivo, lugar), ...]} en orden de preferencia: la imagen del país, después la de la capital."""
    r = sesion.get(SPARQL, params={"query": CONSULTA, "format": "json"}, headers=AGENTE, timeout=120)
    r.raise_for_status()
    salida = {}
    for f in r.json()["results"]["bindings"]:
        iso = f["iso"]["value"].upper()
        lista = salida.setdefault(iso, [])
        if "img" in f:
            a = archivo_de(f["img"]["value"])
            if (a, "") not in lista:
                lista.insert(sum(1 for _, l in lista if l == ""), (a, ""))      # las del país, primero
        if "capImg" in f:
            a = archivo_de(f["capImg"]["value"])
            lugar = f.get("capLabel", {}).get("value", "")
            if all(a != x for x, _ in lista):
                lista.append((a, lugar))
    return salida


def datos_commons(archivos):
    """{archivo: {url, ancho, alto, autor, licencia, pagina}} pidiendo de a 40."""
    salida = {}
    archivos = list(archivos)
    for i in range(0, len(archivos), 40):
        grupo = archivos[i:i + 40]
        r = sesion.get(COMMONS, headers=AGENTE, timeout=120, params={
            "action": "query", "format": "json", "prop": "imageinfo", "iiprop": "url|size|extmetadata",
            "iiurlwidth": 960, "titles": "|".join("File:" + a for a in grupo)})
        r.raise_for_status()
        q = r.json().get("query", {})
        normal = {n["to"]: n["from"] for n in q.get("normalized", [])}
        for p in q.get("pages", {}).values():
            ii = (p.get("imageinfo") or [None])[0]
            if not ii:
                continue
            md = ii.get("extmetadata", {})
            autor = html.unescape(re.sub(r"<[^>]+>", "", md.get("Artist", {}).get("value", ""))).strip()
            nombre = normal.get(p["title"], p["title"])[5:]
            salida[nombre] = {"url": ii.get("thumburl") or ii["url"], "ancho": ii.get("thumbwidth"), "alto": ii.get("thumbheight"),
                              "autor": re.sub(r"\s+", " ", autor)[:80] or "Autor en Wikimedia Commons",
                              "licencia": md.get("LicenseShortName", {}).get("value", ""),
                              "pagina": ii.get("descriptionurl")}
        time.sleep(0.5)
    return salida


def main():
    print("Atlas Macro · fotos de los países (Wikimedia Commons)")
    paises = json.load(open(CARPETA / "datos.json", encoding="utf-8"))["paises"]
    todos = candidatos()
    elegir = {}
    for iso in paises:
        lista = [(a, l) for a, l in todos.get(iso, []) if not NO_FOTO.search(a)]
        if iso in ELEGIDAS:
            lista = [(ELEGIDAS[iso], "")] + lista
        if lista:
            elegir[iso] = lista[:3]
    print(f"· {len(elegir)} países con alguna foto candidata en Wikidata")
    info = datos_commons({a for l in elegir.values() for a, _ in l})
    salida = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "fuente": "Wikimedia Commons (licencias libres; autor y licencia en cada foto)", "paises": {}}
    for iso, lista in elegir.items():
        for a, lugar in lista:
            d = info.get(a)
            # que sea apaisada (para el banner) y que tenga licencia
            if d and d["licencia"] and d["ancho"] and d["alto"] and d["ancho"] >= d["alto"] * 1.15:
                salida["paises"][iso] = {**d, "lugar": lugar, "archivo": a}
                break
        if VER:
            x = salida["paises"].get(iso)
            print(f"     {iso}: {x['archivo'][:60] + ' · ' + x['licencia'] if x else '—'}")
    with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as archivo:
        json.dump(salida, archivo, ensure_ascii=False, separators=(",", ":"))
    print(f"✅ fotos.json listo · {len(salida['paises'])} países con foto de {len(paises)} · "
          f"{ARCHIVO_SALIDA.stat().st_size / 1024:,.0f} KB")


if __name__ == "__main__":
    main()
