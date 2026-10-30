"""
actualizar_bcra.py — Atlas Macro

Datos del Banco Central para la página "BCRA":
  · Compras y ventas de dólares del BCRA, día por día (en millones de US$).
  · Factores de explicación de la base monetaria: por qué se emitieron o absorbieron
    pesos cada día (compras de dólares, operaciones con el Tesoro, pases, letras, etc.).
  · Reservas día por día y su variación.
Además baja los precios de las letras del Tesoro en pesos (Lecap/Boncap) de data912.com
para la curva de tasas en pesos.

Genera bcra.json. Si una parte falla, deja la anterior.

Uso:
    python3 actualizar_bcra.py
    python3 actualizar_bcra.py -v     # muestra qué variables encontró
"""

import json
import re
import sys
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from curl_cffi import requests

CARPETA = Path(__file__).parent
ARCHIVO_SALIDA = CARPETA / "bcra.json"
VER = "-v" in sys.argv
sesion = requests.Session(impersonate="chrome")

BCRA_LISTA = "https://api.bcra.gob.ar/estadisticas/v4.0/Monetarias"
BCRA_SERIE = "https://api.bcra.gob.ar/estadisticas/v4.0/Monetarias/{id}"
RESERVAS, MAYORISTA = 1, 5
DESDE = (date.today() - timedelta(days=365 * 3)).isoformat()      # últimos 3 años

LETRAS_URL = "https://data912.com/live/arg_notes"
MESES_LETRA = {"E": 1, "F": 2, "M": 3, "A": 4, "Y": 5, "J": 6, "L": 7, "G": 8, "S": 9, "O": 10, "N": 11, "D": 12}


def bajar(url, **kw):
    """JSON; si el certificado del BCRA falla, reintenta sin verificarlo (son datos públicos)."""
    try:
        r = sesion.get(url, timeout=60, **kw)
    except Exception:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            r = sesion.get(url, timeout=60, verify=False, **kw)
    r.raise_for_status()
    return r.json()


def serie(idv):
    """[[fecha, valor], ...] de una variable del BCRA, de los últimos 3 años."""
    res = bajar(BCRA_SERIE.format(id=idv), params={"desde": DESDE, "hasta": date.today().isoformat(), "limit": 3000})["results"]
    filas = [d for r in res for d in r["detalle"]] if res and "detalle" in res[0] else res
    return sorted([[f["fecha"], f["valor"]] for f in filas if f.get("valor") is not None])


# Nombres en castellano de cada factor (por número de variable del BCRA, v4.0)
NOMBRES = {
    46: "Variación total de la base", 47: "Compra de dólares (al sector privado)", 48: "Compra de dólares al Tesoro",
    50: "Utilidades transferidas al Tesoro", 51: "Otras operaciones con el Tesoro", 52: "Pases", 53: "LELIQ y NOTALIQ",
    54: "Redescuentos y adelantos", 55: "Intereses y remuneración de cuentas", 56: "LEBAC y NOBAC",
    57: "Rescate de cuasimonedas", 58: "LEFI", 59: "Otros",
    78: "Compras de dólares (efecto en las reservas)",
}


def parte_factores(lista):
    print("· Factores de explicación de la base monetaria")
    factores = [v for v in lista if "explanatory factors" in v["descripcion"].lower()]
    if VER:
        for v in factores:
            print(f"        {v['idVariable']:>4}  {v['descripcion'][:120]}")
    salida = {}
    for v in factores:
        try:
            s = serie(v["idVariable"])
        except Exception as e:
            print(f"     ⚠️  {v['idVariable']}: {str(e)[:60]}")
            continue
        if s:
            salida[str(v["idVariable"])] = {"nombre": NOMBRES.get(v["idVariable"], v["descripcion"]), "desc_bcra": v["descripcion"],
                                            "unidad": "millones de US$" if "reserves" in v["descripcion"].lower() else "millones de $", "serie": s}
    print(f"     · {len(salida)} factores")
    return salida


def parte_compras(factores):
    """Compras netas de dólares del BCRA en millones de US$ (variable 78: efecto de las compras en las reservas)."""
    print("· Compras de dólares del BCRA")
    if "78" not in factores:
        raise RuntimeError("no encontré la variable 78 (compras de divisas, en dólares)")
    out = [[f, round(v, 1)] for f, v in factores.pop("78")["serie"]]
    total_anio = sum(v for f, v in out if f.startswith(str(date.today().year)))
    print(f"     · {len(out)} días · en {date.today().year}: US$ {total_anio:,.0f} millones · último {out[-1] if out else '—'}")
    return out


def parte_reservas():
    print("· Reservas día por día")
    s = serie(RESERVAS)
    print(f"     · {len(s)} días · último {s[-1] if s else '—'}")
    return s


def vencimiento(ticker):
    """'S31O5' → '2025-10-31'; 'T15E7' → '2027-01-15' (Lecap empiezan con S, Boncap con T)."""
    m = re.fullmatch(r"[ST](\d{2})([EFMAYJLGSOND])(\d)", ticker)
    if not m:
        return None
    dia, mes, anio = int(m.group(1)), MESES_LETRA[m.group(2)], 2020 + int(m.group(3))
    try:
        return date(anio, mes, dia).isoformat()
    except ValueError:
        return None


def parte_letras():
    print("· Letras del Tesoro en pesos (data912)")
    datos = bajar(LETRAS_URL)
    if VER and datos:
        print(f"        ejemplo de lo que devuelve data912: {datos[0]}")
    out = []
    for x in datos:
        t = x.get("symbol", "")
        v = vencimiento(t)
        if v and v > date.today().isoformat() and x.get("c"):
            out.append({"ticker": t, "vence": v, "precio": x["c"], "var": x.get("pct_change"), "volumen": x.get("v")})
    out.sort(key=lambda x: x["vence"])
    print(f"     · {len(out)} letras vigentes: " + ", ".join(f"{x['ticker']} ${x['precio']}" for x in out[:6]))
    return out


def main():
    print("Atlas Macro · BCRA")
    anterior = json.load(open(ARCHIVO_SALIDA, encoding="utf-8")) if ARCHIVO_SALIDA.exists() else {}
    salida = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "fuente": "BCRA (API de estadísticas) y data912.com (letras del Tesoro)"}
    ok = 0
    try:
        lista = bajar(BCRA_LISTA, params={"limit": 3000})["results"]
        salida["factores"] = parte_factores(lista)
        ok += 1
        try:
            salida["compras"] = parte_compras(salida["factores"])
            ok += 1
        except Exception as e:
            print(f"     ❌ compras: {str(e)[:120]}")
        salida["reservas"] = parte_reservas()
        ok += 1
    except Exception as e:
        print(f"     ❌ BCRA: {str(e)[:120]} (dejo el dato anterior)")
    try:
        salida["letras"] = parte_letras()
        ok += 1
    except Exception as e:
        print(f"     ❌ letras: {str(e)[:120]} (dejo el dato anterior)")
    for k in ("factores", "compras", "reservas", "letras"):
        if k not in salida and k in anterior:
            salida[k] = anterior[k]
    if ok == 0:
        sys.exit("❌ No respondió ninguna fuente: dejo el bcra.json anterior")
    with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as archivo:
        json.dump(salida, archivo, ensure_ascii=False, separators=(",", ":"))
    print(f"✅ bcra.json listo · {ARCHIVO_SALIDA.stat().st_size / 1024:,.0f} KB")


if __name__ == "__main__":
    main()
