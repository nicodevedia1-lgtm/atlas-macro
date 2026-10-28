"""
actualizar_mercados.py — Atlas Macro

Baja el precio de cierre de índices bursátiles, acciones argentinas en Wall Street
(ADRs), materias primas, cripto y monedas del mundo, y genera mercados.json con el último año de datos.

Fuentes: Stooq (stooq.com) y, si Stooq no tiene el dato o no responde, Yahoo Finance.
Los precios son de cierre (o con demora): no es una cotización en tiempo real.

Uso:
    python3 actualizar_mercados.py
"""

import csv
import io
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from curl_cffi import requests

CARPETA = Path(__file__).parent
ARCHIVO_SALIDA = CARPETA / "mercados.json"

URL_STOOQ = "https://stooq.com/q/d/l/?s={ticker}&i=d&d1={desde}&d2={hasta}"
URL_YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=2y&interval=1d"

# grupo → [(clave, nombre, ticker en Stooq, ticker en Yahoo, moneda, unidad)]
INSTRUMENTOS = {
    "indices": [
        ("sp500", "S&P 500", "^spx", "^GSPC", "USD", "puntos"),
        ("nasdaq", "Nasdaq", "^ndq", "^IXIC", "USD", "puntos"),
        ("merval", "Merval", "^mrv", "^MERV", "ARS", "puntos"),
        ("bovespa", "Bovespa", "^bvp", "^BVSP", "BRL", "puntos"),
        ("eurostoxx", "Euro Stoxx 50", "^stx", "^STOXX50E", "EUR", "puntos"),
        ("nikkei", "Nikkei 225", "^nkx", "^N225", "JPY", "puntos"),
    ],
    "adrs": [
        ("ypf", "YPF", "ypf.us", "YPF", "USD", "US$"),
        ("ggal", "Grupo Galicia", "ggal.us", "GGAL", "USD", "US$"),
        ("meli", "Mercado Libre", "meli.us", "MELI", "USD", "US$"),
        ("pam", "Pampa Energía", "pam.us", "PAM", "USD", "US$"),
        ("vist", "Vista Energy", "vist.us", "VIST", "USD", "US$"),
        ("bma", "Banco Macro", "bma.us", "BMA", "USD", "US$"),
    ],
    "materias": [
        ("soja", "Soja", "zs.f", "ZS=F", "USD", "US$ por tonelada"),
        ("maiz", "Maíz", "zc.f", "ZC=F", "USD", "US$ por tonelada"),
        ("trigo", "Trigo", "zw.f", "ZW=F", "USD", "US$ por tonelada"),
        ("brent", "Petróleo Brent", "cb.f", "BZ=F", "USD", "US$ por barril"),
        ("oro", "Oro", "gc.f", "GC=F", "USD", "US$ por onza"),
        ("cobre", "Cobre", "hg.f", "HG=F", "USD", "US$ por libra"),
    ],
    "cripto": [
        ("btc", "Bitcoin", "btcusd", "BTC-USD", "USD", "US$"),
        ("eth", "Ether", "ethusd", "ETH-USD", "USD", "US$"),
    ],
    # unidad "moneda:X" = cuántas X vale un dólar (o al revés para euro y libra, como se cotizan siempre)
    "monedas": [
        ("dxy", "Índice dólar (DXY)", "dx.f", "DX-Y.NYB", "USD", "puntos"),
        ("eur", "Euro", "eurusd", "EURUSD=X", "USD", "moneda:US$ por euro"),
        ("brl", "Real brasileño", "usdbrl", "BRL=X", "BRL", "moneda:reales por dólar"),
        ("clp", "Peso chileno", "usdclp", "CLP=X", "CLP", "moneda:pesos chilenos por dólar"),
        ("mxn", "Peso mexicano", "usdmxn", "MXN=X", "MXN", "moneda:pesos mexicanos por dólar"),
        ("cop", "Peso colombiano", "usdcop", "COP=X", "COP", "moneda:pesos colombianos por dólar"),
        ("jpy", "Yen", "usdjpy", "JPY=X", "JPY", "moneda:yenes por dólar"),
        ("cny", "Yuan", "usdcny", "CNY=X", "CNY", "moneda:yuanes por dólar"),
        ("gbp", "Libra esterlina", "gbpusd", "GBPUSD=X", "USD", "moneda:US$ por libra"),
    ],
}

# Los granos cotizan en centavos de dólar por bushel: los pasamos a dólares por tonelada
TONELADAS_POR_BUSHEL = {"soja": 0.0272155, "trigo": 0.0272155, "maiz": 0.0254012}

sesion = requests.Session(impersonate="chrome")


def de_stooq(ticker):
    """[(fecha, cierre), ...] desde Stooq, o None si no anduvo."""
    hasta = date.today()
    desde = hasta - timedelta(days=730)
    url = URL_STOOQ.format(ticker=ticker, desde=desde.strftime("%Y%m%d"), hasta=hasta.strftime("%Y%m%d"))
    try:
        r = sesion.get(url, timeout=40)
    except Exception:
        return None
    if r.status_code != 200 or not r.text.startswith("Date"):
        return None                                  # Stooq responde "No data" o un aviso de límite
    filas = list(csv.DictReader(io.StringIO(r.text)))
    datos = [(f["Date"], float(f["Close"])) for f in filas if f.get("Close") not in (None, "", "null")]
    return datos or None


def de_yahoo(ticker):
    """[(fecha, cierre), ...] desde Yahoo Finance, o None si no anduvo."""
    try:
        r = sesion.get(URL_YAHOO.format(ticker=ticker), timeout=40)
        res = r.json()["chart"]["result"][0]
    except Exception:
        return None
    tiempos = res.get("timestamp") or []
    cierres = res["indicators"]["quote"][0].get("close") or []
    datos = [(datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), c) for t, c in zip(tiempos, cierres) if c is not None]
    return datos or None


def resumen(datos):
    """Último precio y variaciones: del día, del mes, del año y de 12 meses."""
    fechas = [f for f, _ in datos]
    ult_f, ult = datos[-1]
    anterior = datos[-2][1] if len(datos) > 1 else None

    def precio_al(dia):
        """Último cierre en o antes de esa fecha."""
        cand = [v for f, v in datos if f <= dia]
        return cand[-1] if cand else None

    fin = date.fromisoformat(ult_f)
    hace_mes = precio_al((fin - timedelta(days=30)).isoformat())
    fin_anio_pasado = precio_al(f"{fin.year - 1}-12-31")
    hace_anio = precio_al((fin - timedelta(days=365)).isoformat())
    var = lambda a: round((ult / a - 1) * 100, 2) if a else None
    # para el gráfico: último año, un punto por día hábil
    ultimo_anio = [(f, round(v, 4)) for f, v in datos if f >= (fin - timedelta(days=366)).isoformat()]
    return {
        "fecha": ult_f, "precio": round(ult, 4),
        "dia": var(anterior), "mes": var(hace_mes), "anio": var(fin_anio_pasado), "doce": var(hace_anio),
        "serie": ultimo_anio,
    }


def main():
    print("Atlas Macro · mercados")
    anterior = json.load(open(ARCHIVO_SALIDA, encoding="utf-8")) if ARCHIVO_SALIDA.exists() else {}
    salida = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "fuente": "Stooq y Yahoo Finance (precios de cierre)", "grupos": {}}
    ok = falla = 0
    for grupo, lista in INSTRUMENTOS.items():
        salida["grupos"][grupo] = []
        for clave, nombre, t_stooq, t_yahoo, moneda, unidad in lista:
            datos, fuente = de_stooq(t_stooq), "Stooq"
            if not datos:
                datos, fuente = de_yahoo(t_yahoo), "Yahoo"
            if not datos:
                falla += 1
                viejo = next((x for x in anterior.get("grupos", {}).get(grupo, []) if x["clave"] == clave), None)
                print(f"     ⚠️  {nombre}: no respondió ni Stooq ni Yahoo{' (dejo el dato anterior)' if viejo else ''}")
                if viejo:
                    salida["grupos"][grupo].append(viejo)
                continue
            if clave in TONELADAS_POR_BUSHEL and datos[-1][1] > 100:      # viene en centavos por bushel
                datos = [(f, v / 100 / TONELADAS_POR_BUSHEL[clave]) for f, v in datos]
            r = resumen(datos)
            salida["grupos"][grupo].append({"clave": clave, "nombre": nombre, "ticker": t_yahoo, "moneda": moneda,
                                             "unidad": unidad, "fuente": fuente, **r})
            ok += 1
            dia = f"{r['dia']:+.2f}%" if r["dia"] is not None else "—"
            print(f"     · {nombre:<16} {r['precio']:>14,.2f}  {dia:>8}  ({r['fecha']}, {fuente})")
            time.sleep(0.6)                                               # sin apurar a los servidores

    if ok == 0:
        sys.exit("❌ No llegó ningún precio: dejo el mercados.json anterior")
    with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as archivo:
        json.dump(salida, archivo, ensure_ascii=False, separators=(",", ":"))
    kb = ARCHIVO_SALIDA.stat().st_size / 1024
    print(f"✅ mercados.json listo · {ok} instrumentos{f' · {falla} sin datos' if falla else ''} · {kb:,.0f} KB")


if __name__ == "__main__":
    main()
