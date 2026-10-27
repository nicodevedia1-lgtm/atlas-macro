"""
actualizar_productos.py — Atlas Macro

Para una lista de productos (petróleo, soja, oro, autos, chips...) baja de
UN Comtrade cuánto exporta e importa cada país, y genera productos.json.
Con eso la página arma un mapa del mundo por producto: quién lo vende y quién lo compra.

Usa la consulta pública de Comtrade (no hace falta registrarse), que devuelve
hasta 500 filas por pedido: alcanza porque pedimos "cada país contra el mundo".

Ojo: son los datos que informa cada país. Algunos exportadores grandes informan
tarde o no informan (por ejemplo Venezuela o Irán), y ahí el mapa los muestra sin dato.

Uso:
    python3 actualizar_productos.py                # todos los productos
    python3 actualizar_productos.py petroleo soja  # solo algunos, para probar
    python3 actualizar_productos.py petroleo -v    # mostrando cada paso
    python3 actualizar_productos.py --todo         # rehace también los que ya se bajaron hoy

Si se corta, volvé a correrlo: saltea los productos que ya bajó hoy.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests

CARPETA = Path(__file__).parent
ARCHIVO_SALIDA = CARPETA / "productos.json"
# Con una clave gratuita de Comtrade (variable COMTRADE_KEY) se usa la consulta completa,
# que deja hacer más pedidos; sin clave, la pública, que limita la velocidad.
CLAVE = os.environ.get("COMTRADE_KEY", "").strip()
URL = "https://comtradeapi.un.org/data/v1/get/C/A/HS" if CLAVE else "https://comtradeapi.un.org/public/v1/preview/C/A/HS"

# clave → (nombre, emoji, códigos del Sistema Armonizado, grupo)
PRODUCTOS = {
    "petroleo":  ("Petróleo crudo", "🛢️", "2709", "Energía y minería"),
    "gas":       ("Gas natural", "🔥", "2711", "Energía y minería"),
    "carbon":    ("Carbón", "⚫", "2701", "Energía y minería"),
    "oro":       ("Oro", "🥇", "7108", "Energía y minería"),
    "cobre":     ("Cobre (mineral y refinado)", "🟠", "2603,7403", "Energía y minería"),
    "hierro":    ("Mineral de hierro", "⛏️", "2601", "Energía y minería"),
    "litio":     ("Carbonato de litio", "🔋", "283691", "Energía y minería"),
    "soja":      ("Soja (poroto)", "🫘", "1201", "Agro y alimentos"),
    "harina":    ("Harina de soja", "🌾", "2304", "Agro y alimentos"),
    "aceite":    ("Aceite de soja", "🫗", "1507", "Agro y alimentos"),
    "maiz":      ("Maíz", "🌽", "1005", "Agro y alimentos"),
    "trigo":     ("Trigo", "🌾", "1001", "Agro y alimentos"),
    "carne":     ("Carne vacuna", "🥩", "0201,0202", "Agro y alimentos"),
    "cafe":      ("Café", "☕", "0901", "Agro y alimentos"),
    "cacao":     ("Cacao", "🍫", "1801", "Agro y alimentos"),
    "vino":      ("Vino", "🍷", "2204", "Agro y alimentos"),
    "autos":     ("Autos", "🚗", "8703", "Industria"),
    "celulares": ("Teléfonos y celulares", "📱", "8517", "Industria"),
    "chips":     ("Chips (circuitos integrados)", "💾", "8542", "Industria"),
    "compus":    ("Computadoras", "💻", "8471", "Industria"),
    "remedios":  ("Medicamentos", "💊", "3004", "Industria"),
    "ropa":      ("Ropa", "👕", "61,62", "Industria"),
}

# Países que suelen no informar (o informar muy tarde) su comercio a la ONU. Si no aparecen,
# los completamos "en espejo": sus exportaciones = lo que el resto del mundo dice que les compró,
# y sus importaciones = lo que el resto dice que les vendió. Código numérico M49 de la ONU.
ESPEJO = {"RUS": 643, "VEN": 862, "IRN": 364, "IRQ": 368, "LBY": 434, "DZA": 12, "AGO": 24, "NGA": 566,
          "TKM": 795, "SDN": 729, "SSD": 728, "YEM": 887, "SYR": 760, "PRK": 408, "CUB": 192, "TCD": 148,
          "GNQ": 226, "GIN": 324, "COD": 180, "KWT": 414, "QAT": 634, "ARE": 784, "SAU": 682,
          "TWN": 490}     # Taiwán no informa a la ONU: figura como "Other Asia, nes" (código 490)
POR_CODIGO = {v: k for k, v in ESPEJO.items()}

ANIO = datetime.now().year
ANIOS_A_PROBAR = [ANIO - 2, ANIO - 3, ANIO - 1]     # primero el que suele estar más completo

sesion = requests.Session(impersonate="chrome")
VER = "-v" in sys.argv


LIMITE = 100000 if CLAVE else 500        # filas máximas por pedido


def pedir(anio, flujo, codigos, socio=0):
    """
    Filas de Comtrade, o None si no respondió. `anio` puede ser una lista de años
    y `socio` una lista de países ("643,862"), separados por comas.
    """
    params = {"period": anio, "cmdCode": codigos, "flowCode": flujo, "partnerCode": socio,
              "partner2Code": 0, "customsCode": "C00", "motCode": 0, "includeDesc": "true"}
    for intento in range(5):
        try:
            r = sesion.get(URL, params=params, timeout=120,
                           headers={"Ocp-Apim-Subscription-Key": CLAVE} if CLAVE else None)
        except Exception as e:
            print(f"        (no conectó, reintento: {str(e)[:60]})")
            time.sleep(10)
            continue
        if VER:
            print(f"        [{anio} {flujo} socio {socio} · código {r.status_code}] {r.text[:100]!r}")
        if r.status_code == 200:
            try:
                return r.json().get("data") or []
            except ValueError:
                return None
        if r.status_code in (429, 500, 502, 503, 504):
            espera = 20 * (intento + 1)
            print(f"        (Comtrade pide esperar: {espera} segundos)")
            time.sleep(espera)
            continue
        return None
    return None


def valores(filas, paises, campo="reporterISO"):
    """{ISO3: millones de US$} sumando filas (por ejemplo, si el producto tiene varios códigos)."""
    out = {}
    for f in filas:
        iso = (f.get(campo) or "").upper()
        v = f.get("primaryValue") or 0
        if iso in paises and v > 0:
            out[iso] = out.get(iso, 0) + v / 1e6
    return out


def bajar_varios_anios(flujo, codigos, paises):
    """{año: {país: valor}} para los años a probar, con la menor cantidad de pedidos posible."""
    por_anio = {}
    anios = ",".join(str(a) for a in ANIOS_A_PROBAR)
    filas = pedir(anios, flujo, codigos)
    time.sleep(1.5)
    if filas is not None and len(filas) < LIMITE:              # entró todo en un solo pedido
        for a in ANIOS_A_PROBAR:
            por_anio[a] = valores([f for f in filas if f.get("refYear") == a], paises)
        return por_anio
    for a in ANIOS_A_PROBAR:                                   # si no, año por año
        f = pedir(a, flujo, codigos)
        time.sleep(1.5)
        if f is not None:
            por_anio[a] = valores(f, paises)
    return por_anio


def espejo(anio, codigos, faltan, flujo_socios, paises):
    """
    Para los países que no informaron: lo que declaran sus socios. Si faltan las exportaciones
    de Rusia, sumamos lo que todos los demás dicen que le compraron a Rusia (flujo "M").
    Se piden varios países juntos; si la respuesta se llena (500 filas), se parte en dos.
    """
    out = {}
    grupos = [faltan[i:i + 6] for i in range(0, len(faltan), 6)]
    while grupos:
        g = grupos.pop(0)
        filas = pedir(anio, flujo_socios, codigos, ",".join(str(ESPEJO[k]) for k in g))
        time.sleep(1.5)
        if filas is None:
            continue
        if len(filas) >= LIMITE and len(g) > 1:
            grupos += [g[:len(g) // 2], g[len(g) // 2:]]
            continue
        for f in filas:                                        # se identifica al socio por su código numérico
            k = POR_CODIGO.get(f.get("partnerCode"))
            if k and (f.get("reporterISO") or "").upper() in paises and (f.get("primaryValue") or 0) > 0:
                out[k] = out.get(k, 0) + f["primaryValue"] / 1e6
    out = {k: v for k, v in out.items() if v >= 1}
    return out


def main():
    claves = [a for a in sys.argv[1:] if not a.startswith("-")] or list(PRODUCTOS)
    todo = "--todo" in sys.argv
    paises = set(json.load(open(CARPETA / "datos.json", encoding="utf-8"))["paises"])
    anterior = json.load(open(ARCHIVO_SALIDA, encoding="utf-8")) if ARCHIVO_SALIDA.exists() else {"productos": {}}
    salida = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "fuente": "UN Comtrade (Naciones Unidas), lo que informa cada país",
              "productos": dict(anterior.get("productos", {}))}
    hoy = datetime.now(timezone.utc).date().isoformat()

    def guardar():
        with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as archivo:
            json.dump(salida, archivo, ensure_ascii=False, separators=(",", ":"))

    print(f"Atlas Macro · productos ({len(claves)}) · {'con clave de Comtrade' if CLAVE else 'consulta pública, sin clave'}")
    ok = 0
    for n, clave in enumerate(claves, 1):
        nombre, emoji, codigos, grupo = PRODUCTOS[clave]
        previo = salida["productos"].get(clave)
        if previo and previo.get("fecha") == hoy and not todo:
            print(f"  {n:>2}/{len(claves)} {nombre}: ya está de hoy, lo salteo")
            ok += 1
            continue
        expo = bajar_varios_anios("X", codigos, paises)
        if not any(expo.values()):
            print(f"  {n:>2}/{len(claves)} ⚠️  {nombre}: sin datos{' (dejo el dato anterior)' if previo else ''}")
            continue
        # el año con más países informando; si hay uno más nuevo casi igual de completo, ese
        maximo = max(len(v) for v in expo.values())
        anio = max(a for a, v in expo.items() if len(v) >= maximo * 0.9)
        x = expo[anio]
        impo = pedir(anio, "M", codigos)
        time.sleep(1.5)
        m = valores(impo or [], paises)
        ex = espejo(anio, codigos, [k for k in ESPEJO if k in paises and k not in x], "M", paises)
        em = espejo(anio, codigos, [k for k in ESPEJO if k in paises and k not in m], "X", paises) if m else {}
        if VER:
            print(f"        espejo: exportan {ex} · importan {em}")
        x.update(ex)
        m.update(em)
        ordenar = lambda d: [[k, round(v, 1)] for k, v in sorted(d.items(), key=lambda kv: -kv[1])]
        salida["productos"][clave] = {"nombre": nombre, "emoji": emoji, "codigos": codigos, "grupo": grupo, "anio": anio,
                                      "fecha": hoy, "expo": ordenar(x), "impo": ordenar(m),
                                      "espejo": {"expo": sorted(ex), "impo": sorted(em)}}
        guardar()                                              # guarda producto por producto
        ok += 1
        tot = sum(x.values())
        top = sorted(x.items(), key=lambda kv: -kv[1])[:3]
        print(f"  {n:>2}/{len(claves)} {nombre} {anio}: {len(x)} exportadores · US$ {tot:,.0f} M · "
              + ", ".join(f"{k} {v / tot * 100:.0f}%" for k, v in top)
              + (f" · estimados: {', '.join(sorted(ex))}" if ex else ""))

    if ok == 0:
        sys.exit("❌ No llegó ningún producto: dejo el productos.json anterior")
    guardar()
    kb = ARCHIVO_SALIDA.stat().st_size / 1024
    print(f"✅ productos.json listo · {ok} productos · {kb:,.0f} KB")


if __name__ == "__main__":
    main()
