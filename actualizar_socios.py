"""
actualizar_socios.py — Atlas Macro

Baja de WITS (la base de comercio del Banco Mundial) a quién le vende y a quién
le compra cada país, y genera socios.json con los 5 principales socios de cada
lado y qué porcentaje del total representa cada uno.

Usa el último año que tenga datos cada país (suele ir 1 a 3 años atrasado,
porque cada país informa su comercio a la ONU con demora).

Uso:
    python3 actualizar_socios.py            # todos los países (tarda unos minutos)
    python3 actualizar_socios.py ARG CHL    # solo algunos, para probar
    python3 actualizar_socios.py --ver ARG  # muestra la respuesta cruda de la API
    python3 actualizar_socios.py ARG -v     # arma el país mostrando cada paso
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests

CARPETA = Path(__file__).parent
ARCHIVO_SALIDA = CARPETA / "socios.json"

URL = ("https://wits.worldbank.org/API/V1/SDMX/V21/datasource/tradestats-trade/"
       "reporter/{rep}/year/{anio}/partner/all/product/Total/indicator/{ind}?format=JSON")
INDICADORES = {"vende": "XPRT-TRD-VL", "compra": "MPRT-TRD-VL"}   # exportaciones e importaciones, miles de US$

# WITS usa algunos códigos viejos que no son el ISO3 actual
ALIAS = {"ROM": "ROU", "SER": "SRB", "ZAR": "COD", "TMP": "TLS", "MNT": "MNE", "SUD": "SDN", "OAS": "TWN"}
CODIGO_WITS = {v: k for k, v in ALIAS.items() if k != "OAS"}       # para pedir un país como "reporter"

ANIO = datetime.now().year
ANIOS_A_PROBAR = range(ANIO - 1, ANIO - 8, -1)                       # del más nuevo al más viejo

sesion = requests.Session(impersonate="chrome")   # trae sus propios certificados
VER = "--ver" in sys.argv or "-v" in sys.argv


def pedir(rep, anio, ind):
    """El JSON de la API, o None si ese país no tiene datos para ese año."""
    url = URL.format(rep=CODIGO_WITS.get(rep, rep).lower(), anio=anio, ind=ind)
    for intento in range(3):
        try:
            r = sesion.get(url, timeout=90)          # WITS a veces tarda casi un minuto
            codigo, crudo = r.status_code, r.content
        except Exception as e:
            if VER:
                print(f"        [{anio} · no conectó] {e}")
            time.sleep(3)
            continue
        texto = crudo.decode("utf-8-sig", errors="replace").strip()       # saca la "marca BOM" si viene
        if VER:
            print(f"        [{anio} · código {codigo}] {texto[:90]!r}")
        if codigo == 200 and texto.startswith("{"):
            try:
                return json.loads(texto)
            except ValueError:
                return None
        if codigo in (429, 500, 502, 503, 504):
            time.sleep(5 * (intento + 1))
            continue
        return None                                   # 404 o un aviso de "sin datos"
    return None


def por_socio(data):
    """{código del socio: valor} a partir del formato SDMX-JSON de WITS."""
    dims = data["structure"]["dimensions"]["series"]
    i_socio = next(i for i, d in enumerate(dims) if "PARTNER" in d["id"].upper())
    valores = {}
    for clave, serie in data["dataSets"][0]["series"].items():
        pos = [int(x) for x in clave.split(":")]
        socio = dims[i_socio]["values"][pos[i_socio]]["id"].upper()
        obs = list(serie["observations"].values())
        if obs and obs[0] and obs[0][0] not in (None, ""):
            valores[ALIAS.get(socio, socio)] = valores.get(ALIAS.get(socio, socio), 0) + float(obs[0][0])
    return valores


COBERTURA_MINIMA = 70    # % del total que tiene que tener un país de destino identificado


def top5(valores, paises):
    """
    Los 5 socios más grandes (solo países, sin regiones ni 'mundo'), en % del total.
    Algunos países (sobre todo petroleros) declaran gran parte de lo que venden con
    destino "no especificado": si los países identificados no llegan al 70% del total,
    el dato no sirve y se descarta (devuelve None y la cobertura).
    """
    en_paises = sum(v for k, v in valores.items() if k in paises)
    total = valores.get("WLD") or en_paises
    if not total:
        return None, 0, 0
    cobertura = en_paises / total * 100
    if cobertura < COBERTURA_MINIMA:
        return None, total, cobertura
    socios = sorted(((k, v) for k, v in valores.items() if k in paises), key=lambda x: -x[1])[:5]
    return [[k, round(v / total * 100, 1)] for k, v in socios], total, cobertura


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--ver" in sys.argv:
        rep = (args or ["ARG"])[0].upper()
        for anio in ANIOS_A_PROBAR:
            data = pedir(rep, anio, INDICADORES["vende"])
            if data:
                print(f"{rep} {anio}: dimensiones →", [(d["id"], d["values"][:3]) for d in data["structure"]["dimensions"]["series"]])
                print("primeras series →", list(data["dataSets"][0]["series"].items())[:3])
                return
            print(f"{rep} {anio}: sin datos")
        return

    datos = json.load(open(CARPETA / "datos.json", encoding="utf-8"))
    paises = set(datos["paises"])
    anterior = json.load(open(ARCHIVO_SALIDA, encoding="utf-8")) if ARCHIVO_SALIDA.exists() else {"paises": {}}
    lista = [a.upper() for a in args] or sorted(paises)

    print(f"Atlas Macro · socios comerciales ({len(lista)} países)")
    salida = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "fuente": "WITS, Banco Mundial (datos de Naciones Unidas, Comtrade)",
              "paises": dict(anterior.get("paises", {}))}
    ok = sin = 0
    def guardar():
        with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as archivo:
            json.dump(salida, archivo, ensure_ascii=False, separators=(",", ":"))

    for n, iso in enumerate(lista, 1):
        if n % 10 == 0 and ok:
            guardar()                                 # va guardando, por si se corta a la mitad
        fila, poca = None, None
        for anio in ANIOS_A_PROBAR:
            expo = pedir(iso, anio, INDICADORES["vende"])
            time.sleep(0.4)
            if not expo:
                continue
            impo = pedir(iso, anio, INDICADORES["compra"])
            time.sleep(0.4)
            if not impo:
                continue
            vende, tx, cx = top5(por_socio(expo), paises - {iso})
            compra, tm, cm = top5(por_socio(impo), paises - {iso})
            if VER:
                print(f"        {iso} {anio}: vende={vende} compra={compra} (cobertura {cx:.0f}% y {cm:.0f}%)")
            if not (vende and compra):
                poca = poca or f"{anio}: solo {min(cx, cm):.0f}% tiene un país de destino identificado"
            if vende and compra:
                fila = {"anio": anio, "vende": vende, "compra": compra,
                        "expo_musd": round(tx / 1000), "impo_musd": round(tm / 1000)}   # millones de US$
                break
        if fila:
            salida["paises"][iso] = fila
            ok += 1
            print(f"  {n:>3}/{len(lista)} {iso} {fila['anio']}: vende a {fila['vende'][0][0]} ({fila['vende'][0][1]}%) · "
                  f"compra a {fila['compra'][0][0]} ({fila['compra'][0][1]}%)")
        elif poca:
            sin += 1
            salida["paises"].pop(iso, None)           # el dato no sirve: la página usa el escrito a mano
            print(f"  {n:>3}/{len(lista)} ⚠️  {iso}: lo descarto ({poca})")
        else:
            sin += 1
            print(f"  {n:>3}/{len(lista)} ⚠️  {iso}: sin datos en WITS{' (dejo el dato anterior)' if iso in salida['paises'] else ''}")

    if ok == 0:
        sys.exit("❌ No llegó ningún dato: dejo el socios.json anterior")
    guardar()
    kb = ARCHIVO_SALIDA.stat().st_size / 1024
    print(f"✅ socios.json listo · {ok} países con datos · {sin} sin datos · {kb:,.0f} KB")


if __name__ == "__main__":
    main()
