"""
actualizar_series_ar.py — Atlas Macro

Arma series_ar.json: un catálogo de series MENSUALES de Argentina, todas con el mismo
formato, para la página "Actividad, precios y empleo" y para el Comparador de series.

Fuentes:
  · BCRA (API de estadísticas): reservas, compras de dólares, dólar, dinero, tasas,
    depósitos y préstamos (las diarias se pasan a mensuales: promedio, último dato o suma).
  · API de Series de Tiempo de datos.gob.ar: INDEC (EMAE por sector, IPC por categorías,
    comercio exterior) y Ministerio de Trabajo (empleo registrado SIPA, salarios).

Uso:
    python3 actualizar_series_ar.py
    python3 actualizar_series_ar.py -v
"""

import csv
import io
import json
import sys
import time
import warnings
from datetime import date, datetime, timezone
from pathlib import Path

from curl_cffi import requests

CARPETA = Path(__file__).parent
ARCHIVO_SALIDA = CARPETA / "series_ar.json"
VER = "-v" in sys.argv
sesion = requests.Session(impersonate="chrome")
DESDE = "2016-01-01"

BCRA_SERIE = "https://api.bcra.gob.ar/estadisticas/v4.0/Monetarias/{id}"
SERIES_URL = "https://apis.datos.gob.ar/series/api/series/"
SERIES_BUSCAR = "https://apis.datos.gob.ar/series/api/search/"

# clave → (nombre, unidad, grupo, fuente, id, cómo pasar a mensual)
#   fuente "bcra": id numérico; "ts": id de datos.gob.ar; "buscar": [texto de búsqueda, palabras que tiene que tener]
#   mensual: "prom" (promedio del mes), "ult" (último dato del mes), "suma" (suma del mes)
CATALOGO = {
    # --- Banco Central y dinero
    "reservas":     ("Reservas del BCRA", "millones de US$", "Banco Central y dinero", "bcra", 1, "ult"),
    "compras":      ("Compras de dólares del BCRA", "millones de US$ en el mes", "Banco Central y dinero", "bcra", 78, "suma"),
    "mayorista":    ("Dólar mayorista", "$ por US$ (promedio del mes)", "Banco Central y dinero", "bcra", 5, "prom"),
    "base":         ("Base monetaria", "millones de $", "Banco Central y dinero", "bcra", 15, "ult"),
    "m2priv_ia":    ("M2 privado, variación anual", "%", "Banco Central y dinero", "bcra", 25, "prom"),
    "m3":           ("M3 (todo el dinero en pesos)", "millones de $", "Banco Central y dinero", "bcra", 1234, "ult"),
    "pases":        ("Pases pasivos del BCRA (stock)", "millones de $", "Banco Central y dinero", "bcra", 152, "ult"),
    "encajes":      ("Cuentas corrientes de los bancos en el BCRA (encajes)", "millones de $", "Banco Central y dinero", "bcra", 19, "ult"),
    # --- Tasas
    "badlar":       ("Tasa BADLAR", "% anual", "Tasas de interés", "bcra", 7, "prom"),
    "tamar":        ("Tasa TAMAR", "% anual", "Tasas de interés", "bcra", 44, "prom"),
    "pf30":         ("Tasa de plazo fijo a 30 días", "% anual", "Tasas de interés", "bcra", 12, "prom"),
    "call":         ("Tasa call entre bancos (BAIBAR)", "% anual", "Tasas de interés", "bcra", 11, "prom"),
    "repo":         ("Tasa de repo entre terceros", "% anual", "Tasas de interés", "bcra", 150, "prom"),
    "t_personales": ("Tasa de préstamos personales", "% anual", "Tasas de interés", "bcra", 14, "prom"),
    "rem":          ("Inflación esperada a 12 meses (REM)", "%", "Precios", "bcra", 29, "ult"),
    # --- Bancos
    "dep_pesos":    ("Depósitos en pesos del sector privado", "millones de $", "Depósitos y préstamos", "bcra", 100, "ult"),
    "dep_usd":      ("Depósitos en dólares del sector privado", "millones de US$", "Depósitos y préstamos", "bcra", 108, "ult"),
    "prest_pesos":  ("Préstamos en pesos al sector privado", "millones de $", "Depósitos y préstamos", "bcra", 117, "ult"),
    "prest_usd":    ("Préstamos en dólares al sector privado", "millones de US$", "Depósitos y préstamos", "bcra", 125, "ult"),
    # --- Precios
    "inflacion":    ("Inflación mensual (IPC)", "% mensual", "Precios", "bcra", 27, "ult"),
    "inflacion_ia": ("Inflación interanual (IPC)", "%", "Precios", "bcra", 28, "ult"),
    "ipc_nucleo":   ("IPC núcleo (índice)", "índice dic 2016 = 100", "Precios", "ts", "148.3_INUCLEONAL_DICI_M_19", "ult"),
    "ipc_regulados":("IPC regulados (índice)", "índice dic 2016 = 100", "Precios", "ts", "148.3_IREGULANAL_DICI_M_22", "ult"),
    "ipc_general":  ("IPC nivel general (índice)", "índice dic 2016 = 100", "Precios", "buscar", ["IPC Nivel general Nacional Base dic 2016 Mensual", ["nivel general", "nacional", "mensual"], "148.3_INIVELNAL_DICI_M_26"], "ult"),
    "ipc_estac":    ("IPC estacionales (índice)", "índice dic 2016 = 100", "Precios", "buscar", ["IPC Estacionales Nacional Base dic 2016 Mensual", ["estacional", "nacional", "mensual"], "148.3_IESTACNAL_DICI_M_25"], "ult"),
    "salarios":     ("Índice de salarios (registrados)", "índice oct 2016 = 100", "Precios", "ts", "149.1_TL_REGIADO_OCTU_0_16", "ult"),
    # --- Actividad
    "emae":         ("EMAE desestacionalizado", "índice 2004 = 100", "Actividad", "ts", "143.3_NO_PR_2004_A_31", "ult"),
    "emae_orig":    ("EMAE original", "índice 2004 = 100", "Actividad", "ts", "143.3_NO_PR_2004_A_21", "ult"),
    "emae_constr":  ("Actividad de la construcción", "índice 2004 = 100", "Actividad", "ts", "11.3_VMATC_2004_M_12", "ult"),
    "emae_ind":     ("Actividad de la industria", "índice 2004 = 100", "Actividad", "ts", "11.3_VMASD_2004_M_23", "ult"),
    "emae_agro":    ("Actividad del agro", "índice 2004 = 100", "Actividad", "ts", "11.3_ISOM_2004_M_39", "ult"),
    "emae_mineria": ("Actividad de minería, petróleo y gas", "índice 2004 = 100", "Actividad", "ts", "11.3_ISD_2004_M_26", "ult"),
    "emae_comercio":("Actividad del comercio", "índice 2004 = 100", "Actividad", "ts", "11.3_AGCS_2004_M_41", "ult"),
    # --- Comercio exterior
    "expo":         ("Exportaciones", "millones de US$", "Comercio exterior", "ts", "74.3_IET_0_M_16", "ult"),
    "saldo_com":    ("Saldo comercial", "millones de US$", "Comercio exterior", "ts", "74.3_ISC_0_M_19", "ult"),
    "expo_energia": ("Exportaciones de combustibles y energía", "millones de US$", "Comercio exterior", "ts", "74.3_IECE_0_M_35", "ult"),
    "impo_comb":    ("Importaciones de combustibles", "millones de US$", "Comercio exterior", "ts", "74.3_IICL_0_M_42", "ult"),
    # --- Empleo registrado (SIPA, sin estacionalidad)
    "empleo_total": ("Trabajadores registrados (total)", "miles de personas", "Empleo", "ts", "151.1_TL_SIN_TAC_2012_M_15", "ult"),
    "empleo_priv":  ("Asalariados privados registrados", "miles de personas", "Empleo", "ts", "151.1_AARIADOTAC_2012_M_26", "ult"),
    "empleo_pub":   ("Asalariados públicos", "miles de personas", "Empleo", "ts", "151.1_AARIADOTAC_2012_M_25", "ult"),
    "empleo_casas": ("Casas particulares registradas", "miles de personas", "Empleo", "ts", "151.1_AARIADOTAC_2012_M_40", "ult"),
    "empleo_auto":  ("Autónomos", "miles de personas", "Empleo", "ts", "151.1_IPENDIETAC_2012_M_34", "ult"),
    "empleo_mono":  ("Monotributistas", "miles de personas", "Empleo", "buscar", ["Independientes monotributo Sin estacionalidad", ["monotributo", "sin estacionalidad"]], "ult"),
    "salario_priv": ("Salario promedio privado registrado", "$ por mes", "Empleo", "ts", "153.1_RNERACIDIO_2009_M_21", "ult"),
}

# Series que se calculan a partir de otras
DERIVADAS = {
    "impo":          ("Importaciones", "millones de US$", "Comercio exterior", lambda S, m: S["expo"].get(m) - S["saldo_com"].get(m) if m in S["expo"] and m in S["saldo_com"] else None),
    "saldo_energia": ("Saldo comercial de energía", "millones de US$", "Comercio exterior", lambda S, m: S["expo_energia"].get(m) - S["impo_comb"].get(m) if m in S["expo_energia"] and m in S["impo_comb"] else None),
    "ratio_usd":     ("Préstamos / depósitos en dólares", "%", "Depósitos y préstamos", lambda S, m: S["prest_usd"][m] / S["dep_usd"][m] * 100 if S["dep_usd"].get(m) and m in S["prest_usd"] else None),
}


def bajar(url, **kw):
    """JSON con reintentos: la API de datos.gob.ar corta si se le pide mucho seguido."""
    error = None
    for intento in range(4):
        try:
            try:
                r = sesion.get(url, timeout=90, **kw)
            except Exception:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    r = sesion.get(url, timeout=90, verify=False, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"código {r.status_code}")
            r.raise_for_status()
            time.sleep(0.4)
            return r.json()
        except Exception as e:
            error = e
            if VER:
                print(f"        (reintento {intento + 1}: {str(e)[:60]})")
            time.sleep(3 * (intento + 1))
    raise error


def a_mensual(filas, modo):
    """[[fecha, valor], ...] diaria → {'AAAA-MM': valor}"""
    meses = {}
    for f, v in sorted(filas):
        if v is not None:
            meses.setdefault(f[:7], []).append(v)
    if modo == "suma":
        return {m: round(sum(vs), 2) for m, vs in meses.items()}
    if modo == "prom":
        return {m: round(sum(vs) / len(vs), 4) for m, vs in meses.items()}
    return {m: vs[-1] for m, vs in meses.items()}


def de_bcra(idv, modo):
    hoy = date.today()
    filas = []
    # la API devuelve hasta 3000 filas por pedido: pedimos de a tramos de 5 años
    for desde_anio in range(2016, hoy.year + 1, 5):
        res = bajar(BCRA_SERIE.format(id=idv), params={"desde": f"{desde_anio}-01-01", "hasta": f"{min(desde_anio + 4, hoy.year)}-12-31", "limit": 3000})["results"]
        filas += [[d["fecha"], d["valor"]] for r in res for d in r.get("detalle", [])] if res and "detalle" in res[0] else [[d["fecha"], d["valor"]] for d in res]
    return a_mensual(filas, modo)


def resolver(busqueda):
    """Busca el id de una serie por texto; si no aparece, prueba los ids de respaldo que vienen al final."""
    texto, palabras = busqueda[:2]
    res = bajar(SERIES_BUSCAR, params={"q": texto, "limit": 40})["data"]
    for s in res:
        d = (s["field"].get("description") or "").lower()
        if all(p in d for p in palabras) and s["field"].get("frequency") == "R/P1M":
            if VER:
                print(f"        encontrada: {s['field']['id']} · {s['field'].get('description')}")
            return s["field"]["id"]
    for sid in busqueda[2:]:
        try:
            if de_ts(sid):
                if VER:
                    print(f"        uso el id de respaldo {sid}")
                return sid
        except Exception:
            pass
    raise RuntimeError(f"no encontré «{texto}»")


CSV_CACHE = {}


def csv_de(url):
    """Filas del CSV original de datos.gob.ar (se baja una sola vez aunque lo usen varias series)."""
    if url not in CSV_CACHE:
        r = sesion.get(url, timeout=120)
        r.raise_for_status()
        CSV_CACHE[url] = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig", errors="replace"))))
        time.sleep(0.4)
    return CSV_CACHE[url]


def de_ts(sid):
    """Serie de datos.gob.ar. La API a veces viene vacía o atrasada respecto del CSV original
    del que sale la serie: usamos el CSV (la API dice cuál es y en qué columna está) y,
    si no se puede, lo que dio la API."""
    j = bajar(SERIES_URL, params={"ids": sid, "format": "json", "limit": 5000, "start_date": DESDE, "metadata": "full"})
    api = {f[0][:7]: f[1] for f in j["data"] if f[1] is not None}
    meta = (j.get("meta") or [{}, {}])[1]
    url, columna = meta.get("distribution", {}).get("downloadURL"), meta.get("field", {}).get("title")
    if url and columna:
        try:
            desde_csv = {}
            for f in csv_de(url):
                fecha, v = f.get("indice_tiempo"), f.get(columna)
                if fecha and v not in (None, "", "NA"):
                    try:
                        desde_csv[fecha[:7]] = float(v)
                    except ValueError:
                        pass
            if desde_csv and (not api or max(desde_csv) >= max(api)):
                if VER and (not api or max(desde_csv) > max(api)):
                    print(f"        (uso el CSV de {sid}: llega a {max(desde_csv)}{', la API a ' + max(api) if api else ', la API vino vacía'})")
                return desde_csv
        except Exception as e:
            if VER:
                print(f"        (no pude leer el CSV de {sid}: {str(e)[:60]})")
    return api


def main():
    print("Atlas Macro · series mensuales de Argentina")
    anterior = json.load(open(ARCHIVO_SALIDA, encoding="utf-8")) if ARCHIVO_SALIDA.exists() else {"series": {}}
    S, salida = {}, {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "fuente": "BCRA (API de estadísticas) y datos.gob.ar (INDEC, Ministerio de Trabajo)", "series": {}}
    ok = mal = 0
    for clave, (nombre, unidad, grupo, fuente, idv, modo) in CATALOGO.items():
        try:
            if fuente == "bcra":
                m = de_bcra(idv, modo)
            else:
                m = de_ts(resolver(idv) if fuente == "buscar" else idv)
            m = {k: v for k, v in m.items() if k >= DESDE[:7]}
            if not m:
                raise RuntimeError("vino vacía")
            S[clave] = m
            salida["series"][clave] = {"nombre": nombre, "unidad": unidad, "grupo": grupo,
                                       "fuente": "BCRA" if fuente == "bcra" else "INDEC / Ministerio de Trabajo",
                                       "serie": [[k, round(v, 4)] for k, v in sorted(m.items())]}
            ok += 1
            u = max(m)
            print(f"     · {nombre}: {len(m)} meses, último {u} = {m[u]:,.2f}")
        except Exception as e:
            mal += 1
            print(f"     ⚠️  {nombre}: {str(e)[:90]}{' (dejo el dato anterior)' if clave in anterior['series'] else ''}")
            if clave in anterior["series"]:
                salida["series"][clave] = anterior["series"][clave]
                S[clave] = dict(anterior["series"][clave]["serie"])
    for clave, (nombre, unidad, grupo, f) in DERIVADAS.items():
        try:
            meses = sorted(set().union(*[set(S.get(k, {})) for k in S]))
            serie = [[m, round(v, 4)] for m in meses if (v := f(S, m)) is not None]
            if serie:
                salida["series"][clave] = {"nombre": nombre, "unidad": unidad, "grupo": grupo, "fuente": "Cálculo propio", "serie": serie}
                print(f"     · {nombre} (calculada): {len(serie)} meses")
        except Exception as e:
            print(f"     ⚠️  {nombre}: {str(e)[:90]}")
    if ok == 0:
        sys.exit("❌ No llegó ninguna serie: dejo el series_ar.json anterior")
    with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as archivo:
        json.dump(salida, archivo, ensure_ascii=False, separators=(",", ":"))
    print(f"✅ series_ar.json listo · {len(salida['series'])} series ({mal} con problemas) · {ARCHIVO_SALIDA.stat().st_size / 1024:,.0f} KB")


if __name__ == "__main__":
    main()
