"""
actualizar_argentina.py — Atlas Macro

Baja los datos de Argentina que se actualizan todos los días y genera argentina.json:
  · Riesgo país (desde 1999)
  · Siete cotizaciones del dólar: oficial, blue, MEP, contado con liquidación,
    mayorista, cripto y tarjeta
  · Inflación mensual del INDEC (y la interanual)
  · Valor de la UVA
  · Tasa de plazo fijo a 30 días (histórica) y lo que paga hoy cada banco
  · Feriados nacionales

Fuente: ArgentinaDatos (https://argentinadatos.com), un proyecto abierto y no
oficial que junta estos datos de fuentes públicas (BCRA, INDEC, etc.).

Uso:
    python3 actualizar_argentina.py
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests

CARPETA = Path(__file__).parent
ARCHIVO_SALIDA = CARPETA / "argentina.json"

BASE = "https://raw.githubusercontent.com/enzonotario/esjs-argentina-datos-api/main/datos/v1"
URL_RIESGO = f"{BASE}/finanzas/indices/riesgo-pais/index.json"
URL_DOLAR = f"{BASE}/cotizaciones/dolares/{{casa}}/index.json"
URL_INFLACION = f"{BASE}/finanzas/indices/inflacion/index.json"
URL_INTERANUAL = f"{BASE}/finanzas/indices/inflacionInteranual/index.json"
URL_UVA = f"{BASE}/finanzas/indices/uva/index.json"
URL_TASA30 = f"{BASE}/finanzas/tasas/depositos30Dias/index.json"
URL_PLAZO_FIJO = f"{BASE}/finanzas/tasas/plazoFijo/index.json"
URL_FERIADOS = f"{BASE}/feriados/{{anio}}/index.json"

# Las cotizaciones que mostramos: código en ArgentinaDatos → nombre en la página
CASAS = {
    "oficial": "Oficial",
    "blue": "Blue",
    "bolsa": "MEP",
    "contadoconliqui": "Contado con liqui",
    "mayorista": "Mayorista",
    "cripto": "Cripto",
    "tarjeta": "Tarjeta",
}


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


def ultimo_del_mes(filas):
    """Último valor de cada mes: {"2024-03": valor del último día con dato de marzo}"""
    salida = {}
    for fila in filas:                          # vienen ordenadas por fecha
        salida[fila["fecha"][:7]] = fila["valor"]
    return salida


def valor_hace(filas, dias, campo="venta"):
    """Valor de hace N días hábiles con dato (filas ordenadas por fecha)."""
    if len(filas) > dias:
        return filas[-1 - dias][campo]
    return None


def nombre_banco(texto):
    """'BANCO CMF S.A.' → 'Banco CMF'; 'BANCO DE LA NACION ARGENTINA' → 'Banco de la Nación Argentina'"""
    chicas = {"de", "del", "la", "las", "los", "y", "e", "and", "of"}
    especiales = {"Cmf": "CMF", "Bbva": "BBVA", "Icbc": "ICBC", "Hsbc": "HSBC", "Bica": "BICA", "Uala": "Ualá", "Bind": "BIND",
                  "Voii": "VOII", "Nacion": "Nación", "Compañia": "Compañía", "Credito": "Crédito", "Cordoba": "Córdoba",
                  "Republica": "República", "Rioja": "Rioja", "Tucuman": "Tucumán", "Neuquen": "Neuquén", "Comercio": "Comercio"}
    texto = re.sub(r"\s+(S\.?\s?A\.?\s?U?\.?|SOCIEDAD AN[OÓ]NIMA)\s*$", "", texto.strip())   # saca "S.A." solo al final
    palabras = []
    for i, p in enumerate(texto.split()):
        abre = p.startswith("(")
        w = p.strip("()").lower()
        w = w if (w in chicas and i > 0) else especiales.get(w.capitalize(), w.capitalize())
        palabras.append(("(" if abre else "") + w + (")" if p.endswith(")") else ""))
    return " ".join(palabras)


def main():
    print("Atlas Macro · datos de Argentina")

    # --- Riesgo país ---
    print("· Riesgo país")
    riesgo = bajar(URL_RIESGO)
    riesgo_mes = promedio_mensual(riesgo, "valor")
    ultimo_riesgo = riesgo[-1]                  # la lista viene ordenada por fecha

    # --- Dólares ---
    casas = {}
    for codigo, nombre in CASAS.items():
        print(f"· Dólar {nombre}")
        filas = [f for f in bajar(URL_DOLAR.format(casa=codigo)) if f.get("venta")]
        mensual = promedio_mensual(filas, "venta")
        ultimo = filas[-1]
        casas[codigo] = {
            "nombre": nombre,
            "ultimo": {"fecha": ultimo["fecha"], "compra": ultimo.get("compra"), "venta": ultimo["venta"]},
            # para mostrar cuánto se movió: el día hábil anterior, hace una semana y hace un mes
            "antes": {"dia": valor_hace(filas, 1), "semana": valor_hace(filas, 5), "mes": valor_hace(filas, 21)},
            "mensual": [[mes, round(mensual[mes], 2)] for mes in sorted(mensual)],
        }

    blue, oficial = casas["blue"]["mensual"], casas["oficial"]["mensual"]
    oficial_mes = dict(oficial)
    brecha = (casas["blue"]["ultimo"]["venta"] / casas["oficial"]["ultimo"]["venta"] - 1) * 100

    # --- Inflación (INDEC, mensual) ---
    print("· Inflación")
    inflacion = bajar(URL_INFLACION)
    interanual = bajar(URL_INTERANUAL)

    # --- UVA y tasas ---
    print("· UVA y tasas de plazo fijo")
    uva = ultimo_del_mes(bajar(URL_UVA))
    tasa30 = bajar(URL_TASA30)
    # La fuente mezcla unidades: a veces 0,30 y a veces 30 para un 30% anual. Lo pasamos todo a %.
    for fila in tasa30:
        fila["valor"] = fila["valor"] if fila["valor"] >= 3 else fila["valor"] * 100
    tasa30_mes = promedio_mensual(tasa30, "valor")
    bancos = []
    for banco in bajar(URL_PLAZO_FIJO):
        tna = banco.get("tnaClientes") or banco.get("tnaNoClientes")
        if not tna:
            continue
        bancos.append({
            "entidad": nombre_banco(banco["entidad"]),
            "tna": round(tna * 100, 2),                         # en %
            "tnaNoClientes": round((banco.get("tnaNoClientes") or 0) * 100, 2) or None,
            "enlace": banco.get("enlace"),
        })
    bancos.sort(key=lambda b: -b["tna"])

    # --- Feriados de este año y del que viene (el del año que viene puede no estar todavía) ---
    print("· Feriados")
    feriados = []
    anio = datetime.now().year
    for a in (anio, anio + 1):
        respuesta = requests.get(URL_FERIADOS.format(anio=a), timeout=60)
        if respuesta.status_code == 200:
            feriados += [{"fecha": f["fecha"], "nombre": f["nombre"], "tipo": f["tipo"]} for f in respuesta.json()]

    datos = {
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fuente": "ArgentinaDatos (argentinadatos.com), proyecto abierto y no oficial",
        "riesgo_pais": {
            "ultimo": {"fecha": ultimo_riesgo["fecha"], "valor": ultimo_riesgo["valor"]},
            "mensual": [[mes, round(riesgo_mes[mes])] for mes in sorted(riesgo_mes)],
        },
        # Lo que ya usaba la ficha de Argentina (blue, oficial y brecha)
        "dolar": {
            "ultimo": {
                "fecha": casas["blue"]["ultimo"]["fecha"],
                "blue": casas["blue"]["ultimo"]["venta"],
                "oficial": casas["oficial"]["ultimo"]["venta"],
                "brecha": round(brecha, 1),
            },
            # cada fila: [mes, blue, oficial, brecha %]
            "mensual": [
                [mes, valor, round(oficial_mes[mes], 2), round((valor / oficial_mes[mes] - 1) * 100, 1)]
                for mes, valor in blue if mes in oficial_mes
            ],
        },
        "casas": casas,
        # [mes, % de suba de precios en el mes]; el dato de cada mes sale con fecha del último día
        "inflacion": {
            "mensual": [[f["fecha"][:7], f["valor"]] for f in inflacion],
            "interanual": {"mes": interanual[-1]["fecha"][:7], "valor": interanual[-1]["valor"]},
        },
        "uva": [[mes, round(uva[mes], 2)] for mes in sorted(uva)],
        "tasa30": [[mes, round(tasa30_mes[mes], 2)] for mes in sorted(tasa30_mes)],   # TNA % promedio del mes
        "plazo_fijo": bancos,
        "feriados": feriados,
    }

    with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, ensure_ascii=False, separators=(",", ":"))

    kb = ARCHIVO_SALIDA.stat().st_size / 1024
    ult_infl = inflacion[-1]
    print(f"✅ argentina.json ({kb:,.0f} KB): riesgo país {ultimo_riesgo['valor']} · "
          f"blue ${casas['blue']['ultimo']['venta']:,.0f} · oficial ${casas['oficial']['ultimo']['venta']:,.0f} · "
          f"MEP ${casas['bolsa']['ultimo']['venta']:,.0f} · inflación {ult_infl['fecha'][:7]}: {ult_infl['valor']}% · "
          f"{len(bancos)} bancos")


if __name__ == "__main__":
    main()
