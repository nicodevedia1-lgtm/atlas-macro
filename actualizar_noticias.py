"""
actualizar_noticias.py — Atlas Macro

Hace dos cosas y genera noticias.json:
  1. Elecciones: busca en Wikidata las elecciones nacionales de los próximos 12 meses.
  2. Gobernantes: compara quién gobierna según Wikidata con lo que dice curado.json.
     Si no coinciden, lo anota en alertas.md (GitHub te lo manda por mail).

Las noticias de cada país no se bajan: la página tiene un link a Google Noticias.

Uso:
    python3 actualizar_noticias.py
"""

import difflib
import json
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from curl_cffi import requests

CARPETA = Path(__file__).parent
ARCHIVO_SALIDA = CARPETA / "noticias.json"
ARCHIVO_ALERTAS = CARPETA / "alertas.md"

URL_WIKIDATA = "https://query.wikidata.org/sparql"

# Wikidata pide que cada programa se identifique
CABECERAS = {"User-Agent": "AtlasMacro/1.0 (https://github.com/nicodevedia1-lgtm/atlas-macro)",
             "Accept": "application/sparql-results+json"}

HOY = date.today()


# ═══════════════════════════════════════════════════════════════════
# HERRAMIENTAS
# ═══════════════════════════════════════════════════════════════════

def consulta_wikidata(sparql):
    """Manda una consulta SPARQL a Wikidata y devuelve la lista de resultados."""
    for intento in range(3):
        try:
            r = requests.get(URL_WIKIDATA, params={"query": sparql, "format": "json"},
                             headers=CABECERAS, timeout=90)
            if r.status_code == 200:
                return r.json()["results"]["bindings"]
            print(f"     ⚠️  Wikidata respondió {r.status_code}")
        except Exception as error:
            print(f"     ⚠️  {error}")
        time.sleep(10)
    return None


def valor(fila, campo):
    """En las respuestas de Wikidata cada dato viene como {"value": ...}."""
    return fila[campo]["value"] if campo in fila else None


def normalizar(texto):
    """'Volodímir Zelenski' → 'volodimir zelenski' (sin acentos ni mayúsculas)."""
    sin_acentos = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sin_acentos.lower()


def palabras(texto):
    """Palabras de 4 letras o más, sin acentos: sirven para comparar apellidos."""
    limpio = "".join(c if c.isalpha() else " " for c in normalizar(texto))
    return [p for p in limpio.split() if len(p) >= 4]


# ═══════════════════════════════════════════════════════════════════
# 1. ELECCIONES
# ═══════════════════════════════════════════════════════════════════

def buscar_elecciones():
    desde = (HOY - timedelta(days=14)).isoformat()
    hasta = (HOY + timedelta(days=365)).isoformat()
    sparql = f"""
    SELECT ?eleccion ?eleccionLabel ?fecha ?precision ?iso WHERE {{
      ?eleccion wdt:P1001 ?pais ;
                p:P585/psv:P585 [ wikibase:timeValue ?fecha ; wikibase:timePrecision ?precision ] ;
                wdt:P31/wdt:P279* wd:Q40231 .
      ?pais wdt:P298 ?iso .
      FILTER(?fecha >= "{desde}T00:00:00Z"^^xsd:dateTime && ?fecha <= "{hasta}T00:00:00Z"^^xsd:dateTime)
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "es,en". }}
    }}"""
    filas = consulta_wikidata(sparql)
    if filas is None:
        return None

    por_pais_y_fecha = {}
    for fila in filas:
        nombre = valor(fila, "eleccionLabel")
        n = normalizar(nombre)
        if any(x in n for x in ("parcial", "by-election", "municipal", "local", "regional", "primaria", "primary",
                                "gubernatorial", "state legislative")):
            continue
        sin_nombre = nombre.startswith("Q") and nombre[1:].isdigit()     # Wikidata no tiene el nombre cargado
        eleccion = {
            "iso": valor(fila, "iso"),
            "fecha": valor(fila, "fecha")[:10],
            "precision": int(valor(fila, "precision")),   # 11 = día, 10 = mes, 9 = año
            "nombre": "" if sin_nombre else nombre,
        }
        clave = (eleccion["iso"], eleccion["fecha"])
        # Si hay varias el mismo día en el mismo país, nos quedamos con una:
        # mejor un nombre en castellano (los ingleses empiezan con el año) y, si no, el más corto
        puntaje = lambda e: (e["nombre"] == "", e["nombre"][:1].isdigit(), len(e["nombre"]))
        if clave not in por_pais_y_fecha or puntaje(eleccion) < puntaje(por_pais_y_fecha[clave]):
            por_pais_y_fecha[clave] = eleccion
    elecciones = sorted(por_pais_y_fecha.values(), key=lambda e: e["fecha"])
    for e in elecciones:
        if not e["nombre"]:
            e["nombre"] = "Elecciones"
    return elecciones


# ═══════════════════════════════════════════════════════════════════
# 2. GOBERNANTES
# ═══════════════════════════════════════════════════════════════════

def buscar_gobernantes(isos):
    valores = " ".join(f'"{iso}"' for iso in isos)
    sparql = f"""
    SELECT ?iso ?cargo ?personaLabel WHERE {{
      VALUES ?iso {{ {valores} }}
      ?pais wdt:P298 ?iso .
      {{ ?pais p:P35 ?st . ?st ps:P35 ?persona . BIND("estado" AS ?cargo) FILTER NOT EXISTS {{ ?st pq:P582 ?fin }} }}
      UNION
      {{ ?pais p:P6 ?st . ?st ps:P6 ?persona . BIND("gobierno" AS ?cargo) FILTER NOT EXISTS {{ ?st pq:P582 ?fin }} }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "es,en". }}
    }}"""
    filas = consulta_wikidata(sparql)
    if filas is None:
        return None
    gobernantes = {}
    for fila in filas:
        nombre = valor(fila, "personaLabel")
        if nombre.startswith("Q") and nombre[1:].isdigit():
            continue          # Wikidata no tiene el nombre cargado
        gobernantes.setdefault(valor(fila, "iso"), set()).add(nombre)
    return gobernantes


def comparar_gobernantes(curado, gobernantes):
    """Devuelve la lista de países donde el líder de curado.json no aparece en Wikidata."""
    genericas = {"consejo", "federal", "junta", "militar", "funciones", "transicion", "capitanes",
                 "regentes", "presidencia", "lord", "jeque", "emir", "principe", "sultan", "alberto"}
    diferencias = []
    for iso, ficha in curado.items():
        pol = ficha.get("politica")
        if not pol or iso not in gobernantes:
            continue
        nuestras = [p for p in palabras(pol["lider"]) if p not in genericas]
        if not nuestras:
            continue          # el "líder" no es una persona (por ejemplo, el Consejo Federal suizo)
        suyas = [p for nombre in gobernantes[iso] for p in palabras(nombre)]
        coincide = any(difflib.SequenceMatcher(None, a, b).ratio() >= 0.8 for a in nuestras for b in suyas)
        if not coincide:
            diferencias.append((iso, ficha["nombre"], pol["lider"], sorted(gobernantes[iso])))
    return diferencias


# ═══════════════════════════════════════════════════════════════════
# 3. TITULARES
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
# PROGRAMA PRINCIPAL
# ═══════════════════════════════════════════════════════════════════

def main():
    curado = json.load(open(CARPETA / "curado.json", encoding="utf-8"))["paises"]
    anterior = {}
    if ARCHIVO_SALIDA.exists():
        anterior = json.load(open(ARCHIVO_SALIDA, encoding="utf-8"))

    print("Atlas Macro · noticias\n")

    print("1/2  Elecciones (Wikidata)")
    elecciones = buscar_elecciones()
    if elecciones is None:
        print("     ⚠️  No pude consultar Wikidata: dejo las elecciones anteriores")
        elecciones = anterior.get("elecciones", [])
    else:
        print(f"     {len(elecciones)} elecciones nacionales encontradas")

    print("2/2  Gobernantes (Wikidata)")
    gobernantes = buscar_gobernantes(list(curado))
    alertas = []
    foto_anterior = anterior.get("gobernantes")      # lo que decía Wikidata la vez pasada
    foto_nueva = foto_anterior or {}
    if gobernantes is None:
        print("     ⚠️  No pude consultar Wikidata")
    else:
        foto_nueva = {iso: sorted(nombres) for iso, nombres in gobernantes.items()}
        diferencias = comparar_gobernantes(curado, gobernantes)
        if foto_anterior is None:
            # Primera vez: Wikidata tiene algunos datos viejos, así que mostramos todo una sola vez
            print(f"     Primera revisión: {len(diferencias)} países no coinciden (revisalos una vez)")
            for iso, nombre, nuestro, suyos in diferencias:
                alertas.append(f"- **{nombre}**: la ficha dice *{nuestro}*, Wikidata dice *{', '.join(suyos)}*")
        else:
            # Después: solo avisamos si Wikidata CAMBIÓ algo desde la vez anterior.
            # Así no molestan los datos viejos de Wikidata, pero sí detectamos los cambios de gobierno.
            cambiaron = {iso for iso in foto_nueva if foto_nueva[iso] != foto_anterior.get(iso, foto_nueva[iso])}
            for iso, nombre, nuestro, suyos in diferencias:
                if iso in cambiaron:
                    antes = ", ".join(foto_anterior.get(iso, []))
                    alertas.append(f"- **{nombre}**: Wikidata cambió (antes: *{antes}*; ahora: *{', '.join(suyos)}*). "
                                   f"La ficha dice *{nuestro}*.")
            print(f"     {len(cambiaron)} cambios en Wikidata · {len(alertas)} fichas para revisar")

    # Elecciones de las últimas dos semanas: hay que revisar esas fichas
    recientes = [e for e in elecciones if e["fecha"] <= HOY.isoformat() and e["precision"] >= 11]
    for e in recientes:
        nombre = curado.get(e["iso"], {}).get("nombre", e["iso"])
        alertas.append(f"- **{nombre}**: hubo elecciones el {e['fecha']} ({e['nombre']}). Revisá la ficha.")

    datos = {
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fuente": "Wikidata",
        "elecciones": elecciones,
        "gobernantes": foto_nueva,
    }
    with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, ensure_ascii=False, separators=(",", ":"))

    if alertas:
        texto = ("Wikidata no coincide con algunas fichas, o hubo elecciones hace poco. "
                 "Revisalas y, si hace falta, actualizá `curado.json`.\n\n" + "\n".join(alertas) + "\n")
        ARCHIVO_ALERTAS.write_text(texto, encoding="utf-8")
    elif ARCHIVO_ALERTAS.exists():
        ARCHIVO_ALERTAS.unlink()

    print(f"\n✅ noticias.json listo · {len(elecciones)} elecciones · {len(alertas)} alertas")
    if alertas:
        print("\nPara revisar:\n" + "\n".join(alertas))


if __name__ == "__main__":
    main()
