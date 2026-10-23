"""
actualizar_riesgo.py — Atlas Macro

Baja el riesgo país (EMBIG) de varios países de Latinoamérica y genera riesgo.json.

Fuente: Banco Central de Reserva del Perú (BCRP), que publica todos los meses el
"Diferencial de rendimientos del índice de bonos de mercados emergentes (EMBIG)"
de cada país, en puntos básicos, con datos de Reuters. Desde 2006 (Chile, desde 2017).

Argentina no está acá: su riesgo país sale de actualizar_argentina.py, que tiene
datos diarios y desde 1999.

Uso:
    python3 actualizar_riesgo.py
"""

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from curl_cffi import requests

CARPETA = Path(__file__).parent
ARCHIVO_SALIDA = CARPETA / "riesgo.json"

URL_BCRP = "https://estadisticas.bcrp.gob.pe/estadisticas/series/api/{codigos}/json/2006-1/{hasta}"

# Código de cada serie en el BCRP → país (código ISO de 3 letras)
SERIES = {
    "PN01129XM": "PER",
    "PN01131XM": "BRA",
    "PN01132XM": "CHL",
    "PN01133XM": "COL",
    "PN01134XM": "ECU",
    "PN01135XM": "MEX",
    "PN01136XM": "VEN",
    "PN01137XM": "LATAM",   # promedio de Latinoamérica, para comparar
}

# El BCRP nombra los meses así: "Ene.2024", "Set.2024" (septiembre), etc.
MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
         "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
         "jan": 1, "apr": 4, "aug": 8, "dec": 12}


def mes_iso(nombre):
    """'Set.2024' → '2024-09'"""
    mes, anio = nombre.replace(" ", "").split(".")
    return f"{anio}-{MESES[mes.lower()[:3]]:02d}"


def numero(texto):
    """'152.3' → 152.3 ; 'n.d.' (no disponible) → None"""
    try:
        return float(texto)
    except (TypeError, ValueError):
        return None


def main():
    print("Atlas Macro · riesgo país de Latinoamérica (BCRP)")
    hoy = date.today()
    url = URL_BCRP.format(codigos="-".join(SERIES), hasta=f"{hoy.year}-{hoy.month}")

    respuesta = requests.get(url, impersonate="chrome", timeout=60)
    texto = respuesta.text.strip().lstrip("﻿")
    if respuesta.status_code != 200 or not texto.startswith("{"):
        sys.exit(f"❌ El BCRP respondió algo raro (error {respuesta.status_code}): {texto[:200]}")
    datos = json.loads(texto)

    # Cada período trae un valor por serie, en el mismo orden en que las pedimos
    nombres = [s["name"] for s in datos["config"]["series"]]
    print(f"· {len(nombres)} series, {len(datos['periods'])} meses")
    isos = list(SERIES.values())

    series = {iso: [] for iso in isos}
    for periodo in datos["periods"]:
        mes = mes_iso(periodo["name"])
        for iso, valor in zip(isos, periodo["values"]):
            v = numero(valor)
            if v is not None:
                series[iso].append([mes, round(v)])

    salida = {
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fuente": "Banco Central de Reserva del Perú (EMBIG, datos de Reuters)",
        "paises": {},
        "latam": series.pop("LATAM"),
    }
    for iso, mensual in series.items():
        if not mensual:
            print(f"  ⚠️  {iso}: sin datos")
            continue
        salida["paises"][iso] = {"ultimo": {"mes": mensual[-1][0], "valor": mensual[-1][1]}, "mensual": mensual}
        print(f"  {iso}: {mensual[-1][1]:>6} puntos ({mensual[-1][0]}) · desde {mensual[0][0]}")

    if not salida["paises"]:
        sys.exit("❌ No llegó ningún dato: dejo el riesgo.json anterior")

    with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as archivo:
        json.dump(salida, archivo, ensure_ascii=False, separators=(",", ":"))
    print(f"✅ riesgo.json listo · {len(salida['paises'])} países")


if __name__ == "__main__":
    main()
