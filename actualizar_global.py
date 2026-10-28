"""
actualizar_global.py — Atlas Macro

Datos de la "terminal" global:
  · Tasas de interés de los bancos centrales del mundo (BIS, Banco de Pagos Internacionales):
    la tasa de política monetaria de unos 40 países, mes a mes.
  · Curva de rendimientos de los bonos del Tesoro de Estados Unidos (Departamento del Tesoro):
    cuánto paga EE.UU. por pedir prestado a 1 mes, 3 meses, 1 año... hasta 30 años.
Genera global.json. Si una fuente falla, deja la parte anterior.

Uso:
    python3 actualizar_global.py
    python3 actualizar_global.py -v
"""

import csv
import io
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from curl_cffi import requests

CARPETA = Path(__file__).parent
ARCHIVO_SALIDA = CARPETA / "global.json"
VER = "-v" in sys.argv
sesion = requests.Session(impersonate="chrome")

BIS_URLS = [
    "https://stats.bis.org/api/v1/data/WS_CBPOL/M./all?startPeriod={desde}&detail=dataonly",
    "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/M.?startPeriod={desde}&detail=dataonly",
]
BIS_CSV = "application/vnd.sdmx.data+csv;version=1.0.0"
TESORO_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
              "{anio}/all?type=daily_treasury_yield_curve&field_tdr_date_value={anio}&page&_format=csv")
PLAZOS = ["1 Mo", "2 Mo", "3 Mo", "4 Mo", "6 Mo", "1 Yr", "2 Yr", "3 Yr", "5 Yr", "7 Yr", "10 Yr", "20 Yr", "30 Yr"]


def tasas_bancos_centrales(iso2_a_iso3):
    print("· Tasas de los bancos centrales (BIS)")
    desde = f"{date.today().year - 6}-01"
    texto, error = None, None
    for url in BIS_URLS:
        try:
            r = sesion.get(url.format(desde=desde), headers={"Accept": BIS_CSV}, timeout=90)
            if VER:
                print(f"        {url[:60]}… código {r.status_code} · {r.text[:120]!r}")
            if r.status_code == 200 and "OBS_VALUE" in r.text[:2000]:
                texto = r.text
                break
            error = f"código {r.status_code}"
        except Exception as e:
            error = str(e)[:80]
    if not texto:
        raise RuntimeError(f"el BIS no respondió ({error})")
    series = {}
    for f in csv.DictReader(io.StringIO(texto)):
        area, mes, v = f.get("REF_AREA"), f.get("TIME_PERIOD"), f.get("OBS_VALUE")
        if area and mes and v not in (None, "", "NaN"):
            series.setdefault(area, []).append([mes[:7], round(float(v), 2)])
    salida = {}
    for area, s in series.items():
        s.sort()
        clave = "EUR" if area == "XM" else iso2_a_iso3.get(area)
        if clave:
            salida[clave] = s
    print(f"     · {len(salida)} bancos centrales · ej.: " + ", ".join(
        f"{k} {salida[k][-1][1]}% ({salida[k][-1][0]})" for k in ("USA", "EUR", "BRA", "CHL", "ARG") if k in salida))
    return salida


def curva_tesoro():
    print("· Curva del Tesoro de EE.UU.")
    filas = []
    hoy = date.today()
    for anio in (hoy.year - 2, hoy.year - 1, hoy.year):
        r = sesion.get(TESORO_URL.format(anio=anio), timeout=90)
        if VER:
            print(f"        {anio}: código {r.status_code} · {r.text[:100]!r}")
        if r.status_code != 200:
            continue
        for f in csv.DictReader(io.StringIO(r.text.lstrip("﻿"))):
            m, d, y = f["Date"].split("/")
            filas.append({"fecha": f"{y}-{m}-{d}", **{p: float(f[p]) for p in PLAZOS if f.get(p) not in (None, "")}})
    if not filas:
        raise RuntimeError("el Tesoro no respondió")
    filas.sort(key=lambda f: f["fecha"])

    def curva_al(fecha):
        cand = [f for f in filas if f["fecha"] <= fecha]
        f = cand[-1] if cand else filas[0]
        return {"fecha": f["fecha"], "tasas": [[p, f.get(p)] for p in PLAZOS if f.get(p) is not None]}

    ult = filas[-1]["fecha"]
    y, m, d = map(int, ult.split("-"))
    hace_mes = date(y, m, d).replace(day=1).isoformat() if m == 1 else date(y, m - 1, min(d, 28)).isoformat()
    salida = {"hoy": curva_al(ult), "mes": curva_al(hace_mes), "anio": curva_al(date(y - 1, m, min(d, 28)).isoformat()),
              # 2 y 10 años y la diferencia (si es negativa, la curva está "invertida")
              "serie": [[f["fecha"], f.get("2 Yr"), f.get("10 Yr")] for f in filas if f.get("2 Yr") and f.get("10 Yr")]}
    t = dict(salida["hoy"]["tasas"])
    print(f"     · {ult}: 3 meses {t.get('3 Mo')}% · 2 años {t.get('2 Yr')}% · 10 años {t.get('10 Yr')}% · 30 años {t.get('30 Yr')}%")
    return salida


def main():
    print("Atlas Macro · tasas del mundo y curva del Tesoro")
    paises = json.load(open(CARPETA / "datos.json", encoding="utf-8"))["paises"]
    iso2_a_iso3 = {d["iso2"].upper(): k for k, d in paises.items() if d.get("iso2")}
    anterior = json.load(open(ARCHIVO_SALIDA, encoding="utf-8")) if ARCHIVO_SALIDA.exists() else {}
    salida = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "fuente": "BIS (tasas de política monetaria) y Departamento del Tesoro de EE.UU. (curva de rendimientos)"}
    ok = 0
    for clave, funcion in (("tasas", lambda: tasas_bancos_centrales(iso2_a_iso3)), ("curva", curva_tesoro)):
        try:
            salida[clave] = funcion()
            ok += 1
        except Exception as e:
            print(f"     ❌ {clave}: {str(e)[:120]} (dejo el dato anterior)")
            if clave in anterior:
                salida[clave] = anterior[clave]
    if ok == 0:
        sys.exit("❌ No respondió ninguna fuente: dejo el global.json anterior")
    with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as archivo:
        json.dump(salida, archivo, ensure_ascii=False, separators=(",", ":"))
    print(f"✅ global.json listo · {ARCHIVO_SALIDA.stat().st_size / 1024:,.0f} KB")


if __name__ == "__main__":
    main()
