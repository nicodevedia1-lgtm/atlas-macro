"""
actualizar.py — Atlas Macro

Baja los indicadores macro de TODOS los países desde las APIs del
Banco Mundial y del FMI, los combina y genera datos.json.

Uso:
    pip3 install -r requirements.txt
    python3 actualizar.py
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pycountry
from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException


# ═══════════════════════════════════════════════════════════════════
# 1. CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════════

ANIO_INICIO = 1960
ANIO_FIN = datetime.now().year - 1        # último año cerrado (en 2026 → 2025)

CARPETA = Path(__file__).parent
ARCHIVO_SALIDA = CARPETA / "datos.json"

URL_BM = "https://api.worldbank.org/v2"
URL_FMI = "https://www.imf.org/external/datamapper/api/v1"

# Si después de bajar todo hay menos países que esto, algo salió mal
# y NO pisamos el datos.json anterior.
MINIMO_PAISES = 150

# Indicadores del Banco Mundial: nuestra clave → (código BM, divisor)
# El divisor pasa unidades: US$ → miles de millones, personas → millones.
INDICADORES_BM = {
    "pib":       ("NY.GDP.MKTP.CD",    1e9),  # PBI, US$ corrientes
    "crec":      ("NY.GDP.MKTP.KD.ZG", 1),    # crecimiento real, %
    "infl":      ("FP.CPI.TOTL.ZG",    1),    # inflación (IPC), %
    "deflactor": ("NY.GDP.DEFL.KD.ZG", 1),    # inflación por deflactor del PBI, %
    "cc":        ("BN.CAB.XOKA.GD.ZS", 1),    # cuenta corriente, % del PBI
    "deuda":     ("GC.DOD.TOTL.GD.ZS", 1),    # deuda del gobierno central, % del PBI
    "desempleo": ("SL.UEM.TOTL.ZS",    1),    # desempleo (estimación OIT), %
    "poblacion": ("SP.POP.TOTL",       1e6),  # habitantes
    "pibpc":     ("NY.GDP.PCAP.CD",    1),    # PBI per cápita, US$
    "ppa":       ("NY.GDP.PCAP.PP.CD", 1),    # PBI per cápita PPA, US$ internacionales
}

# Indicadores del FMI (World Economic Outlook). Ya vienen en las
# mismas unidades que usamos: PBI en miles de millones, población en millones.
INDICADORES_FMI = {
    "pib":       "NGDPD",
    "crec":      "NGDP_RPCH",
    "infl":      "PCPIPCH",
    "cc":        "BCA_NGDPD",
    "deuda":     "GGXWDG_NGDP",   # deuda bruta del gobierno general
    "desempleo": "LUR",
    "poblacion": "LP",
    "pibpc":     "NGDPDPC",
    "ppa":       "PPPPC",
}

# Reglas de combinación: para cada indicador, de dónde sacar el dato
# en orden de preferencia. Si la primera fuente no tiene un año,
# se usa la segunda, y así.
REGLAS = {
    "pib":       ["bm.pib",       "fmi.pib"],
    "crec":      ["bm.crec",      "fmi.crec"],
    "infl":      ["bm.infl",      "bm.deflactor", "fmi.infl"],
    "cc":        ["bm.cc",        "fmi.cc"],
    "deuda":     ["fmi.deuda",    "bm.deuda"],     # acá el FMI va primero
    "desempleo": ["bm.desempleo", "fmi.desempleo"],
    "poblacion": ["bm.poblacion", "fmi.poblacion"],
    "pibpc":     ["bm.pibpc",     "fmi.pibpc"],
    "ppa":       ["bm.ppa",       "fmi.ppa"],
}

# Excepción para los años MÁS NUEVOS que el último dato de la fuente principal.
# Ejemplo: si el Banco Mundial tiene el IPC argentino hasta 2024, para 2025
# preferimos el IPC del FMI antes que el deflactor del PBI (el deflactor mide
# precios de todo lo producido, no de lo que consumen las familias).
# Para los huecos VIEJOS (Argentina 2007–2015) sigue mandando el deflactor.
REGLAS_RECIENTES = {
    "infl": ["bm.infl", "fmi.infl", "bm.deflactor"],
}

# Códigos que el FMI escribe distinto que el Banco Mundial
FMI_A_BM = {"UVK": "XKX", "WBG": "PSE"}   # Kosovo, Cisjordania y Gaza

# Países que el Banco Mundial no publica pero el FMI sí
PAISES_SOLO_FMI = {"TWN": "Taiwán"}

# Indicadores que guardamos también para el promedio mundial
INDICADORES_MUNDO = ["crec", "infl", "desempleo"]


# ═══════════════════════════════════════════════════════════════════
# 2. PEDIR DATOS A INTERNET
# ═══════════════════════════════════════════════════════════════════

# curl_cffi se conecta igual que el navegador Chrome. Lo usamos porque el
# servidor del FMI bloquea (error 403) las conexiones que no parecen de un navegador.
sesion = requests.Session(impersonate="chrome")


def pedir_json(url, params=None, intentos=4):
    """Hace un GET y devuelve el JSON. Si falla, reintenta esperando cada vez más."""
    for intento in range(1, intentos + 1):
        try:
            respuesta = sesion.get(url, params=params, timeout=90)
        except RequestException as error:
            problema = error                       # sin conexión, o el servidor tardó demasiado
        else:
            codigo = respuesta.status_code
            if codigo == 200:                      # 200 = OK
                return respuesta.json()
            if 400 <= codigo < 500 and codigo != 429:
                # Errores 4xx (403 prohibido, 404 no existe…): reintentar no sirve
                raise RuntimeError(f"El servidor rechazó el pedido (error {codigo}): {url}")
            problema = f"error {codigo}"           # 429 (demasiados pedidos) o 5xx (servidor caído)

        if intento == intentos:
            raise RuntimeError(f"No pude bajar {url}: {problema}")
        espera = 2 ** intento                      # 2, 4, 8 segundos
        print(f"     ⚠️  falló ({problema}). Reintento en {espera} s…")
        time.sleep(espera)


# ═══════════════════════════════════════════════════════════════════
# 3. BANCO MUNDIAL
# ═══════════════════════════════════════════════════════════════════

def bajar_paises():
    """Lista de países del Banco Mundial (sin agregados como 'Mundo' o 'Zona euro')."""
    respuesta = pedir_json(f"{URL_BM}/es/country", {"format": "json", "per_page": 500})
    filas = respuesta[1]

    paises = {}
    for fila in filas:
        region = fila["region"]
        if region["id"] in ("", "NA") or region["iso2code"] == "NA":
            continue                               # es un agregado, no un país

        iso3 = fila["id"]
        pais_py = pycountry.countries.get(alpha_3=iso3)
        paises[iso3] = {
            "nombre":  fila["name"].strip(),
            "iso2":    fila["iso2Code"],
            "isoNum":  pais_py.numeric if pais_py else None,   # el código que usa el mapa
            "region":  region["value"].strip(),
            "ingreso": fila["incomeLevel"]["value"].strip(),
            "capital": fila["capitalCity"].strip() or None,
        }

    for iso3, nombre in PAISES_SOLO_FMI.items():
        pais_py = pycountry.countries.get(alpha_3=iso3)
        paises.setdefault(iso3, {
            "nombre": nombre, "iso2": pais_py.alpha_2 if pais_py else None,
            "isoNum": pais_py.numeric if pais_py else None,
            "region": None, "ingreso": None, "capital": None,
        })
    return paises


def bajar_indicador_bm(codigo, divisor=1):
    """Devuelve {iso3: {año: valor}} para un indicador del Banco Mundial."""
    serie = {}
    pagina, total_paginas = 1, 1

    while pagina <= total_paginas:
        respuesta = pedir_json(
            f"{URL_BM}/country/all/indicator/{codigo}",
            {"format": "json", "date": f"{ANIO_INICIO}:{ANIO_FIN}",
             "per_page": 10000, "page": pagina},
        )
        # Si el código está mal, el BM devuelve una lista con un solo mensaje de error
        if len(respuesta) < 2:
            raise ValueError(f"El Banco Mundial no reconoce {codigo}: {respuesta}")

        meta, filas = respuesta
        total_paginas = meta["pages"]

        for fila in filas or []:
            if fila["value"] is None:
                continue
            iso3 = fila["countryiso3code"]
            anio = int(fila["date"])
            serie.setdefault(iso3, {})[anio] = fila["value"] / divisor

        pagina += 1
    return serie


# ═══════════════════════════════════════════════════════════════════
# 4. FMI
# ═══════════════════════════════════════════════════════════════════

def bajar_indicador_fmi(codigo):
    """Devuelve {iso3: {año: valor}} para un indicador del FMI, sin proyecciones futuras."""
    respuesta = pedir_json(f"{URL_FMI}/{codigo}")
    por_pais = respuesta.get("values", {}).get(codigo, {})

    serie = {}
    for cod_fmi, anios in por_pais.items():
        iso3 = FMI_A_BM.get(cod_fmi, cod_fmi)
        valores = {
            int(anio): valor
            for anio, valor in anios.items()
            if valor is not None and ANIO_INICIO <= int(anio) <= ANIO_FIN
        }
        if valores:
            serie[iso3] = valores
    return serie


# ═══════════════════════════════════════════════════════════════════
# 5. COMBINAR Y COMPACTAR
# ═══════════════════════════════════════════════════════════════════

def combinar(fuentes):
    """
    fuentes: lista de (etiqueta, {año: valor}) en orden de preferencia.
    Devuelve la serie combinada y de qué fuente salió cada año.
    """
    serie, origen = {}, {}
    for etiqueta, datos in fuentes:
        for anio, valor in datos.items():
            if anio not in serie:                  # solo si ninguna fuente anterior lo tenía
                serie[anio] = valor
                origen[anio] = etiqueta
    return serie, origen


def compactar(serie, decimales=2):
    """{1960: 15.87, 1961: 20.1, ...} → [1960, [15.87, 20.1, ...]] con null en los huecos."""
    if not serie:
        return None
    primero, ultimo = min(serie), max(serie)
    valores = [
        round(serie[a], decimales) if a in serie else None
        for a in range(primero, ultimo + 1)
    ]
    return [primero, valores]


def rangos(anios):
    """[1961, 1962, 1963, 1990] → [[1961, 1963], [1990, 1990]]"""
    resultado = []
    for anio in sorted(anios):
        if resultado and anio == resultado[-1][1] + 1:
            resultado[-1][1] = anio                # estira el rango actual
        else:
            resultado.append([anio, anio])         # empieza uno nuevo
    return resultado


def armar_pais(iso3, descargas):
    """Arma las series de un país aplicando REGLAS. Devuelve (series, origen)."""
    series, origen_pais = {}, {}

    def fuentes_de(prioridades):
        """["bm.pib", "fmi.pib"] → [("bm.pib", {año: valor}), ("fmi.pib", {...})]"""
        fuentes = []
        for etiqueta in prioridades:
            fuente, clave = etiqueta.split(".")     # "bm.pib" → "bm", "pib"
            datos = descargas[fuente].get(clave, {}).get(iso3, {})
            fuentes.append((etiqueta, datos))
        return fuentes

    for indicador, prioridades in REGLAS.items():
        fuentes = fuentes_de(prioridades)
        serie, origen = combinar(fuentes)
        if not serie:
            continue

        if indicador in REGLAS_RECIENTES:
            datos_principal = fuentes[0][1]
            ultimo = max(datos_principal) if datos_principal else ANIO_INICIO - 1
            serie_rec, origen_rec = combinar(fuentes_de(REGLAS_RECIENTES[indicador]))
            for anio, valor in serie_rec.items():
                if anio > ultimo:                   # solo los años posteriores al último dato principal
                    serie[anio] = valor
                    origen[anio] = origen_rec[anio]

        decimales = 0 if indicador in ("pibpc", "ppa") else 2
        series[indicador] = compactar(serie, decimales)

        # Guardamos qué años NO vienen de la fuente principal
        principal = prioridades[0]
        otros = {}
        for anio, etiqueta in origen.items():
            if etiqueta != principal:
                otros.setdefault(etiqueta, []).append(anio)
        if otros:
            origen_pais[indicador] = {
                "principal": principal,
                **{etq: rangos(anios) for etq, anios in otros.items()},
            }

    return series, origen_pais


# ═══════════════════════════════════════════════════════════════════
# 6. PROGRAMA PRINCIPAL
# ═══════════════════════════════════════════════════════════════════

def main():
    inicio = time.time()
    print(f"Atlas Macro · actualizando datos {ANIO_INICIO}–{ANIO_FIN}\n")

    print("1/4  Lista de países (Banco Mundial)")
    paises = bajar_paises()
    print(f"     {len(paises)} países\n")

    descargas = {"bm": {}, "fmi": {}}

    print("2/4  Indicadores del Banco Mundial")
    for clave, (codigo, divisor) in INDICADORES_BM.items():
        print(f"     · {clave:<10} {codigo}")
        descargas["bm"][clave] = bajar_indicador_bm(codigo, divisor)
        # Si el BM falla del todo, pedir_json lanza un error y el script se corta
        # sin tocar el datos.json anterior: el BM es la base, sin él no seguimos.

    print("\n3/4  Indicadores del FMI")
    fallas = []
    for clave, codigo in INDICADORES_FMI.items():
        print(f"     · {clave:<10} {codigo}")
        try:
            descargas["fmi"][clave] = bajar_indicador_fmi(codigo)
        except Exception as error:
            print(f"     ⚠️  no se pudo bajar {codigo}: {error}")
            fallas.append(codigo)

    print("\n4/4  Combinando y guardando")
    salida_paises = {}
    for iso3, info in paises.items():
        series, origen = armar_pais(iso3, descargas)
        if series:
            salida_paises[iso3] = {**info, "series": series, "origen": origen}

    mundo = {}
    for indicador in INDICADORES_MUNDO:
        serie = descargas["bm"].get(indicador, {}).get("WLD", {})
        if serie:
            mundo[indicador] = compactar(serie)

    # Si faltó algún dato del FMI y ya existe un datos.json completo, no lo
    # pisamos con uno peor: preferimos dejar los datos de la semana pasada.
    if fallas and ARCHIVO_SALIDA.exists():
        sys.exit(f"❌ Faltaron datos del FMI ({', '.join(fallas)}). "
                 "Dejo el datos.json anterior sin tocar.")

    if len(salida_paises) < MINIMO_PAISES:
        sys.exit(f"❌ Solo {len(salida_paises)} países con datos (mínimo {MINIMO_PAISES}). "
                 "No modifico datos.json.")

    datos = {
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "anios": [ANIO_INICIO, ANIO_FIN],
        "fuentes": {
            "bm":  {"nombre": "Banco Mundial", "url": "https://data.worldbank.org",
                    "codigos": {k: v[0] for k, v in INDICADORES_BM.items()}},
            "fmi": {"nombre": "FMI (World Economic Outlook)", "url": "https://www.imf.org/external/datamapper",
                    "codigos": INDICADORES_FMI},
        },
        "mundo": mundo,
        "paises": dict(sorted(salida_paises.items())),
    }

    # Escribimos a un archivo temporal y después lo renombramos:
    # así nunca queda un datos.json a medio escribir.
    temporal = ARCHIVO_SALIDA.with_suffix(".tmp")
    with open(temporal, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, ensure_ascii=False, separators=(",", ":"))
    temporal.replace(ARCHIVO_SALIDA)

    tamanio_kb = ARCHIVO_SALIDA.stat().st_size / 1024
    print(f"     ✅ {ARCHIVO_SALIDA.name}: {len(salida_paises)} países, {tamanio_kb:,.0f} KB")
    print(f"     Tardó {time.time() - inicio:.0f} s")


if __name__ == "__main__":
    main()
