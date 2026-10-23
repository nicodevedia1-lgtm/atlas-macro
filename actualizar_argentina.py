"""
actualizar_argentina.py — Atlas Macro

Baja el riesgo país y las cotizaciones del dólar oficial y blue de Argentina,
calcula promedios mensuales y la brecha cambiaria, y genera argentina.json.

Fuente: ArgentinaDatos (https://argentinadatos.com), un proyecto abierto y no
oficial que junta estos datos todos los días.

Uso:
    python3 actualizar_argentina.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests

CARPETA = Path(__file__).parent
ARCHIVO_SALIDA = CARPETA / "argentina.json"

BASE = "https://raw.githubusercontent.com/enzonotario/esjs-argentina-datos-api/main/datos/v1"
URL_RIESGO = f"{BASE}/finanzas/indices/riesgo-pais/index.json"
URL_BLUE = f"{BASE}/cotizaciones/dolares/blue/index.json"
URL_OFICIAL = f"{BASE}/cotizaciones/dolares/oficial/index.json"


def bajar(url):
    """Baja un archivo JSON. Si falla, corta el script (así no se pisa el archivo anterior)."""
    respuesta = requests.get(url, timeout=60)
    if respuesta.status_code != 200:
        raise SystemExit(f"❌ No pude bajar {url} (error {respuesta.status_code})")
    return respuesta.json()


def promedio_mensual(filas, campo):
    """
    filas: lista de diccionarios con "fecha" ("2024-03-15") y un valor.
    Devuelve {"2024-03": promedio del mes, ...}
    """
    sumas, cantidades = {}, {}
    for fila in filas:
        valor = fila.get(campo)
        if valor is None:
            continue
        mes = fila["fecha"][:7]                 # "2024-03-15" → "2024-03"
        sumas[mes] = sumas.get(mes, 0) + valor
        cantidades[mes] = cantidades.get(mes, 0) + 1
    return {mes: sumas[mes] / cantidades[mes] for mes in sumas}


def main():
    print("Atlas Macro · datos de Argentina")

    print("· Riesgo país")
    riesgo = bajar(URL_RIESGO)
    print("· Dólar blue")
    blue = bajar(URL_BLUE)
    print("· Dólar oficial")
    oficial = bajar(URL_OFICIAL)

    # --- Riesgo país ---
    riesgo_mes = promedio_mensual(riesgo, "valor")
    ultimo_riesgo = riesgo[-1]                  # la lista viene ordenada por fecha

    # --- Dólar: usamos el precio de venta ---
    blue_mes = promedio_mensual(blue, "venta")
    oficial_mes = promedio_mensual(oficial, "venta")
    ultimo_blue, ultimo_oficial = blue[-1], oficial[-1]

    # Brecha cambiaria: cuánto más caro está el blue que el oficial, en %
    brecha = (ultimo_blue["venta"] / ultimo_oficial["venta"] - 1) * 100

    meses_dolar = sorted(set(blue_mes) & set(oficial_mes))   # meses que están en las dos series

    datos = {
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fuente": "ArgentinaDatos (argentinadatos.com), proyecto abierto y no oficial",
        "riesgo_pais": {
            "ultimo": {"fecha": ultimo_riesgo["fecha"], "valor": ultimo_riesgo["valor"]},
            "mensual": [[mes, round(riesgo_mes[mes])] for mes in sorted(riesgo_mes)],
        },
        "dolar": {
            "ultimo": {
                "fecha": ultimo_blue["fecha"],
                "blue": ultimo_blue["venta"],
                "oficial": ultimo_oficial["venta"],
                "brecha": round(brecha, 1),
            },
            # cada fila: [mes, blue, oficial, brecha %]
            "mensual": [
                [mes, round(blue_mes[mes], 2), round(oficial_mes[mes], 2),
                 round((blue_mes[mes] / oficial_mes[mes] - 1) * 100, 1)]
                for mes in meses_dolar
            ],
        },
    }

    with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, ensure_ascii=False, separators=(",", ":"))

    print(f"✅ argentina.json: riesgo país {ultimo_riesgo['valor']} puntos ({ultimo_riesgo['fecha']}), "
          f"blue ${ultimo_blue['venta']:,.0f}, oficial ${ultimo_oficial['venta']:,.0f}, brecha {brecha:.1f}%")


if __name__ == "__main__":
    main()
