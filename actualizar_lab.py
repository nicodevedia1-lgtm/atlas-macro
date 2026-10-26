"""
actualizar_lab.py — Atlas Macro

Baja las variables extra que usa el Laboratorio (la sección de regresiones):
sociales, desigualdad, macro, Estado, instituciones y ambiente. Todas del
Banco Mundial, desde el año 2000, y las guarda en lab.json.

Va aparte de actualizar.py a propósito: si alguna de estas variables falla,
los datos principales (datos.json) no se tocan.

Uso:
    python3 actualizar_lab.py
"""

import json
import sys
import time
from datetime import datetime, timezone

# Reusamos las herramientas que ya escribimos en actualizar.py
from actualizar import CARPETA, URL_BM, compactar, pedir_json

ANIO_INICIO = 2000
ANIO_FIN = datetime.now().year - 1
ARCHIVO_SALIDA = CARPETA / "lab.json"

# clave → (nombre, unidad, [códigos del Banco Mundial, en orden de preferencia], fuente del BM)
# Si un código deja de existir, se prueba el siguiente. La fuente 2 es la base general
# (World Development Indicators); la 3 son los indicadores de gobernanza.
VARIABLES = {
    # Sociales
    "vida":     ("Esperanza de vida", "años", ["SP.DYN.LE00.IN"], 2),
    "mortinf":  ("Mortalidad infantil", "por cada 1.000 nacidos", ["SP.DYN.IMRT.IN"], 2),
    "fert":     ("Hijos por mujer", "hijos", ["SP.DYN.TFRT.IN"], 2),
    "urbano":   ("Población urbana", "% del total", ["SP.URB.TOTL.IN.ZS"], 2),
    "internet": ("Uso de internet", "% de la población", ["IT.NET.USER.ZS"], 2),
    # Salud
    "mort5":    ("Mortalidad de menores de 5 años", "por cada 1.000 nacidos", ["SH.DYN.MORT"], 2),
    "materna":  ("Mortalidad materna", "por cada 100.000 nacidos", ["SH.STA.MMRT"], 2),
    "medicos":  ("Médicos", "por cada 1.000 habitantes", ["SH.MED.PHYS.ZS"], 2),
    "saludpc":  ("Gasto en salud por persona", "US$", ["SH.XPD.CHEX.PC.CD"], 2),
    # Educación
    "secundaria": ("Escolarización secundaria", "% de la edad correspondiente (bruta)", ["SE.SEC.ENRR"], 2),
    "universidad": ("Escolarización universitaria", "% de la edad correspondiente (bruta)", ["SE.TER.ENRR"], 2),
    # Población
    "mayores":  ("Población de 65 años o más", "% del total", ["SP.POP.65UP.TO.ZS"], 2),
    "chicos":   ("Población de 0 a 14 años", "% del total", ["SP.POP.0014.TO.ZS"], 2),
    "migrantes": ("Inmigrantes", "% de la población", ["SM.POP.TOTL.ZS"], 2),
    # Trabajo y género
    "mujeres":  ("Mujeres que trabajan o buscan trabajo", "% de las mujeres de 15 años o más", ["SL.TLF.CACT.FE.ZS"], 2),
    "jovenes":  ("Desempleo juvenil", "% de los jóvenes de 15 a 24 que buscan trabajo", ["SL.UEM.1524.ZS"], 2),
    "parlamento": ("Mujeres en el Congreso", "% de las bancas", ["SG.GEN.PARL.ZS"], 2),
    # Servicios y seguridad
    "electricidad": ("Acceso a electricidad", "% de la población", ["EG.ELC.ACCS.ZS"], 2),
    "agua":     ("Agua potable segura", "% de la población", ["SH.H2O.SMDW.ZS"], 2),
    "homicidios": ("Homicidios", "por cada 100.000 habitantes", ["VC.IHR.PSRC.P5"], 2),
    # Desigualdad y pobreza
    "gini":     ("Desigualdad (Gini)", "0 a 100", ["SI.POV.GINI"], 2),
    "pobreza":  ("Pobreza extrema", "% de la población", ["SI.POV.DDAY"], 2),
    "top10":    ("Ingreso del 10% más rico", "% del ingreso total", ["SI.DST.10TH.10"], 2),
    # Macro
    "apertura": ("Apertura comercial", "% del PBI", ["NE.TRD.GNFS.ZS"], 2),
    "inversion": ("Inversión", "% del PBI", ["NE.GDI.TOTL.ZS"], 2),
    "ahorro":   ("Ahorro nacional", "% del PBI", ["NY.GNS.ICTR.ZS"], 2),
    "ied":      ("Inversión extranjera directa", "% del PBI", ["BX.KLT.DINV.WD.GD.ZS"], 2),
    "remesas":  ("Remesas recibidas", "% del PBI", ["BX.TRF.PWKR.DT.GD.ZS"], 2),
    # Estado
    "gedu":     ("Gasto público en educación", "% del PBI", ["SE.XPD.TOTL.GD.ZS"], 2),
    "gsalud":   ("Gasto público en salud", "% del PBI", ["SH.XPD.GHED.GD.ZS"], 2),
    "impuestos": ("Recaudación de impuestos", "% del PBI", ["GC.TAX.TOTL.GD.ZS"], 2),
    # Instituciones (de -2,5 a +2,5: más alto = mejor)
    # En 2024 el Banco Mundial les cambió el código (antes CC.EST, ahora GOV_WGI_CC.EST): probamos los dos
    "corrupcion": ("Control de la corrupción", "índice, -2,5 a 2,5", ["GOV_WGI_CC.EST", "GOV_WGI_CC_EST", "CC.EST"], 3),
    "derecho":  ("Estado de derecho", "índice, -2,5 a 2,5", ["GOV_WGI_RL.EST", "GOV_WGI_RL_EST", "RL.EST"], 3),
    # Ambiente
    "co2":      ("Emisiones de CO₂ por persona", "toneladas", ["EN.GHG.CO2.PC.CE.AR5", "EN.ATM.CO2E.PC"], 2),
}


def bajar_variable(codigo, fuente):
    """Devuelve {iso3: {año: valor}} para un indicador del Banco Mundial (fuente None = la que diga el BM)."""
    serie = {}
    pagina, total_paginas = 1, 1
    while pagina <= total_paginas:
        parametros = {"format": "json", "date": f"{ANIO_INICIO}:{ANIO_FIN}", "per_page": 10000, "page": pagina}
        if fuente:
            parametros["source"] = fuente
        respuesta = pedir_json(f"{URL_BM}/country/all/indicator/{codigo}", parametros)
        if len(respuesta) < 2 or respuesta[1] is None:
            raise ValueError(f"el Banco Mundial no reconoce {codigo}")
        meta, filas = respuesta
        total_paginas = meta["pages"]
        for fila in filas:
            # algunas bases dejan vacío countryiso3code y ponen el código en country.id
            iso = fila.get("countryiso3code") or (fila.get("country") or {}).get("id", "")
            if fila["value"] is None or len(iso) != 3:
                continue
            serie.setdefault(iso, {})[int(fila["date"])] = fila["value"]
        pagina += 1
    return serie


def main():
    inicio = time.time()
    print(f"Atlas Macro · variables del Laboratorio {ANIO_INICIO}–{ANIO_FIN}\n")

    # Solo guardamos países que ya están en datos.json (así no se cuelan agregados como "Mundo")
    paises_validos = set(json.load(open(CARPETA / "datos.json", encoding="utf-8"))["paises"])

    anterior = {}
    if ARCHIVO_SALIDA.exists():
        anterior = json.load(open(ARCHIVO_SALIDA, encoding="utf-8"))

    variables, por_pais, fallas = {}, {}, []
    for clave, (nombre, unidad, codigos, fuente) in VARIABLES.items():
        datos, usado = None, None
        for codigo in codigos:
            for f in (fuente, None):             # primero con la fuente indicada, después sin especificarla
                try:
                    datos, usado = bajar_variable(codigo, f), codigo
                    break
                except Exception as error:
                    print(f"     ⚠️  {codigo}{f' (fuente {f})' if f else ''}: {error}")
            if datos is not None:
                break
        if datos is None:
            # Si falla, conservamos lo que había la vez anterior
            fallas.append(clave)
            if clave in anterior.get("variables", {}):
                variables[clave] = anterior["variables"][clave]
                for iso, vars_pais in anterior.get("paises", {}).items():
                    if clave in vars_pais:
                        por_pais.setdefault(iso, {})[clave] = vars_pais[clave]
            continue

        n = 0
        for iso, valores in datos.items():
            if iso in paises_validos and valores:
                por_pais.setdefault(iso, {})[clave] = compactar(valores, 2)
                n += 1
        variables[clave] = {"nombre": nombre, "unidad": unidad, "codigo": usado}
        print(f"     · {nombre:<32} {n:>3} países")

    if len(variables) < len(VARIABLES) // 2:
        sys.exit("❌ Fallaron demasiadas variables: dejo el lab.json anterior sin tocar.")

    salida = {
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "anios": [ANIO_INICIO, ANIO_FIN],
        "fuente": "Banco Mundial (World Development Indicators y Worldwide Governance Indicators)",
        "variables": variables,
        "paises": dict(sorted(por_pais.items())),
    }
    temporal = ARCHIVO_SALIDA.with_suffix(".tmp")
    with open(temporal, "w", encoding="utf-8") as archivo:
        json.dump(salida, archivo, ensure_ascii=False, separators=(",", ":"))
    temporal.replace(ARCHIVO_SALIDA)

    kb = ARCHIVO_SALIDA.stat().st_size / 1024
    print(f"\n✅ lab.json listo · {len(variables)} variables · {len(por_pais)} países · {kb:,.0f} KB · {time.time() - inicio:.0f} s")
    if fallas:
        print(f"⚠️  No se pudieron bajar: {', '.join(fallas)} (quedó el dato anterior, si había)")


if __name__ == "__main__":
    main()
