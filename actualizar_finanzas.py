"""
actualizar_finanzas.py — Atlas Macro

Datos financieros de Argentina para la página "Argentina financiera":
  · BCRA (API pública de estadísticas): reservas, dólar mayorista, base monetaria,
    tasas (BADLAR, TAMAR, plazo fijo), inflación esperada (REM), depósitos y préstamos.
  · Bonos en dólares (Bonares y Globales) desde data912.com: precio y variación.
  · Actividad económica (EMAE del INDEC) desde la API de Series de Tiempo de datos.gob.ar.
Genera finanzas.json. Si una fuente falla, deja la parte anterior y sigue con las otras.

Uso:
    python3 actualizar_finanzas.py
    python3 actualizar_finanzas.py -v     # muestra qué encontró en cada fuente
"""

import json
import sys
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from curl_cffi import requests

CARPETA = Path(__file__).parent
ARCHIVO_SALIDA = CARPETA / "finanzas.json"
VER = "-v" in sys.argv
sesion = requests.Session(impersonate="chrome")

# El BCRA cambia de versión cada tanto (la 3.0 ya no anda): probamos de la más nueva a la más vieja
BCRA_VERSIONES = ["v4.0", "v3.0"]
BCRA_LISTA = "https://api.bcra.gob.ar/estadisticas/{v}/Monetarias"
BCRA_SERIE = "https://api.bcra.gob.ar/estadisticas/{v}/Monetarias/{id}"
# clave → (nombre en la página, unidad, número de variable en la API del BCRA, v4.0).
# Los números son fijos; las descripciones del BCRA ahora vienen en inglés, por eso no buscamos por nombre.
BCRA_VARIABLES = {
    # Banco Central y dinero
    "reservas":     ("Reservas del BCRA", "millones de US$", 1),
    "mayorista":    ("Dólar mayorista", "$ por US$", 5),
    "minorista":    ("Dólar minorista (promedio de bancos)", "$ por US$", 4),
    "base":         ("Base monetaria", "millones de $", 15),
    "m2var":        ("Dinero en la economía (M2 privado), variación anual", "%", 25),
    # Tasas
    "badlar":       ("Tasa BADLAR (bancos privados)", "% anual", 7),
    "tamar":        ("Tasa TAMAR (bancos privados)", "% anual", 44),
    "pf30":         ("Tasa de plazo fijo a 30 días", "% anual", 12),
    "baibar":       ("Tasa entre bancos (BAIBAR)", "% anual", 11),
    "t_adelantos":  ("Tasa de adelantos en cuenta corriente", "% anual", 13),
    "t_personales": ("Tasa de préstamos personales", "% anual", 14),
    "rem":          ("Inflación esperada para los próximos 12 meses (REM)", "%", 29),
    # Bancos
    "depositos":    ("Depósitos en pesos del sector privado", "millones de $", 100),
    "dep_usd":      ("Depósitos en dólares del sector privado", "millones de US$", 108),
    "prestamos":    ("Préstamos en pesos al sector privado", "millones de $", 117),
    "hipotecarios": ("Préstamos hipotecarios", "millones de $", 112),
    "prendarios":   ("Préstamos prendarios (autos)", "millones de $", 113),
    "personales":   ("Préstamos personales", "millones de $", 114),
    "tarjetas":     ("Deuda con tarjetas de crédito", "millones de $", 115),
    # Índices de ajuste
    "cer":          ("CER", "índice (2/2/2002 = 1)", 30),
    "uva":          ("UVA", "$ por UVA", 31),
}

BONOS_URL = "https://data912.com/live/arg_bonds"
BONOS_HIST = "https://data912.com/historical/bonds/{t}"
BONOS = ["AL29D", "AL30D", "AL35D", "AE38D", "AL41D", "GD29D", "GD30D", "GD35D", "GD38D", "GD41D", "GD46D"]

SERIES_URL = "https://apis.datos.gob.ar/series/api/series/"


def bajar(url, **kw):
    """JSON de una dirección. El BCRA suele tener mal armado su certificado: si falla, reintenta sin verificarlo."""
    try:
        r = sesion.get(url, timeout=60, **kw)
    except Exception as e:
        if "api.bcra.gob.ar" not in url:
            raise
        if VER:
            print(f"        (certificado del BCRA: reintento sin verificar · {str(e)[:60]})")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            r = sesion.get(url, timeout=60, verify=False, **kw)
    r.raise_for_status()
    return r.json()


def parte_bcra():
    print("· BCRA")
    lista, version, error = None, None, None
    for version in BCRA_VERSIONES:
        try:
            lista = bajar(BCRA_LISTA.format(v=version), params={"limit": 3000})["results"]
            break
        except Exception as e:
            error = e
            if VER:
                print(f"        {version}: {str(e)[:80]}")
    if lista is None:
        raise error
    print(f"     (API {version}, {len(lista)} variables)")
    if VER:
        for v in lista:
            print(f"        {v['idVariable']:>4}  {v['descripcion'][:110]}")
    salida = {}
    desde = (date.today() - timedelta(days=365 * 5)).isoformat()
    por_id = {v["idVariable"]: v for v in lista}
    for clave, (nombre, unidad, idv) in BCRA_VARIABLES.items():
        var = por_id.get(idv)
        if not var:
            print(f"     ⚠️  {nombre}: el BCRA ya no tiene la variable {idv}")
            continue
        try:
            res = bajar(BCRA_SERIE.format(v=version, id=var["idVariable"]), params={"desde": desde, "hasta": date.today().isoformat(), "limit": 3000})["results"]
            # la v4 devuelve [{"idVariable":..., "detalle": [{fecha, valor}, ...]}]; la v3, la lista directa
            filas = [d for r in res for d in r["detalle"]] if res and isinstance(res[0], dict) and "detalle" in res[0] else res
        except Exception as e:
            print(f"     ⚠️  {nombre}: no bajó la serie ({str(e)[:60]})")
            continue
        filas = sorted(filas, key=lambda f: f["fecha"])
        salida[clave] = {"nombre": nombre, "unidad": unidad, "id": var["idVariable"], "desc_bcra": var["descripcion"],
                         "serie": [[f["fecha"], f["valor"]] for f in filas]}
        u = filas[-1] if filas else None
        print(f"     · {nombre}: {u['valor'] if u else '—'} ({u['fecha'] if u else ''}) · id {var['idVariable']}")
    return salida


def parte_bonos():
    print("· Bonos")
    vivos = {b.get("symbol"): b for b in bajar(BONOS_URL)}
    salida = []
    for t in BONOS:
        b = vivos.get(t)
        if not b:
            print(f"     ⚠️  {t}: no está en data912")
            continue
        try:
            hist = bajar(BONOS_HIST.format(t=t))
            serie = [[h["date"], h["c"]] for h in hist if h.get("c")][-260:]       # último año
        except Exception:
            serie = []
        salida.append({"ticker": t[:-1], "precio": b.get("c"), "var": b.get("pct_change"),
                       "ley": "Nueva York" if t.startswith("GD") else "Argentina", "serie": serie})
        print(f"     · {t[:-1]}: US$ {b.get('c')} ({b.get('pct_change')}%)")
    return salida


EMAE = {"original": "143.3_NO_PR_2004_A_21", "desest": "143.3_NO_PR_2004_A_31"}   # índice base 2004 = 100


def parte_emae():
    print("· Actividad (EMAE)")
    salida = {}
    for clave, sid in EMAE.items():
        datos = bajar(SERIES_URL, params={"ids": sid, "format": "json", "limit": 5000, "start_date": "2004-01-01"})["data"]
        salida[clave] = [[f[0][:7], round(f[1], 2)] for f in datos if f[1] is not None]
        print(f"     · EMAE {clave}: {len(salida[clave])} meses, último {salida[clave][-1] if salida[clave] else '—'}")
    return salida


def main():
    print("Atlas Macro · Argentina financiera")
    anterior = json.load(open(ARCHIVO_SALIDA, encoding="utf-8")) if ARCHIVO_SALIDA.exists() else {}
    salida = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "fuente": "BCRA (API de estadísticas), data912.com (bonos) e INDEC vía datos.gob.ar (EMAE)"}
    ok = 0
    for clave, funcion in (("bcra", parte_bcra), ("bonos", parte_bonos), ("emae", parte_emae)):
        try:
            salida[clave] = funcion()
            ok += bool(salida[clave])
        except Exception as e:
            print(f"     ❌ {clave}: {str(e)[:120]} (dejo el dato anterior)")
            if clave in anterior:
                salida[clave] = anterior[clave]
    if ok == 0:
        sys.exit("❌ No respondió ninguna fuente: dejo el finanzas.json anterior")
    with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as archivo:
        json.dump(salida, archivo, ensure_ascii=False, separators=(",", ":"))
    print(f"✅ finanzas.json listo · {ARCHIVO_SALIDA.stat().st_size / 1024:,.0f} KB")


if __name__ == "__main__":
    main()
