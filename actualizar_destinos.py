"""
actualizar_destinos.py — Atlas Macro

Para cada país baja de UN Comtrade sus 6 principales productos de exportación y de
importación (a 4 dígitos del Sistema Armonizado, por ejemplo 2304 = harina de soja),
y para cada uno los 5 países a los que más se lo vende o de los que más lo compra.
Genera destinos.json, que usa la ficha de cada país ("Comercio y socios").

Necesita la clave gratuita de Comtrade (variable COMTRADE_KEY): son 2 pedidos por
país, unos 400 en total, y el plan gratis permite 500 por día.

Uso:
    export COMTRADE_KEY="tu-clave"
    python3 actualizar_destinos.py             # todos los países (se puede cortar y retomar)
    python3 actualizar_destinos.py ARG CHL -v  # solo algunos, mostrando cada paso
    python3 actualizar_destinos.py --todo      # rehace también los que ya se bajaron este mes
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests

CARPETA = Path(__file__).parent
ARCHIVO_SALIDA = CARPETA / "destinos.json"
ARCHIVO_NOMBRES = CARPETA / "hs4_es.json"        # nombres en castellano de cada producto (escritos a mano)
URL = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
CLAVE = os.environ.get("COMTRADE_KEY", "").strip()

TOP_PRODUCTOS = 6
TOP_SOCIOS = 5
ANIO = datetime.now().year
ANIOS = [ANIO - 1, ANIO - 2, ANIO - 3]           # el más nuevo que tenga el país
TAIWAN = 490                                     # en Comtrade figura como "Other Asia, nes"

class SinCupo(Exception):
    """Comtrade rechazó la clave o se terminó el cupo del día."""


sesion = requests.Session(impersonate="chrome")
VER = "-v" in sys.argv


def pedir(**params):
    """Filas de Comtrade, o None si no respondió."""
    base = {"partner2Code": 0, "customsCode": "C00", "motCode": 0, "includeDesc": "true", "maxRecords": 100000}
    base.update(params)
    for intento in range(5):
        try:
            r = sesion.get(URL, params=base, timeout=180, headers={"Ocp-Apim-Subscription-Key": CLAVE})
        except Exception as e:
            print(f"        (no conectó, reintento: {str(e)[:60]})")
            time.sleep(10)
            continue
        if VER:
            print(f"        [{params.get('reporterCode')} {params.get('period')} · código {r.status_code}] {r.text[:100]!r}")
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
        if r.status_code in (401, 403):
            raise SinCupo()
        return None
    return None


def codigos_comtrade():
    """{ISO3: código numérico de Comtrade} de los países que informan."""
    r = sesion.get("https://comtradeapi.un.org/files/v1/app/reference/Reporters.json", timeout=60)
    salida = {}
    for f in r.json().get("results", []):
        iso = (f.get("reporterCodeIsoAlpha3") or "").upper()
        if iso and not f.get("isGroup") and not f.get("entryExpiredDate"):
            salida[iso] = f["reporterCode"]
    return salida


def main():
    if not CLAVE:
        sys.exit("❌ Falta la clave: export COMTRADE_KEY=\"tu-clave\"")
    args = [a.upper() for a in sys.argv[1:] if not a.startswith("-")]
    todo = "--todo" in sys.argv
    paises = set(json.load(open(CARPETA / "datos.json", encoding="utf-8"))["paises"])
    nombres_es = json.load(open(ARCHIVO_NOMBRES, encoding="utf-8")) if ARCHIVO_NOMBRES.exists() else {}
    anterior = json.load(open(ARCHIVO_SALIDA, encoding="utf-8")) if ARCHIVO_SALIDA.exists() else {}
    salida = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "fuente": "UN Comtrade (Naciones Unidas)",
              "paises": anterior.get("paises", {}), "nombres": anterior.get("nombres", {})}
    mes = datetime.now(timezone.utc).strftime("%Y-%m")

    codigos = codigos_comtrade()
    lista = args or sorted(k for k in paises if k in codigos)
    print(f"Atlas Macro · qué vende cada país y a quién ({len(lista)} países)")

    def guardar():
        with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as archivo:
            json.dump(salida, archivo, ensure_ascii=False, separators=(",", ":"))

    ok = 0
    for n, iso in enumerate(lista, 1):
        prev = salida["paises"].get(iso)
        if prev and prev.get("mes") == mes and not todo:
            print(f"  {n:>3}/{len(lista)} {iso}: ya está de este mes, lo salteo")
            continue
        if iso not in codigos:
            print(f"  {n:>3}/{len(lista)} ⚠️  {iso}: no informa a Comtrade")
            continue
        rep = codigos[iso]

        # 1) Todos los productos (4 dígitos) contra el mundo, en los últimos 3 años, exportaciones e importaciones
        try:
            filas = pedir(reporterCode=rep, period=",".join(map(str, ANIOS)), cmdCode="AG4", flowCode="X,M", partnerCode=0)
        except SinCupo:
            print("⚠️  Comtrade rechazó la clave o se terminó el cupo del día: guardo lo que hay. "
                  "Volvé a correrlo mañana y sigue desde acá.")
            break
        time.sleep(1)
        if not filas:
            print(f"  {n:>3}/{len(lista)} ⚠️  {iso}: sin datos")
            continue
        por_anio = {}
        for f in filas:
            por_anio.setdefault(f["refYear"], []).append(f)
        anio = max(por_anio, key=lambda a: (len(por_anio[a]) >= 0.7 * max(map(len, por_anio.values())), a))
        totales, productos = {"X": 0, "M": 0}, {"X": {}, "M": {}}
        for f in por_anio[anio]:
            fl, cod, v = f.get("flowCode"), str(f.get("cmdCode")), f.get("primaryValue") or 0
            if fl in productos and len(cod) == 4 and v > 0:
                productos[fl][cod] = productos[fl].get(cod, 0) + v
                totales[fl] += v
                salida["nombres"].setdefault(cod, f.get("cmdDesc") or cod)
        # El capítulo 99 son "productos no especificados" (datos confidenciales, por ejemplo): cuentan
        # en el total pero no en el ranking; guardamos qué parte del total son, para avisarlo.
        oculto = {fl: sum(v for c, v in productos[fl].items() if c.startswith("99")) for fl in productos}
        top = {fl: sorted(((c, v) for c, v in productos[fl].items() if not c.startswith("99")),
                          key=lambda kv: -kv[1])[:TOP_PRODUCTOS] for fl in productos}

        # 2) Para esos productos, a qué países (un solo pedido para todo)
        cods = sorted({c for fl in top for c, _ in top[fl]})
        try:
            socios = pedir(reporterCode=rep, period=anio, cmdCode=",".join(cods), flowCode="X,M") if cods else []
        except SinCupo:
            print("⚠️  Comtrade rechazó la clave o se terminó el cupo del día: guardo lo que hay. "
                  "Volvé a correrlo mañana y sigue desde acá.")
            break
        time.sleep(1)
        por = {}
        for f in socios or []:
            p = (f.get("partnerISO") or "").upper()
            if f.get("partnerCode") == TAIWAN:
                p = "TWN"
            v = f.get("primaryValue") or 0
            if p in paises and p != iso and v > 0:
                clave = (f.get("flowCode"), str(f.get("cmdCode")))
                por.setdefault(clave, {})
                por[clave][p] = por[clave].get(p, 0) + v

        def armar(fl):
            out = []
            for cod, v in top[fl]:
                dest = sorted(por.get((fl, cod), {}).items(), key=lambda kv: -kv[1])[:TOP_SOCIOS]
                out.append([cod, round(v / 1e6, 1), [[p, round(x / v * 100, 1)] for p, x in dest]])
            return out

        salida["paises"][iso] = {"anio": anio, "mes": mes,
                                 "expo_total": round(totales["X"] / 1e6), "impo_total": round(totales["M"] / 1e6),
                                 "vende": armar("X"), "compra": armar("M"),
                                 "sin_especificar": {"vende": round(oculto["X"] / totales["X"] * 100, 1) if totales["X"] else 0,
                                                     "compra": round(oculto["M"] / totales["M"] * 100, 1) if totales["M"] else 0}}
        guardar()
        ok += 1
        v0 = salida["paises"][iso]["vende"][0] if salida["paises"][iso]["vende"] else None
        if v0:
            nom = nombres_es.get(v0[0]) or salida["nombres"].get(v0[0], v0[0])
            print(f"  {n:>3}/{len(lista)} {iso} {anio}: vende sobre todo {nom[:40]} "
                  f"({v0[1] / (totales['X'] / 1e6) * 100:.0f}%), sobre todo a {', '.join(p for p, _ in v0[2][:3])}")

    guardar()
    faltan = sorted({c for p in salida["paises"].values() for fl in ("vende", "compra") for c, *_ in p[fl]} - set(nombres_es))
    print(f"✅ destinos.json listo · {ok} países nuevos · {len(salida['paises'])} en total")
    if faltan:
        print(f"   ({len(faltan)} productos todavía sin nombre en castellano en hs4_es.json)")


if __name__ == "__main__":
    main()
